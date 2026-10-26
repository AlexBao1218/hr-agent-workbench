from __future__ import annotations
import copy
import hashlib
import json
import os
import unicodedata
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from .store import Store, uid, now, version, RULES
from .policy import rank, grade_reason, POLICY_VERSION
from .agent_settings import snapshot_settings, cv_settings_hash
from .agent_workbench import install_workbench
from .diagnostics import write_diagnostic
from .candidate_deletion import delete_candidate
from .display import DisplayProjection
from .evidence import reading_order

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT/'backend'/'.env')
MAX_FILE = 20 * 1024 * 1024
MIMES = {'.pdf':'application/pdf','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}
class ApiFailure(Exception):
    def __init__(self, code, message, status=400): self.code,self.message,self.status=code,message,status
class HrReviewInput(BaseModel):
    model_config={'extra':'forbid'}
    decision: Literal['priority_interview','consider','not_now'] | None
    note: str = Field(max_length=2000,strict=True)

class TextInput(BaseModel):
    message: str = Field(min_length=1,max_length=12000)
class ParseInput(BaseModel):
    jdText: str = Field(min_length=1,max_length=80000)
class DraftInput(ParseInput):
    criteria: list[dict]
class ActorInput(BaseModel):
    actor: str = Field(min_length=1,max_length=200)
class HatInput(BaseModel): rules: list[dict]
class CompanyMappingInput(BaseModel):
    organization: str = Field(min_length=1,max_length=500)
    group: str = Field(min_length=1,max_length=200)
class CompanyGroupInput(BaseModel):
    expectedVersion: str
    actor: str = Field(min_length=1,max_length=200)
    mappings: list[CompanyMappingInput] = Field(max_length=1000)
class OrderInput(BaseModel):
    standardsVersion: str
    must: list[str]
    bonus: list[str]
class GroupMoveInput(BaseModel):
    standardsVersion: str
    criterionId: str
    targetGroup: Literal['must','bonus']
    position: int = Field(ge=0,strict=True)
    actor: str = Field(min_length=1,max_length=200)
class DemoInput(BaseModel): includeReal: bool = True
class FeedbackInput(BaseModel):
    text: str = Field(min_length=1,max_length=12000)
    pointId: str | None = None

def normalize(text): return ' '.join(text.split())
def organization_key(text): return normalize(unicodedata.normalize('NFKC',text)).casefold()
def validate_criteria(jd, criteria):
    if not criteria or len(criteria)>50: raise ApiFailure('invalid_criteria','Provide 1–50 criteria',422)
    ids=set()
    for c in criteria:
        if not isinstance(c,dict) or not all(k in c for k in ('id','title','description','group','priority','jdQuote')):
            raise ApiFailure('invalid_criteria','Incomplete criterion',422)
        if not isinstance(c['id'],str) or not c['id'] or c['id'] in ids or c['group'] not in ('must','bonus'):
            raise ApiFailure('invalid_criteria','Criteria need unique IDs and must/bonus groups',422)
        ids.add(c['id'])
        if not isinstance(c['priority'],int) or isinstance(c['priority'],bool) or c['priority']<0:
            raise ApiFailure('invalid_criteria','Priority must be a nonnegative integer',422)
        for k in ('title','description','jdQuote'):
            if not isinstance(c[k],str) or not c[k].strip(): raise ApiFailure('invalid_criteria',f'{k} must be nonempty',422)
        if normalize(c['jdQuote']) not in normalize(jd):
            raise ApiFailure('invalid_jd_quote','Every criterion must preserve an exact JD quotation',422)
    for group in ('must','bonus'):
        priorities=[c['priority'] for c in criteria if c['group']==group]
        if len(set(priorities))!=len(priorities): raise ApiFailure('invalid_criteria','Priorities must be unique within each group',422)

def same_meaning(a,b):
    def semantics(x): return sorted((c['id'],c['title'],c['description'],c['group'],c['jdQuote']) for c in x['criteria'])
    return a and b and a['jdText']==b['jdText'] and semantics(a)==semantics(b)

def create_app(data_dir=None, provider=None, parser=None, *, recover_interrupted=True, storage_config=None):
    from .provider import create_provider
    from .workflow import Workflow
    provider = provider or create_provider()
    workflow = Workflow(provider)
    from .storage import create_storage
    store, blobs = create_storage(Path(data_dir or os.environ.get('HR_DATA_DIR',ROOT/'runtime')).resolve(), storage_config)
    remote = bool(getattr(store, 'is_remote', False))
    diagnostic_reader = store.read_diagnostic if remote else None
    display_projection=DisplayProjection(store.root, diagnostic_reader)
    if hasattr(provider,'configure_usage_root'):provider.configure_usage_root(store.root)
    if remote and provider.mode == 'live' and not hasattr(provider, 'configure_usage_ledger'):
        store.close()
        raise RuntimeError('Remote live mode requires a provider with a durable usage ledger (DeepSeek).')
    if remote and hasattr(provider, 'configure_usage_ledger'):
        from .usage_ledger import PostgresUsageLedger
        provider.configure_usage_ledger(PostgresUsageLedger(store))
    @asynccontextmanager
    async def lifespan(app):
        try:yield
        finally:
            if remote:
                store.close()
                if hasattr(blobs,'close'):blobs.close()
    app = FastAPI(title='Micro Connect HR AI — local advisory MVP',lifespan=lifespan)
    app.state.store, app.state.provider, app.state.workflow = store,provider,workflow
    if remote:
        from .postgres_store import RemoteStoreError
        from .blob_store import BlobStoreError
        async def storage_failure(request, exc):
            return JSONResponse({'error':{'code':'storage_unavailable','message':'Remote storage is unavailable; no local fallback was used.'}},status_code=503)
        app.add_exception_handler(RemoteStoreError,storage_failure)
        app.add_exception_handler(BlobStoreError,storage_failure)
    app.state.blobs = blobs
    install_workbench(app,store,provider)
    workflow.settings_resolver=lambda rid: copy.deepcopy(store.read()['runs'].get(rid,{}).get('_agentSettings'))
    # Interrupted work never masquerades as completed after restart.
    if recover_interrupted:
        with store.transaction() as s:
            if 'companyGroupConfig' not in s:
                config={'version':version(1,'initial configuration'),'mappings':[]}
                s['companyGroupConfig']=config;s['companyGroupHistory']=[copy.deepcopy(config)]
            for run in s['runs'].values():
                if run['status'] in ('queued','running'):
                    run.update(status='failed',error='Service restarted before completion; retry analysis.')
                    for step in run['steps']:
                        if step['status'] in ('queued','running'):step.update(status='failed',completedAt=now(),summary='Interrupted by service restart.')
            for feedback_items in s['feedback'].values():
                for item in feedback_items:
                    if item['status']=='rechecking':item['status']='needs_review'
            for c in s['candidates'].values():
                if c['stage'] in ('parsing','analyzing'):
                    c.update(stage='needs_attention',error='Service restarted; retry analysis.')
            for c in s['candidates'].values():
                for experience in (c.get('profile') or {}).get('experiences',[]):
                    experience['companyGroup']=None
                assessment=s['assessments'].get(c.get('assessmentId'))
                if assessment and assessment['mode']!=provider.mode:
                    assessment['status']='stale'
                    c.update(stage='needs_attention',error='Model mode changed; run analysis again before using these results.')
            s['activeRunId']=None
    @app.exception_handler(ApiFailure)
    async def fail(request,exc): return JSONResponse({'error':{'code':exc.code,'message':exc.message}},status_code=exc.status)
    @app.exception_handler(RequestValidationError)
    async def invalid(request,exc): return JSONResponse({'error':{'code':'invalid_request','message':'Request body or parameters do not match the API contract.'}},status_code=422)
    def require_provider():
        if not provider.configured: raise ApiFailure('provider_not_configured','Configure OPENAI_API_KEY and OPENAI_MODEL in backend/.env, or explicitly choose HR_MODEL_MODE=fixture for local testing.',503)
    def safe_error(exc):
        # Do not echo upstream response bodies, request payloads, URLs or tokens.
        if isinstance(exc,ApiFailure): return exc.message
        if hasattr(exc,'code'): return f'{exc.code}: {getattr(exc,"message","Model processing failed; check server configuration and retry.")}'
        return f'{type(exc).__name__}: Processing failed; verify the document and model configuration, then retry.'
    def get_candidate(s,cid):
        if cid not in s['candidates'] or s['candidates'][cid].get('removedAt'): raise ApiFailure('not_found','Candidate not found',404)
        return s['candidates'][cid]
    def card(s,c,display=...):
        a=s['assessments'].get(c.get('assessmentId'))
        current=a and a['status']=='current' and s['standards'] and a['standardsVersion']==s['standards']['version']['id'] and a['hatVersion']==s['hat']['version']['id']
        profile=copy.deepcopy(c.get('profile'))
        mapping={organization_key(m['organization']):m['group'] for m in s['companyGroupConfig']['mappings']}
        for experience in (profile or {}).get('experiences',[]):
            experience['companyGroup']=mapping.get(organization_key(experience['organization']))
        result=dict(id=c['id'],name=(c.get('profile') or {}).get('name') or c['name'],fictional=c['fictional'],documentId=c['documentId'],profile=profile,stage=c['stage'],grade=a['grade'] if current else 'pending',rank=None,matchSummary=a['summary'] if a else '',keyUnknown=a.get('keyUnknown') if a else None,assessmentStatus=a['status'] if a else 'none',standardsVersion=a['standardsVersion'] if a else None,hatVersion=a['hatVersion'] if a else None,error=c.get('error'))
        if current and a.get('policyVersion')==POLICY_VERSION:result['gradeReason']=grade_reason(s['standards']['criteria'],a['observations'],a['reviewed'])[1]
        result['hrReview']=copy.deepcopy(c.get('hrReview') or {'decision':None,'note':'','updatedAt':None})
        if c.get('_deletePending'):
            result.update(deletionPending=True,stage='needs_attention',grade='pending',error='删除未完成，请点击右上角垃圾桶重试。')
        display=display_projection.get(s,c) if display is ... else display
        if display is not None:result['display']=display['card']
        return result
    def listing(s):
        cards=[card(s,c) for c in s['candidates'].values() if not c.get('removedAt') or c.get('_deletePending')]
        def key(c):
            a=s['assessments'].get(s['candidates'][c['id']].get('assessmentId'))
            return ({'A':0,'B':1,'C':2,'pending':3}[c['grade']],-a['rankValue'] if a else 0,c['id'])
        cards.sort(key=key)
        for i,c in enumerate(cards): c['rank']=i+1 if c['grade']!='pending' else None
        return dict(candidates=cards,standardsVersion=s['standards']['version']['id'] if s['standards'] else None,hatVersion=s['hat']['version']['id'],activeRunId=s['activeRunId'])
    def new_run(s,standards=None):
        run=dict(id=uid(),status='queued',mode=provider.mode,standardsVersion=standards['version']['id'] if standards else None,hatVersion=s['hat']['version']['id'],steps=[],error=None,createdAt=now())
        run['_agentSettings']=snapshot_settings(s,provider)
        s['runs'][run['id']]=run;s['activeRunId']=run['id']
        return run
    def profile_identity(document,standards,hat):
        # No credentials in cache metadata. Endpoint fingerprint distinguishes provider deployments.
        provider_type=type(provider).__module__+'.'+type(provider).__qualname__
        endpoint=str(getattr(provider,'_base',None) or 'default-endpoint')
        identity={'mode':provider.mode,'model':provider.model,'providerType':provider_type,
                'endpointFingerprint':hashlib.sha256(endpoint.encode()).hexdigest(),
                'documentId':document['id'],'documentHash':document['hash'],
                'standardsVersion':standards['version']['id'],'hatVersion':hat['version']['id']}
        if document['filename'].lower().endswith('.docx'):
            identity['docxIntegrityVersion']=document.get('_docxIntegrityVersion')
        return identity
    def trace(rid,event):
        with store.transaction() as s:
            cid=event.get('candidateId');candidate=s['candidates'].get(cid)
            if rid not in s['runs'] or (cid and not candidate):raise ApiFailure('candidate_deleted','简历已删除，本次处理已停止。',409)
            run=s['runs'][rid]
            expected=run.get('_inputs',{}).get('generations',{}).get(cid)
            if candidate and (candidate.get('removedAt') or (expected is not None and candidate.get('_lastRemovalGeneration',0)>expected)):
                raise ApiFailure('stale_candidate','简历已移除，本次处理已停止。',409)
            if '_diagnostic' in event:
                diagnostic={'runId':rid,'role':event['role'],'attempt':event['attempt'],'candidateId':event.get('candidateId'),'standardsVersion':run['standardsVersion'],'hatVersion':run['hatVersion'],'createdAt':now(),**copy.deepcopy(event['_diagnostic'])}
                if hasattr(provider,'usage_path'):diagnostic['usageReference']={'ledger':'postgres:model_calls' if remote else 'model-usage.sqlite3','runId':rid}
                reference=store.write_diagnostic(diagnostic) if remote else write_diagnostic(store.root,diagnostic)
                run.setdefault('_diagnosticFiles',[]).append(reference)
                if diagnostic['kind']=='validated_profile':
                    cid=event.get('candidateId');candidate=s['candidates'].get(cid)
                    generation=run.get('_inputs',{}).get('generations',{}).get(cid)
                    if candidate and candidate['generation']==generation and candidate['documentId']==diagnostic['documentId']:
                        candidate['profile']=copy.deepcopy(diagnostic['profile'])
                        source_run=s['runs'].get(diagnostic.get('sourceRunId'),run) if diagnostic.get('providerCalled') is False else run
                        candidate['_cvSettingsHash']=cv_settings_hash(source_run.get('_agentSettings'))
                        inputs=run['_inputs']
                        candidate['profileSource']=profile_identity(s['documents'][candidate['documentId']],inputs['standards'],inputs['hat'])
                return
            step={k:event.get(k) for k in ('role','status','candidateId','standardsVersion','hatVersion','attempt','summary','startedAt','completedAt')}
            step.update(id=uid(),hatVersion=run['hatVersion'],standardsVersion=run['standardsVersion'])
            step['attempt']=step['attempt'] or 1
            step['summary']=step['summary'] or ''
            existing=next((x for x in reversed(run['steps']) if x['role']==step['role'] and x['attempt']==step['attempt'] and x['candidateId']==step['candidateId'] and x['status']=='running'),None)
            if existing and step['status']!='running':
                step['id']=existing['id'];step['startedAt']=existing['startedAt'];existing.update(step)
            else:run['steps'].append(step)
    def finish_run(rid,errors):
        with store.transaction() as s:
            if rid not in s['runs']:return
            s['runs'][rid].update(status='failed' if errors else 'succeeded',error='; '.join(errors) if errors else None)
            if s['activeRunId']==rid:s['activeRunId']=None
    def public_document(doc):
        d={k:copy.deepcopy(v) for k,v in doc.items() if k not in ('path','text','hash','blobKey')}
        d.pop('_docxIntegrityVersion',None)
        for p in d['pages']:
            has_image=p.pop('imagePath',None) or p.pop('imageBlobKey',None)
            p.pop('imageHash',None)
            p['imageUrl']=f"/api/documents/{d['id']}/pages/{p['number']}" if has_image else None
        d['originalUrl']=f"/api/documents/{d['id']}/original"
        return d
    def parse_file(doc):
        source = str(blobs.materialize(doc['blobKey'], doc['hash'])) if remote else doc['path']
        output = store.root/'previews'/doc['id']
        if parser: result=parser(source,str(output),doc['id'])
        else:
            from hr_documents import parse_document
            result=parse_document(source,str(output),doc['id'])
        if doc['filename'].lower().endswith('.docx'):
            result['_docxIntegrityVersion']='complete-source-v1'
        result.update(path=doc['path'],filename=doc['filename'],hash=doc['hash'])
        if remote:
            result['blobKey']=doc['blobKey']
            for page in result.get('pages',[]):
                local=page.pop('imagePath',None)
                if local:
                    path=Path(local).resolve()
                    if not path.is_relative_to(output.resolve()):
                        raise ApiFailure('invalid_preview','Parser preview escaped output directory',500)
                    digest=hashlib.sha256(path.read_bytes()).hexdigest()
                    key=f"previews/{doc['id']}/{digest}{path.suffix}"
                    blobs.put_file(key,path)
                    page.update(imageBlobKey=key,imageHash=digest)
        return result
    def publish_result(rid,cid,doc,standards,hat,generations,result,binding_check=None):
        grade,value=rank(standards['criteria'],result['observations'],result['reviewed'])
        assessment=dict(id=uid(),candidateId=cid,runId=rid,standardsVersion=standards['version']['id'],hatVersion=hat['version']['id'],mode=provider.mode,status='current' if result['reviewed'] else 'review_required',grade=grade,rankValue=value,summary=result['summary'],keyUnknown=result.get('keyUnknown'),observations=result['observations'],matches=result['matches'],concerns=result['concerns'],questions=result['questions'],reviewed=result['reviewed'],createdAt=now(),policyVersion=POLICY_VERSION,gradeReason=grade_reason(standards['criteria'],result['observations'],result['reviewed'])[1])
        with store.transaction() as t:
            latest=t['standards']; cc=t['candidates'].get(cid)
            if cc is None:return False
            eligible=not cc.get('removedAt') and cc['generation']==generations[cid] and hat['version']['id']==t['hat']['version']['id'] and same_meaning(standards,latest)
            if binding_check is not None:eligible=eligible and binding_check(t)
            if eligible:
                # Preserve original inference snapshot; attribute priority adoption to its own rank run.
                if standards['version']['id'] != latest['version']['id']:
                    original=copy.deepcopy(assessment);original['status']='stale'
                    t['assessments'][original['id']]=original
                    t['runs'][rid].setdefault('_outputAssessmentIds',[]).append(original['id'])
                    active=t['activeRunId'];rerank=new_run(t,latest);t['activeRunId']=active
                    rerank.update(status='succeeded',_sourceRunId=rid,steps=[dict(id=uid(),role='rank',status='succeeded',candidateId=cid,standardsVersion=latest['version']['id'],hatVersion=hat['version']['id'],attempt=1,summary='Adopted validated coverage from prior priority snapshot; zero additional model calls.',startedAt=now(),completedAt=now())])
                    assessment.update(id=uid(),runId=rerank['id'])
                    rerank['_outputAssessmentIds']=[assessment['id']]
                assessment['standardsVersion']=latest['version']['id']
                assessment['grade'],assessment['rankValue']=rank(latest['criteria'],assessment['observations'],assessment['reviewed'])
                assessment['gradeReason']=grade_reason(latest['criteria'],assessment['observations'],assessment['reviewed'])[1]
                cc.update(assessmentId=assessment['id'],profile=result['profile'],profileSource=profile_identity(doc,standards,hat),stage='ready' if result['reviewed'] else 'needs_attention',error=None)
                for f in t['feedback'].get(cid,[]):
                    if f['status']=='rechecking': f['status']='applied' if result['reviewed'] else 'needs_review'
            else: assessment['status']='stale'
            t['assessments'][assessment['id']]=assessment
            t['runs'][rid].setdefault('_outputAssessmentIds',[]).append(assessment['id'])
        return eligible
    def process(rid,cids,standards,hat,generations):
        with store.transaction() as s:
            if rid not in s['runs']:return
            s['runs'][rid]['status']='running'
            s['runs'][rid]['_inputs']={'standards':standards,'hat':hat,'candidateIds':cids,'generations':generations}
        errors=[]
        for cid in cids:
            try:
                s=store.read(); c=s['candidates'].get(cid)
                if c is None:continue
                doc=s['documents'][c['documentId']]
                if doc['status']!='ready' or (doc['filename'].lower().endswith('.docx') and doc.get('_docxIntegrityVersion')!='complete-source-v1'):
                    with store.transaction() as t:
                        if cid not in t['candidates'] or t['candidates'][cid]['generation']!=generations[cid]:continue
                        t['candidates'][cid]['stage']='parsing'
                    with store.lock:
                        latest=store.read()['candidates'].get(cid)
                        if latest is None or latest.get('removedAt') or latest['generation']!=generations[cid]:continue
                        doc=parse_file(doc)
                        with store.transaction() as t: t['documents'][doc['id']]=doc
                if doc['status']!='ready' or not doc['blocks']:
                    raise ApiFailure('document_not_ready','Document needs attention: '+'; '.join(doc.get('warnings',[])))
                if not standards:
                    with store.transaction() as t:
                        if cid in t['candidates'] and t['candidates'][cid]['generation']==generations[cid]:t['candidates'][cid].update(stage='uploaded',error=None)
                    continue
                require_provider()
                with store.transaction() as t:
                    if cid not in t['candidates'] or t['candidates'][cid]['generation']!=generations[cid]:continue
                    t['candidates'][cid]['stage']='analyzing'
                result=workflow.analyze(doc,standards,hat,rid,cid,feedback=s['feedback'].get(cid,[]),profile=c.get('profile') if provider.mode=='live' and not any(f['status']=='rechecking' for f in s['feedback'].get(cid,[])) and c.get('profileSource')==profile_identity(doc,standards,hat) and c.get('_cvSettingsHash')==cv_settings_hash(s['runs'][rid].get('_agentSettings')) else None,trace_callback=lambda event:trace(rid,event))
                publish_result(rid,cid,doc,standards,hat,generations,result)
            except Exception as exc:
                message=safe_error(exc); errors.append(message)
                with store.transaction() as t:
                    if cid in t['candidates'] and t['candidates'][cid]['generation']==generations[cid]:
                        t['candidates'][cid].update(stage='needs_attention',error=message)
                        for f in t['feedback'].get(cid,[]):
                            if f['status']=='rechecking': f['status']='needs_review'
        finish_run(rid,errors)
    def schedule(s,bg,cids):
        cids=[cid for cid in cids if not s['candidates'][cid].get('removedAt')]
        if not cids:return None
        standards=copy.deepcopy(s['standards']);hat=copy.deepcopy(s['hat'])
        run=new_run(s,standards);run['_candidateIds']=list(cids); generations={}
        for cid in cids:
            c=s['candidates'][cid];c['generation']+=1;generations[cid]=c['generation']
            c.update(stage='analyzing' if standards else 'parsing',error=None)
            if c.get('assessmentId'):s['assessments'][c['assessmentId']]['status']='stale'
        run['_generations']=generations
        bg.add_task(process,run['id'],list(cids),standards,hat,generations)
        return run['id']
    def assessment_wire(a,standards): return {k:v for k,v in reading_order(a,standards).items() if k!='policyVersion'} if a and standards else a

    @app.get('/health')
    def health():return {'status':'ok'}
    @app.get('/api/workspace')
    def workspace():
        s=store.read()
        return dict(jobTitle='Micro Connect HR AI Agent Intern',standards=s['standards'],draftStandards=s['draftStandards'],hat=s['hat'],draftHat=s['draftHat'],mode=provider.mode,provider=dict(configured=provider.configured,model=provider.model),activeRunId=s['activeRunId'])
    @app.get('/api/versions')
    def versions():
        s=store.read();return dict(standards=s['standardsHistory'],hats=s['hats'])
    @app.get('/api/company-groups')
    def company_groups():
        return store.read()['companyGroupConfig']
    @app.put('/api/company-groups')
    def update_company_groups(body:CompanyGroupInput):
        mappings=[];seen=set()
        if not body.actor.strip():raise ApiFailure('invalid_actor','Actor must be nonempty.',422)
        for row in body.mappings:
            key=organization_key(row.organization)
            group=normalize(unicodedata.normalize('NFKC',row.group))
            if not key or key in seen or not group:
                raise ApiFailure('invalid_company_mapping','Organization keys must be unique and nonempty after normalization; group labels must be nonempty.',422)
            seen.add(key)
            mappings.append({'organization':normalize(unicodedata.normalize('NFKC',row.organization)),'group':group})
        with store.transaction() as s:
            previous=s['companyGroupConfig']
            if previous['version']['id']!=body.expectedVersion:
                raise ApiFailure('stale_version','Company groups changed; refresh before saving.',409)
            config={'version':version(previous['version']['number']+1,body.actor.strip()),'mappings':mappings}
            s['companyGroupConfig']=config;s['companyGroupHistory'].append(copy.deepcopy(config))
            return config
    @app.post('/api/standards/parse')
    def parse_jd(body:ParseInput,bg:BackgroundTasks):
        require_provider()
        with store.transaction() as s:
            s['standardsGeneration']+=1; generation=s['standardsGeneration']
            run=new_run(s); context=dict(runId=run['id'],standardsVersion=None,hatVersion=s['hat']['version']['id'],hat=copy.deepcopy(s['hat']))
        def job():
            try:
                with store.transaction() as t:t['runs'][run['id']]['status']='running'
                criteria=workflow.parse_jd(body.jdText,context,trace_callback=lambda event:trace(run['id'],event))
                validate_criteria(body.jdText,criteria)
                with store.transaction() as t:
                    if t['standardsGeneration']==generation and t['hat']['version']['id']==context['hatVersion']:
                        t['draftStandards']=dict(version=version(),jdText=body.jdText,criteria=criteria)
                finish_run(run['id'],[])
            except Exception as exc:finish_run(run['id'],[safe_error(exc)])
        bg.add_task(job);return {'runId':run['id']}
    @app.put('/api/standards/draft')
    def draft(body:DraftInput):
        validate_criteria(body.jdText,body.criteria)
        with store.transaction() as s:
            s['standardsGeneration']+=1
            s['draftStandards']=dict(version=version(),jdText=body.jdText,criteria=body.criteria)
            return s['draftStandards']
    @app.post('/api/standards/chat')
    def standards_chat(body:TextInput):
        require_provider();s=store.read()
        try:
            if hasattr(workflow,'standards_chat'):
                result=workflow.standards_chat(body.message,s['draftStandards'] or s['standards'],s['hat'])
                proposal=result.get('draft')
                if proposal:
                    validate_criteria(proposal['jdText'],proposal['criteria'])
                    with store.transaction() as t:
                        if t['standardsGeneration']!=s['standardsGeneration']:raise ApiFailure('stale_draft','Draft changed; retry your message.',409)
                        t['standardsGeneration']+=1;proposal['version']=version();t['draftStandards']=proposal
                return {'reply':result['reply'],'draft':proposal or s['draftStandards']}
            raise ApiFailure('unavailable','Standards refinement provider is not available.',503)
        except ApiFailure:raise
        except Exception as exc:raise ApiFailure('model_failed',safe_error(exc),503)
    @app.post('/api/standards/confirm')
    def confirm(body:ActorInput,bg:BackgroundTasks):
        with store.transaction() as s:
            if not s['draftStandards']:raise ApiFailure('no_draft','Save a standards draft first.',409)
            obj=copy.deepcopy(s['draftStandards']);validate_criteria(obj['jdText'],obj['criteria'])
            obj['version']=version(len(s['standardsHistory'])+1,body.actor)
            s['standardsHistory'].append(obj);s['standards']=obj;s['draftStandards']=None;s['standardsGeneration']+=1
            rid=schedule(s,bg,list(s['candidates']))
            return {'standards':obj,'runId':rid}
    @app.post('/api/standards/group-move')
    def group_move(body:GroupMoveInput,bg:BackgroundTasks):
        require_provider()
        if not body.actor.strip():raise ApiFailure('invalid_actor','Provide the confirming actor.',422)
        with store.transaction() as s:
            current=s['standards']
            if not current or current['version']['id']!=body.standardsVersion:
                raise ApiFailure('stale_version','Standards changed; refresh before confirming this move.',409)
            criterion=next((c for c in current['criteria'] if c['id']==body.criterionId),None)
            if not criterion or criterion['group']==body.targetGroup:
                raise ApiFailure('invalid_group_move','Move one existing criterion to the other group; use priorities for same-group ordering.',422)
            target=[c for c in current['criteria'] if c['group']==body.targetGroup]
            if body.position>len(target):raise ApiFailure('invalid_position','Target position must be within the destination group.',422)
            draft=s['draftStandards']
            if draft and (draft['jdText']!=current['jdText'] or sorted(draft['criteria'],key=lambda c:c['id'])!=sorted(current['criteria'],key=lambda c:c['id'])):
                raise ApiFailure('draft_conflict','A different standards draft exists. Save/confirm or resolve it before moving a confirmed criterion.',409)
            obj=copy.deepcopy(current)
            moved=next(c for c in obj['criteria'] if c['id']==body.criterionId)
            groups={g:sorted([c for c in obj['criteria'] if c['group']==g and c['id']!=body.criterionId],key=lambda c:c['priority']) for g in ('must','bonus')}
            moved['group']=body.targetGroup;groups[body.targetGroup].insert(body.position,moved)
            for group in groups.values():
                for i,c in enumerate(group):c['priority']=i
            obj['criteria']=groups['must']+groups['bonus']
            validate_criteria(obj['jdText'],obj['criteria'])
            obj['version']=version(len(s['standardsHistory'])+1,body.actor.strip())
            s['standardsHistory'].append(obj);s['standards']=obj;s['draftStandards']=None;s['standardsGeneration']+=1
            rid=schedule(s,bg,list(s['candidates']))
            return {'standards':obj,'runId':rid}
    @app.patch('/api/standards/priorities')
    def priorities(body:OrderInput):
        with store.transaction() as s:
            current=s['standards']
            if not current or current['version']['id']!=body.standardsVersion:raise ApiFailure('stale_version','Standards changed; refresh before reordering.',409)
            for group,ids in [('must',body.must),('bonus',body.bonus)]:
                expected={c['id'] for c in current['criteria'] if c['group']==group}
                if len(ids)!=len(set(ids)) or set(ids)!=expected:raise ApiFailure('invalid_order','Provide an exact permutation within each criterion group.',422)
            obj=copy.deepcopy(current);obj['version']=version(len(s['standardsHistory'])+1,'priority reorder')
            for c in obj['criteria']:c['priority']=(body.must if c['group']=='must' else body.bonus).index(c['id'])
            s['standards']=obj;s['standardsHistory'].append(obj)
            run=new_run(s,obj)
            for c in s['candidates'].values():
                old=s['assessments'].get(c.get('assessmentId'))
                if old and old['status']=='current' and old['standardsVersion']==current['version']['id'] and old['hatVersion']==s['hat']['version']['id']:
                    a=reading_order(old,obj);a.update(id=uid(),runId=run['id'],standardsVersion=obj['version']['id'],createdAt=now())
                    a['grade'],a['rankValue']=rank(obj['criteria'],a['observations'],a['reviewed'])
                    a['policyVersion']=POLICY_VERSION;a['gradeReason']=grade_reason(obj['criteria'],a['observations'],a['reviewed'])[1]
                    s['assessments'][a['id']]=a;c['assessmentId']=a['id']
            run.update(status='succeeded',steps=[dict(id=uid(),role='rank',status='succeeded',candidateId=None,standardsVersion=obj['version']['id'],hatVersion=s['hat']['version']['id'],attempt=1,summary=f'{POLICY_VERSION}: priorities changed; validated cached observations reused; zero model calls.',startedAt=now(),completedAt=now())])
            s['activeRunId']=next((r['id'] for r in reversed(list(s['runs'].values())) if r['status'] in ('queued','running')),None)
            return listing(s)
    @app.put('/api/hat/draft')
    def hat_draft(body:HatInput):
        expected={r[0] for r in RULES}
        if len(body.rules)!=5 or {r.get('id') for r in body.rules}!=expected or any(r.get('enabled') is not True or not r.get('title') or not r.get('text') for r in body.rules):
            raise ApiFailure('core_hat_required','All five core HAT rules must remain enabled; prose may clarify but cannot disable source, version, criteria or advisory enforcement.',422)
        with store.transaction() as s:
            s['draftHat']=dict(version=version(),rules=body.rules);return s['draftHat']
    @app.post('/api/hat/confirm')
    def hat_confirm(body:ActorInput,bg:BackgroundTasks):
        with store.transaction() as s:
            if not s['draftHat']:raise ApiFailure('no_draft','Save a HAT draft first.',409)
            h=copy.deepcopy(s['draftHat']);h['version']=version(len(s['hats'])+1,body.actor)
            s['hat']=h;s['hats'].append(h);s['draftHat']=None
            rid=schedule(s,bg,list(s['candidates']))
            return {'hat':h,'runId':rid}
    def ingest(filename,data,fictional):
        if not filename or Path(filename).name!=filename or '\\' in filename or '\x00' in filename or filename in ('.','..'):
            raise ApiFailure('invalid_filename','Upload filename must not contain a path.')
        suffix=Path(filename).suffix.lower()
        if suffix not in MIMES:raise ApiFailure('unsupported_type','Supported files: PDF, DOCX, PNG, JPEG.')
        if not data or len(data)>MAX_FILE:raise ApiFailure('invalid_size','File must contain 1 byte to 20 MiB.')
        digest=hashlib.sha256(data).hexdigest()
        # Remote object is durable before any committed state references it.
        did=uid();cid=uid();key=f'uploads/{did}/original{suffix}'
        target=store.root/key
        if remote:
            snapshot=store.read()
            for candidate in snapshot['candidates'].values():
                if snapshot['documents'][candidate['documentId']]['hash']==digest:
                    if candidate.get('removedAt'):raise ApiFailure('candidate_removed','该简历正在删除，请完成删除后重新上传。',409)
                    return candidate['id'],False
            blobs.put_bytes(key,data,MIMES[suffix])
        with store.transaction() as s:
            for c in s['candidates'].values():
                if s['documents'][c['documentId']]['hash']==digest:
                    if c.get('removedAt'):raise ApiFailure('candidate_removed','该简历正在删除，请完成删除后重新上传。',409)
                    return c['id'],False
            if not remote:
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            s['documents'][did]=dict(id=did,filename=filename,mimeType=MIMES[suffix],status='failed',previewKind='text',pages=[],blocks=[],warnings=[],text='',path=key if remote else str(target),hash=digest)
            if remote:s['documents'][did]['blobKey']=key
            s['candidates'][cid]=dict(id=cid,name=Path(filename).stem,fictional=fictional,documentId=did,profile=None,stage='uploaded',error=None,generation=0,assessmentId=None)
            return cid,True
    @app.post('/api/candidates')
    async def upload(bg:BackgroundTasks,files:list[UploadFile]=File(...),fictional:str=Form('false')):
        ids=[];fresh=[];errors=[]
        for f in files:
            try:
                data=await f.read(MAX_FILE+1);cid,new=ingest(f.filename,data,fictional.lower()=='true');ids.append(cid)
                if new:fresh.append(cid)
            except ApiFailure as exc:errors.append({'filename':f.filename or '', 'message':exc.message})
            finally:await f.close()
        with store.transaction() as s:rid=schedule(s,bg,fresh)
        return dict(candidateIds=ids,runId=rid,errors=errors)
    @app.post('/api/demo/import')
    def demo(body:DemoInput,bg:BackgroundTasks):
        entries=[];errors=[];manifest=ROOT/'fixtures/manifest.json'
        if manifest.exists():
            try:entries=json.loads(manifest.read_text())['candidates']
            except Exception:errors.append({'filename':'manifest.json','message':'Sample manifest is invalid.'})
        else:errors.append({'filename':'manifest.json','message':'Synthetic sample manifest is not yet installed.'})
        paths=[(e.get('file',''),True) for e in entries]
        if body.includeReal:paths.append(('fixtures/real/Bao_Zijun_HKU_CV.pdf',False))
        ids=[];fresh=[]
        for relative,fictional in paths:
            path=(ROOT/relative).resolve()
            try:
                if not path.is_relative_to((ROOT/'fixtures').resolve()):raise ApiFailure('invalid_path','Sample path escapes fixtures.')
                if not path.is_file():raise ApiFailure('missing_sample','Sample file is missing.')
                if path.stat().st_size>MAX_FILE:raise ApiFailure('invalid_size','Sample exceeds 20 MiB.')
                cid,new=ingest(path.name,path.read_bytes(),fictional);ids.append(cid)
                if new:fresh.append(cid)
            except ApiFailure as exc:errors.append({'filename':Path(relative).name,'message':exc.message})
        with store.transaction() as s:rid=schedule(s,bg,fresh)
        return dict(candidateIds=ids,runId=rid,errors=errors)
    @app.get('/api/candidates')
    def candidates():return listing(store.read())
    @app.delete('/api/candidates/{cid}')
    def permanently_delete_candidate(cid:str,permanent:bool=False):
        if not permanent:raise ApiFailure('confirmation_required','删除方式已更新，请刷新页面后确认永久删除。',409)
        try:
            if not delete_candidate(store,blobs,cid):raise ApiFailure('not_found','Candidate not found',404)
        except ApiFailure:raise
        except Exception:raise ApiFailure('deletion_failed','删除未完成，请重试删除；不会显示为已删除成功。',503)
        return {'candidateId':cid,'deleted':True}
    @app.get('/api/candidates/{cid}')
    def detail(cid:str):
        s=store.read();c=get_candidate(s,cid)
        display=display_projection.get(s,c)
        result=dict(candidate=card(s,c,display),document=public_document(s['documents'][c['documentId']]),assessment=assessment_wire(s['assessments'].get(c.get('assessmentId')),s['standards']),chat=s['chats'].get(cid,[]),feedback=s['feedback'].get(cid,[]))
        if display is not None:result['display']=display
        return result
    @app.post('/api/candidates/{cid}/analyze')
    def analyze(cid:str,bg:BackgroundTasks):
        with store.transaction() as s:
            get_candidate(s,cid)
            if not s['standards']:raise ApiFailure('standards_required','Confirm job standards before analysis.',409)
            # Explicit recheck joins an already active run for this candidate.
            for r in reversed(list(s['runs'].values())):
                if r['status'] in ('queued','running') and cid in r.get('_candidateIds',[]) and r.get('_generations',r.get('_inputs',{}).get('generations',{})).get(cid,0)>=s['candidates'][cid].get('_lastRemovalGeneration',0) and r['standardsVersion']==s['standards']['version']['id'] and r['hatVersion']==s['hat']['version']['id']:return {'runId':r['id']}
            rid=schedule(s,bg,[cid]);s['runs'][rid]['_candidateIds']=[cid]
            return {'runId':rid}
    @app.post('/api/candidates/{cid}/chat')
    def chat(cid:str,body:TextInput):
        require_provider();s=store.read();c=get_candidate(s,cid)
        user=dict(id=uid(),role='user',text=body.message,evidence=[],createdAt=now())
        with store.transaction() as t:
            get_candidate(t,cid)
            t['chats'].setdefault(cid,[]).append(user)
        assessment=s['assessments'].get(c.get('assessmentId'))
        if not assessment or assessment['status']!='current' or assessment['standardsVersion']!=(s['standards'] or {}).get('version',{}).get('id') or assessment['hatVersion']!=s['hat']['version']['id']:
            assessment=None
        history=[{'role':m['role'],'text':m['text'][:2000]} for m in s['chats'].get(cid,[])[-12:]]
        corrections=[{k:f[k] for k in ('id','status','pointId')} | {'text':f['text'][:2000]} for f in s['feedback'].get(cid,[])[-20:]]
        try:result=workflow.chat(body.message,s['documents'][c['documentId']],s['standards'],s['hat'],{'candidateId':cid,'assessment':assessment,'chat':history,'feedback':corrections})
        except Exception as exc:raise ApiFailure('model_failed',safe_error(exc),503)
        answer=dict(id=uid(),role='assistant',text=result['text'],evidence=result['evidence'],createdAt=now())
        with store.transaction() as t:
            get_candidate(t,cid)
            if t['candidates'][cid]['generation']!=c['generation'] or (t['standards'] or {}).get('version')!=(s['standards'] or {}).get('version') or t['hat']['version']['id']!=s['hat']['version']['id']:
                raise ApiFailure('stale_context','Candidate analysis or standards changed while answering; retry your message against the current version.',409)
            t['chats'].setdefault(cid,[]).append(answer)
        return answer
    @app.put('/api/candidates/{cid}/hr-review')
    def save_hr_review(cid:str,body:HrReviewInput):
        with store.transaction() as s:
            candidate=get_candidate(s,cid)
            review={'decision':body.decision,'note':body.note,'updatedAt':now()}
            candidate['hrReview']=review
            return copy.deepcopy(review)

    @app.post('/api/candidates/{cid}/feedback')
    def feedback(cid:str,body:FeedbackInput,bg:BackgroundTasks):
        with store.transaction() as s:
            c=get_candidate(s,cid);a=s['assessments'].get(c.get('assessmentId'))
            if body.pointId and (not a or body.pointId not in {p['id'] for p in a['matches']+a['concerns']}):raise ApiFailure('invalid_point','Analysis point does not belong to this candidate.',422)
            f=dict(id=uid(),candidateId=cid,text=body.text,pointId=body.pointId,status='rechecking' if s['standards'] else 'recorded',createdAt=now())
            s['feedback'].setdefault(cid,[]).append(f)
            rid=schedule(s,bg,[cid]) if s['standards'] else None
            return dict(feedback=f,runId=rid)
    @app.get('/api/runs/{rid}')
    def run(rid:str):
        s=store.read()
        if rid not in s['runs']:raise ApiFailure('not_found','Run not found',404)
        return {k:v for k,v in s['runs'][rid].items() if not k.startswith('_')}
    @app.get('/api/documents/{did}/original')
    def original(did:str):
        doc=store.read()['documents'].get(did)
        if not doc:raise ApiFailure('not_found','Document not found',404)
        path=blobs.materialize(doc['blobKey'],doc['hash']) if remote else Path(doc['path']).resolve()
        if not remote and (not path.is_relative_to(store.root/'uploads') or not path.is_file()):raise ApiFailure('not_found','Original unavailable',404)
        return FileResponse(path,media_type=doc['mimeType'],filename=doc['filename'],content_disposition_type='inline')
    @app.get('/api/documents/{did}/pages/{number}')
    def page(did:str,number:int):
        doc=store.read()['documents'].get(did)
        if not doc:raise ApiFailure('not_found','Document not found',404)
        item=next((p for p in doc['pages'] if p['number']==number),None)
        if not item or not (item.get('imagePath') or item.get('imageBlobKey')):raise ApiFailure('not_found','Page image unavailable; use text preview or download original.',404)
        path=blobs.materialize(item['imageBlobKey'],item['imageHash']) if remote else Path(item['imagePath']).resolve()
        if not remote and (not path.is_relative_to(store.root/'previews') or not path.is_file()):raise ApiFailure('not_found','Page image unavailable',404)
        return FileResponse(path,media_type='image/png' if path.suffix.lower()=='.png' else 'image/jpeg')
    app.state.resume_hooks={'new_run':new_run,'trace':trace,'finish_run':finish_run,
                            'publish_result':publish_result,'profile_identity':profile_identity,'safe_error':safe_error}
    return app

"""Owner-requested transfer of reviewed live runs between this app's deployments.

This is an explicit import, never a model fallback. The original live trace is
retained, sources are rebound by identical original text, and grades are recomputed.
No uploaded file, HR evaluation, standard or active agent setting is imported.
"""
from copy import deepcopy
import hashlib
import json
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from .agent_workbench import public_data
from .display import ANALYSIS_FIELDS
from .evidence import (compose_point_explanations, interview_plan, normalize_evidence,
    validate_interview_focus, validate_source_ownership)
from .schemas import CandidateProfile, CriterionRowsResult, CriterionRowsReview, InterviewResult
from .source_references import resolve_row_references
from .structured_evidence import project_rows, row_claim_grounding, validate_row_review
from .workflow import validate_analysis, validate_presentation, validate_refs


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def checked(payload,state):
    """Pure preflight. The authenticated owner supplies the trace, not its grade."""
    def require(ok,message):
        if not ok:raise ValueError(message)
    cid=payload['candidateId'];candidate=state['candidates'][cid]
    require(not candidate.get('removedAt') and not candidate.get('deletionPending'),'Candidate is unavailable.')
    require(candidate.get('assessmentId')==payload['expectedAssessmentId'],'Candidate assessment changed; reload before transfer.')
    require(not any(r['status'] in ('running','queued') and cid in r.get('_candidateIds',[]) for r in state['runs'].values()),'Candidate already has active work.')
    document=state['documents'][candidate['documentId']];standards=state['standards'];hat=state['hat']
    require(document['hash']==payload['originalSHA256'],'Original file differs; no transfer performed.')
    require(standards==payload['standards'] and hat==payload['hat'],'Confirmed standards or HAT differ.')
    debug=payload['sourceDebug'];source=debug['run'];settings=debug['settings']
    require(source['status']=='succeeded' and source['mode']=='live' and settings['runtime']['mode']=='live','Only a successful live run can be transferred.')
    require(source['standardsVersion']==standards['version']['id'] and source['hatVersion']==hat['version']['id'],'Source versions differ.')
    require(all(settings[k]==state['agentSettings'][k] for k in ('roles','flow')),'Model tasks or parameters differ; explicitly activate the tested settings first.')
    require(not debug.get('truncated') and payload['sourceUsage']['modelRequests']>=3,'Complete original live trace and numeric usage are required.')
    blocks=payload['sourceDocument']['blocks'];target=document['blocks']
    require(len(blocks)==len(target) and all(a['page']==b['page'] and normalize_evidence(a['text'])==normalize_evidence(b['text']) for a,b in zip(blocks,target)),'Original extraction differs; no inferred or fuzzy source mapping is allowed.')
    mapping={a['id']:b['id'] for a,b in zip(blocks,target)}
    def mapped(value):
        if isinstance(value,list):return [mapped(item) for item in value]
        if isinstance(value,dict):
            return {k:(mapping[v] if k=='blockId' else mapped(v)) for k,v in value.items()}
        return value
    artifacts=[a['data'] for a in debug['artifacts']]
    require(all(a['runId']==source['id'] for a in artifacts),'Trace contains another run.')
    def last(kind,role=None):
        values=[a for a in artifacts if a['kind']==kind and (role is None or a['role']==role)]
        require(bool(values),'Missing original artifact: '+kind+'/'+str(role));return values[-1]
    profile_art=last('validated_profile');match=last('match_validation');review=last('review_decision')
    require(profile_art['documentId']==payload['sourceDocument']['id'],'Profile belongs to another original.')
    require(not match['validationIssues'] and review['publishable'] and review['reviewApproved'] and not review['validationIssues'] and not review['reviewIssues'] and not review.get('reviewContractIssues'),'Source review was not clean and publishable.')
    require(match['attempt']==review['attempt'] and match['analysis']==review['analysis'],'Source match/review disagree.')
    profile=CandidateProfile.model_validate(mapped(profile_art['profile'])).model_dump()
    raw_rows=CriterionRowsResult.model_validate(mapped(last('role_output','match')['output'])).model_dump()
    rows,_=resolve_row_references(raw_rows,document,standards)
    require(rows==mapped(match['structuredEvidence']),'Validated rows do not derive from the original model output.')
    row_review=CriterionRowsReview.model_validate(mapped(last('role_output','review')['output'])).model_dump()
    require(row_review['approved'] and not validate_row_review(row_review,rows,standards,require_checks=True),'Original semantic review is invalid.')
    analysis,grounding,targets,_,issues=project_rows(rows,standards,profile)
    analysis=compose_point_explanations(analysis,grounding)
    require(not issues and all(public_data(analysis[k])==mapped(match['analysis'][k]) for k in ANALYSIS_FIELDS),'Reading projection differs from the reviewed result.')
    questions=InterviewResult.model_validate(mapped(last('role_output','interview')['output'])).model_dump()['questions']
    final=last('interview_validation')
    require(not final['validationIssues'] and questions==mapped(final['questions']),'Interview differs from the validated original output.')
    errors=validate_refs([profile,rows,row_review,questions],document)+validate_analysis(analysis,document,standards)+validate_presentation(analysis)
    errors+=validate_source_ownership({'observations':[],'concerns':[]},row_claim_grounding(rows),document,profile)
    errors+=validate_interview_focus(questions,interview_plan(analysis,standards,profile,targets),profile)
    require(not errors,'Source-bound validation failed: '+'; '.join(errors))
    return dict(candidateId=cid,document=deepcopy(document),standards=deepcopy(standards),hat=deepcopy(hat),
        profile=profile,analysis=analysis,grounding=grounding,targets=targets,rows=rows,questions=questions,
        sourceSettings=deepcopy(settings),sourceRunId=source['id'],mapping=mapping,
        binding=digest({'candidate':candidate,'document':document,'standards':standards,'hat':hat,'settings':state['agentSettings']}))


def install_verified_import(app,store,provider):
    def receipt(run):
        result={'runId':run['id'],'imported':True,'modelCalls':0}
        if run.get('_verifiedImportWarning'):result['warning']=run['_verifiedImportWarning']
        return result
    def perform_import(raw):
        if provider.mode!='live':return JSONResponse({'error':{'code':'live_transfer_only','message':'Verified live transfer is unavailable in fixture mode.'}},status_code=409)
        if len(raw)>4*1024*1024:return JSONResponse({'error':{'code':'transfer_too_large','message':'Verified transfer exceeds 4 MiB.'}},status_code=413)
        try:
            payload=json.loads(raw);fingerprint=digest(payload);state=store.read()
            prior=next((r for r in state['runs'].values() if r.get('_verifiedImportDigest')==fingerprint and r['status']=='succeeded'),None)
            if prior:return receipt(prior)
            plan=checked(payload,state)
        except (ValueError,KeyError,TypeError,IndexError) as exc:
            message=str(exc) if isinstance(exc,ValueError) else 'Incomplete source trace or binding.'
            return JSONResponse({'error':{'code':'invalid_verified_transfer','message':message}},status_code=409)
        hooks=app.state.resume_hooks;cid=plan['candidateId'];source=plan['sourceRunId']
        with store.transaction() as state:
            try:check=checked(payload,state)
            except (ValueError,KeyError,TypeError,IndexError):return JSONResponse({'error':{'code':'transfer_conflict','message':'Candidate or settings changed before transfer.'}},status_code=409)
            if check['binding']!=plan['binding']:return JSONResponse({'error':{'code':'transfer_conflict','message':'Candidate changed before transfer.'}},status_code=409)
            prior=next((r for r in state['runs'].values() if r.get('_verifiedImportSource')==source and cid in r.get('_candidateIds',[]) and r['status']=='succeeded'),None)
            if prior:return receipt(prior)
            candidate=state['candidates'][cid];candidate['generation']+=1;generation=candidate['generation']
            run=hooks['new_run'](state,plan['standards']);rid=run['id']
            run.update(status='running',_candidateIds=[cid],_verifiedImportSource=source,_verifiedImportDigest=fingerprint,_agentSettings=plan['sourceSettings'],
                _inputs={'standards':plan['standards'],'hat':plan['hat'],'candidateIds':[cid],'generations':{cid:generation}})
            if candidate.get('assessmentId'):state['assessments'][candidate['assessmentId']]['status']='stale'
            candidate.update(stage='analyzing',error=None)
        context=dict(runId=rid,candidateId=cid,standardsVersion=plan['standards']['version']['id'],hatVersion=plan['hat']['version']['id'])
        def trace(role,kind,details):
            hooks['trace'](rid,{**context,'role':role,'attempt':1,'_diagnostic':{'kind':kind,'imported':True,'providerCalled':False,'sourceRunId':source,**details}})
        try:
            trace('review','verified_live_import_provenance',{'sourceTraceSHA256':digest(payload['sourceDebug']),
                'sourceDebug':payload['sourceDebug'],'sourceUsage':payload['sourceUsage'],'blockMapping':plan['mapping'],
                'notice':'Explicit owner transfer of locally reviewed live model outputs. No cloud inference was called; original source settings and trace are retained.'})
            trace('cv','validated_profile',{'documentId':plan['document']['id'],'profile':plan['profile']})
            common={'analysis':plan['analysis'],'pointGrounding':plan['grounding'],'verificationTargets':plan['targets'],'validationIssues':[]}
            trace('match','match_validation',{**common,'structuredEvidence':plan['rows']})
            trace('review','review_decision',{**common,'reviewIssues':[],'reviewApproved':True,'publishable':True})
            trace('interview','interview_validation',{'questionPlan':interview_plan(plan['analysis'],plan['standards'],plan['profile'],plan['targets']),'questions':plan['questions'],'validationIssues':[]})
            for role in ('jd','cv','match','review','interview'):
                hooks['trace'](rid,{**context,'role':role,'attempt':1,'status':'skipped','summary':'Transferred reviewed local live output from '+source+'; no new model call.'})
            result={**plan['analysis'],'profile':plan['profile'],'questions':plan['questions'],'reviewed':True}
            def mark_publication(state):
                # Committed atomically with the actual assessment, including when
                # the storage client loses its response after a successful write.
                state['runs'][rid]['_verifiedImportPublished']=True
                return True
            if not hooks['publish_result'](rid,cid,plan['document'],plan['standards'],plan['hat'],{cid:generation},result,binding_check=mark_publication):raise ValueError('Candidate changed during transfer.')
            hooks['finish_run'](rid,[])
        except Exception:
            with store.transaction() as state:
                imported=state['runs'][rid]
                if imported.get('_verifiedImportPublished'):
                    imported.update(status='succeeded',error=None,_verifiedImportWarning='Validated result was published; final trace bookkeeping needs inspection. No repeated inference is required.')
                    if state.get('activeRunId')==rid:state['activeRunId']=None
                    return receipt(imported)
                candidate=state['candidates'].get(cid)
                if candidate and candidate['generation']==generation:
                    candidate.update(stage='needs_attention',error='verified_transfer_failed: Verified transfer stopped before publication; retry after checking its trace.')
            hooks['finish_run'](rid,['verified_transfer_failed: Transfer did not complete; inspect its trace before retrying.'])
            return JSONResponse({'error':{'code':'verified_transfer_failed','message':'Transfer did not complete; inspect the run before retrying.','runId':rid}},status_code=503)
        return receipt(store.read()['runs'][rid])

    @app.post('/api/agent-workbench/import-verified-run')
    async def import_verified_run(request:Request):
        return await run_in_threadpool(perform_import,await request.body())

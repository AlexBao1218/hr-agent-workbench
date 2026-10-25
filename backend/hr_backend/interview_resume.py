"""Explicit local interview-only recovery. Default CLI is read-only; no HTTP endpoint."""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from .display import DisplayProjection
from .evidence import (capability_index, compose_point_explanations, interview_plan,
                       validate_capability_coverage, validate_point_grounding, validate_verification_targets)
from .schemas import CandidateProfile
from .workflow import WorkflowError, validate_analysis, validate_presentation, validate_refs, validate_standards


def digest(value):
    # Same canonical encoding as the independent saved-document audit.
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def require(condition,message):
    if not condition:raise WorkflowError('resume_binding_invalid',message)


def provider_identity(provider,document,standards,hat):
    return {'mode':provider.mode,'model':provider.model,
            'providerType':type(provider).__module__+'.'+type(provider).__qualname__,
            'endpointFingerprint':hashlib.sha256(str(getattr(provider,'_base',None) or 'default-endpoint').encode()).hexdigest(),
            'documentId':document['id'],'documentHash':document['hash'],
            'standardsVersion':standards['version']['id'],'hatVersion':hat['version']['id']}


def prepare(root,state,provider,source_run_id,candidate_id,document_digest,own_run_id=None):
    """No Store construction, writes, network or ledger access. Reject ambiguous provenance."""
    root=Path(root).resolve()
    try:
        run=state['runs'][source_run_id];candidate=state['candidates'][candidate_id]
        standards=state['standards'];hat=state['hat'];inputs=run['_inputs']
        doc=state['documents'][candidate['documentId']]
        require(not state.get('activeRunId') or state['activeRunId']==own_run_id,'Another run is active.')
        require(not any(r['status'] in ('queued','running') and r['id']!=own_run_id for r in state['runs'].values()),'Unfinished run exists.')
        require(run['status']=='failed' and str(run.get('error','')).startswith('invalid_interview_evidence:'),'Source must have failed at interview validation.')
        require(run['mode']==provider.mode and candidate_id in run['_candidateIds'] and candidate_id in inputs['candidateIds'],'Candidate/source mode mismatch.')
        require(inputs['generations'][candidate_id]==candidate['generation'],'Candidate generation changed.')
        require(inputs['standards']==standards and inputs['hat']==hat and run['standardsVersion']==standards['version']['id'] and run['hatVersion']==hat['version']['id'],'Confirmed standards/HAT changed.')
        require(standards['version'].get('confirmedAt') and hat['version'].get('confirmedAt'),'Snapshots must be confirmed.')
        require(not any(f['status']=='rechecking' for f in state['feedback'].get(candidate_id,[])),'Feedback recheck is active.')
        current=state['assessments'].get(candidate.get('assessmentId'),{})
        require(current.get('status')!='current','Candidate already has a current assessment.')
        require(doc['status']=='ready' and doc['blocks'] and digest(doc)==document_digest,'Document content differs from the independently reviewed source digest.')
        require(hashlib.sha256(Path(doc['path']).read_bytes()).hexdigest()==doc['hash'],'Original file hash changed.')
        require(candidate.get('profileSource')==provider_identity(provider,doc,standards,hat),'Profile/document/provider provenance changed.')
        artifacts=DisplayProjection(root)._artifacts(run)
        require(artifacts is not None and len(artifacts)==len(set(run['_diagnosticFiles'])),'Missing, unsafe or duplicate diagnostic index.')
        bound=[(ref,a) for ref,a in zip(run['_diagnosticFiles'],artifacts) if a.get('candidateId')==candidate_id]
        require(all(a.get('standardsVersion')==run['standardsVersion'] and a.get('hatVersion')==run['hatVersion'] for _,a in bound),'Diagnostic version mismatch.')
        def one(kind):
            items=[(r,a) for r,a in bound if a.get('kind')==kind]
            require(bool(items),'Required diagnostic missing: '+kind)
            return items[-1]
        profile_ref,profile_art=one('validated_profile');match_ref,match=one('match_validation')
        review_ref,review=one('review_decision');failed_ref,failed=one('interview_validation')
        require(profile_art['documentId']==doc['id'] and profile_art['profile']==candidate['profile'],'Validated profile changed.')
        CandidateProfile.model_validate(profile_art['profile'])
        require(not match['validationIssues'] and review['reviewApproved'] is True and review['publishable'] is True
                and not review['validationIssues'] and not review['reviewIssues'] and not review.get('exclusionValidationIssues'),'Review is not approved and clean.')
        require(match['attempt']==review['attempt'] and match['analysis']==review['analysis'] and match['pointGrounding']==review['pointGrounding'],'Match/review artifacts do not agree.')
        require(failed['validationIssues'] and len(failed['questions'])==3,'Missing original failed question diagnostic.')
        profile=profile_art['profile'];analysis=review['analysis'];grounding=review['pointGrounding']
        targets=review.get('verificationTargets',[])
        require(targets==match.get('verificationTargets') and len(targets)==3,'Legacy source has no reviewed verification plan; use normal analysis.')
        validate_standards(standards)
        errors=validate_refs([profile,analysis,grounding,targets],doc)+validate_verification_targets(targets,analysis,standards,profile)+validate_analysis(analysis,doc,standards)
        errors+=validate_point_grounding(analysis,grounding,require_facts=provider.mode=='live')
        errors+=validate_capability_coverage(analysis,capability_index(doc,profile),exclusions=review.get('sourceExclusions',[]),grounding=grounding)
        if provider.mode=='live':errors+=validate_presentation(analysis)
        require(not errors,'Saved analysis fails deterministic validation: '+'; '.join(errors))
        require(all(item['evidence'] for item in profile['education']+profile['experiences']),'Source profile contains unsupported history.')
        require(compose_point_explanations(analysis,grounding)==analysis,'Grounded public text changed.')
        hashes={ref:hashlib.sha256((root/ref).read_bytes()).hexdigest() for ref in run['_diagnosticFiles']}
        binding={'sourceRunId':source_run_id,'sourceRunDigest':digest(run),'candidateId':candidate_id,
                 'generation':candidate['generation'],'assessmentId':candidate.get('assessmentId'),
                 'documentDigest':document_digest,'documentId':doc['id'],'documentHash':doc['hash'],
                 'profileDigest':digest(profile),'profileSource':candidate['profileSource'],
                 'standards':standards,'hat':hat,'feedback':state['feedback'].get(candidate_id,[]),'artifactHashes':hashes}
        return deepcopy({'digest':digest(binding),'binding':binding,'document':doc,'profile':profile,'analysis':analysis,
                         'artifacts':[(profile_ref,profile_art),(match_ref,match),(review_ref,review)],
                         'verificationTargets':targets,'questionPlan':interview_plan(analysis,standards,profile,targets)})
    except WorkflowError:raise
    except (KeyError,TypeError,ValueError,OSError,AttributeError):
        raise WorkflowError('resume_binding_invalid','Missing or invalid source provenance; no model call made.') from None


def execute(app,source_run_id,candidate_id,document_digest,expected_digest):
    """Reserve once; exactly one interview call; use the normal publication function."""
    if getattr(app.state.store,'is_remote',False):
        raise WorkflowError('remote_resume_unsupported','Manual historical recovery is local-only in this release; no remote run was started.')
    store=app.state.store;provider=app.state.provider;workflow=app.state.workflow;hooks=app.state.resume_hooks
    require(provider.configured,'Provider is not configured.')
    require(bool(expected_digest),'An explicitly checked binding digest is required.')
    with store.transaction() as state:
        plan=prepare(store.root,state,provider,source_run_id,candidate_id,document_digest)
        require(plan['digest']==expected_digest,'Checked recovery binding changed.')
        binding=plan['binding'];standards=binding['standards'];hat=binding['hat']
        run=hooks['new_run'](state,standards);rid=run['id']
        run.update(status='running',_sourceRunId=source_run_id,_resumeKind='interview_only',
                   _resumeBinding=deepcopy(binding),_candidateIds=[candidate_id],
                   _inputs={'standards':deepcopy(standards),'hat':deepcopy(hat),'candidateIds':[candidate_id],'generations':{candidate_id:binding['generation']}})
        state['candidates'][candidate_id].update(stage='analyzing',error=None)
    context={'runId':rid,'candidateId':candidate_id,'standardsVersion':standards['version']['id'],'hatVersion':hat['version']['id']}
    callback=lambda event:hooks['trace'](rid,event)
    def still_bound(state):
        try:return prepare(store.root,state,provider,source_run_id,candidate_id,document_digest,own_run_id=rid)['digest']==expected_digest
        except WorkflowError:return False
    errors=[]
    try:
        for role in ('jd','cv','match','review'):
            workflow._event(callback,context,role,'skipped',summary=f'Reused validated {role} from run {source_run_id}; no provider call.')
        for ref,artifact in plan['artifacts']:
            details={k:deepcopy(v) for k,v in artifact.items() if k not in ('runId','role','attempt','candidateId','standardsVersion','hatVersion','createdAt','usageReference')}
            details.update(reused=True,providerCalled=False,sourceRunId=source_run_id,sourceArtifact=ref,sourceArtifactSHA256=binding['artifactHashes'][ref])
            workflow._diagnostic(callback,context,artifact['role'],artifact['attempt'],details)
        require(still_bound(store.read()),'Recovery binding changed before interview call.')
        questions=workflow.interview_only(plan['document'],standards,hat,plan['profile'],plan['analysis'],rid,candidate_id,callback,feedback=binding['feedback'],targets=plan['verificationTargets'])
        result={'profile':plan['profile'],**plan['analysis'],**questions,'reviewed':True}
        published=hooks['publish_result'](rid,candidate_id,plan['document'],standards,hat,{candidate_id:binding['generation']},result,binding_check=still_bound)
        require(published,'Recovery binding changed during interview; result was not published as current.')
    except Exception as exc:
        errors.append(hooks['safe_error'](exc))
        with store.transaction() as state:
            candidate=state['candidates'][candidate_id]
            if candidate['generation']==binding['generation'] and state.get('activeRunId')==rid:
                candidate.update(stage='needs_attention',error=errors[-1])
    hooks['finish_run'](rid,errors)
    return {'runId':rid,'status':'failed' if errors else 'succeeded','error':'; '.join(errors) or None}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',required=True);parser.add_argument('--source-run',required=True)
    parser.add_argument('--candidate',required=True);parser.add_argument('--document-digest',required=True)
    parser.add_argument('--execute',action='store_true');parser.add_argument('--expected-digest')
    args=parser.parse_args()
    import os
    if os.environ.get('HR_STORAGE_BACKEND','local') != 'local':
        raise SystemExit('This historical recovery CLI is local-only; remote storage was not modified.')
    # Import loads the same ignored backend environment as the normal application.
    from .app import create_app
    from .provider import create_provider
    provider=create_provider();root=Path(args.data_dir).resolve()
    try:
        if args.execute:
            require(bool(args.expected_digest),'--execute requires --expected-digest from a prior check.')
            # Read-only preflight BEFORE Store creation; never run startup recovery.
            with sqlite3.connect((root/'workspace.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
                state=json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            checked=prepare(root,state,provider,args.source_run,args.candidate,args.document_digest)
            require(checked['digest']==args.expected_digest,'Checked recovery binding changed.')
            app=create_app(root,provider,recover_interrupted=False)
            try:result=execute(app,args.source_run,args.candidate,args.document_digest,args.expected_digest)
            finally:app.state.store.db.close()
        else:
            with sqlite3.connect((root/'workspace.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
                state=json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            plan=prepare(root,state,provider,args.source_run,args.candidate,args.document_digest)
            result={'status':'checked','bindingDigest':plan['digest'],'sourceRunId':args.source_run,
                    'candidateId':args.candidate,'standardsVersion':plan['binding']['standards']['version']['id'],
                    'hatVersion':plan['binding']['hat']['version']['id'],'generation':plan['binding']['generation'],
                    'rolesToCall':['interview'],'automaticRetries':0,'modelCalls':0,'writes':0}
        print(json.dumps(result,ensure_ascii=False))
        return 1 if result['status']=='failed' else 0
    except WorkflowError as exc:
        print(json.dumps({'status':'blocked','code':exc.code,'message':exc.message},ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())

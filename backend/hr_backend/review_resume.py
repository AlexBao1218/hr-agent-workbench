"""Bounded local correction of saved failed review; no CV or match regeneration.

Call prepare against read-only state first, inspect its correction, then execute with
its digest. Both source and correction are re-bound before publication.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from .display import DisplayProjection
from .evidence import (capability_index, compose_point_explanations, interview_plan,
                       point_evidence_roles, profile_navigation, review_context,
                       source_ownership_context, validate_source_ownership,
                       validate_capability_coverage, validate_point_grounding,
                       validate_verification_targets)
from .interview_resume import digest, provider_identity, require
from .schemas import CandidateProfile, MatchResult, ReviewResult
from .workflow import WorkflowError, validate_analysis, validate_presentation, validate_refs, validate_standards


def prepare(root,state,provider,source_run_id,candidate_id,document_digest,correction=None,own_run_id=None):
    """Read-only preflight, including content and immutable diagnostic provenance."""
    root=Path(root).resolve()
    try:
        run=state['runs'][source_run_id];candidate=state['candidates'][candidate_id]
        standards=state['standards'];hat=state['hat'];inputs=run['_inputs']
        doc=state['documents'][candidate['documentId']]
        require(not state.get('activeRunId') or state['activeRunId']==own_run_id,'Another run is active.')
        require(not any(r['status'] in ('queued','running') and r['id']!=own_run_id for r in state['runs'].values()),'Unfinished run exists.')
        require(run['status']=='failed' and str(run.get('error','')).startswith('review_failed:'),'Source must have failed at evidence review.')
        require(run['mode']==provider.mode and candidate_id in run['_candidateIds'] and candidate_id in inputs['candidateIds'],'Candidate/source mode mismatch.')
        require(inputs['generations'][candidate_id]==candidate['generation'],'Candidate generation changed.')
        require(inputs['standards']==standards and inputs['hat']==hat and run['standardsVersion']==standards['version']['id'] and run['hatVersion']==hat['version']['id'],'Confirmed standards/HAT changed.')
        require(standards['version'].get('confirmedAt') and hat['version'].get('confirmedAt'),'Snapshots must be confirmed.')
        require(not any(f['status']=='rechecking' for f in state['feedback'].get(candidate_id,[])),'Feedback recheck is active.')
        current=state['assessments'].get(candidate.get('assessmentId'),{})
        require(current.get('status')!='current','Candidate already has a current assessment.')
        require(doc['status']=='ready' and doc['blocks'] and digest(doc)==document_digest,'Document content differs from checked source digest.')
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
        profile_ref,profile_art=one('validated_profile');match_ref,match=one('match_validation');review_ref,review=one('review_decision')
        require(profile_art['documentId']==doc['id'] and profile_art['profile']==candidate['profile'],'Validated profile changed.')
        CandidateProfile.model_validate(profile_art['profile'])
        require(not review.get('exclusionValidationIssues'),'Source exclusion validation must be clean.')
        require(review['publishable'] is False and bool(review['reviewIssues'] or review['validationIssues']),'Source has no unresolved review issues.')
        require(correction is not None or not match['validationIssues'],'A structurally invalid source requires an explicit corrected payload.')
        require(match['attempt']==review['attempt'] and match['analysis']==review['analysis'] and match['pointGrounding']==review['pointGrounding'],'Match/review artifacts do not agree.')
        require(match['verificationTargets']==review['verificationTargets'],'Match/review target artifacts do not agree.')
        require(not review.get('sourceExclusions') and not review.get('pendingSourceExclusions'),'Sources with exclusions need scope-aware normal repair.')
        profile=profile_art['profile']
        original={'analysis':review['analysis'],'pointGrounding':review['pointGrounding'],'verificationTargets':review['verificationTargets']}
        revised=deepcopy(correction if correction is not None else original)
        require(set(revised)==set(original),'Correction must contain analysis, pointGrounding and verificationTargets only.')
        analysis=revised['analysis'];grounding=revised['pointGrounding'];targets=revised['verificationTargets']
        # This operation fixes prose/source attribution, not the candidate's scores or criteria.
        require([(o['criterionId'],o['coverage']) for o in analysis['observations']]==[(o['criterionId'],o['coverage']) for o in original['analysis']['observations']],'Local correction must preserve criterion coverage.')
        for kind in ('matches','concerns'):
            require([(p['id'],p['criterionIds'],p['kind']) for p in analysis[kind]]==[(p['id'],p['criterionIds'],p['kind']) for p in original['analysis'][kind]],'Local correction must preserve point identities and criterion links.')
        schema_input=deepcopy(analysis)
        for point in schema_input['matches']:point.pop('explanation',None)
        MatchResult.model_validate({**schema_input,'pointGrounding':grounding,'verificationTargets':targets})
        require([(t['id'],t['pointId'],t['criterionId']) for t in targets]==[(t['id'],t['pointId'],t['criterionId']) for t in match.get('verificationTargets',[])] and len(targets)==3,'Local correction must preserve selected target identities and criteria.')
        validate_standards(standards)
        errors=validate_refs([profile,analysis,grounding,targets],doc)+validate_verification_targets(targets,analysis,standards,profile)+validate_analysis(analysis,doc,standards)
        errors+=validate_point_grounding(analysis,grounding,require_facts=provider.mode=='live')
        errors+=validate_source_ownership(analysis,grounding,doc,profile)
        errors+=validate_capability_coverage(analysis,capability_index(doc,profile),grounding=grounding)
        if provider.mode=='live':errors+=validate_presentation(analysis)
        require(not errors,'Correction fails deterministic validation: '+'; '.join(errors))
        require(all(item['evidence'] for item in profile['education']+profile['experiences']),'Source profile contains unsupported history.')
        require(compose_point_explanations(analysis,grounding)==analysis,'Grounded public text differs from claims/assessment.')
        hashes={ref:hashlib.sha256((root/ref).read_bytes()).hexdigest() for ref in run['_diagnosticFiles']}
        binding={'sourceRunId':source_run_id,'sourceRunDigest':digest(run),'candidateId':candidate_id,
                 'generation':candidate['generation'],'assessmentId':candidate.get('assessmentId'),
                 'documentDigest':document_digest,'documentId':doc['id'],'documentHash':doc['hash'],
                 'profileDigest':digest(profile),'profileSource':candidate['profileSource'],
                 'standards':standards,'hat':hat,'feedback':state['feedback'].get(candidate_id,[]),
                 'artifactHashes':hashes,'correctionDigest':digest(revised)}
        return deepcopy({'digest':digest(binding),'binding':binding,'document':doc,'profile':profile,
                         **revised,'original':original,'reviewIssues':review['reviewIssues'],
                         'profileArtifact':(profile_ref,profile_art),
                         'questionPlan':interview_plan(analysis,standards,profile,targets)})
    except WorkflowError:raise
    except (KeyError,TypeError,ValueError,OSError,AttributeError):
        raise WorkflowError('resume_binding_invalid','Missing or invalid source/correction provenance; no model call made.') from None


def execute(app,source_run_id,candidate_id,document_digest,expected_digest,correction=None):
    """Exactly one fresh review and, if approved, one interview; never regenerate analysis."""
    if getattr(app.state.store,'is_remote',False):
        raise WorkflowError('remote_resume_unsupported','Manual historical recovery is local-only in this release; no remote run was started.')
    store=app.state.store;provider=app.state.provider;workflow=app.state.workflow;hooks=app.state.resume_hooks
    require(provider.configured,'Provider is not configured.')
    require(bool(expected_digest),'An explicitly checked binding digest is required.')
    with store.transaction() as state:
        plan=prepare(store.root,state,provider,source_run_id,candidate_id,document_digest,correction)
        require(plan['digest']==expected_digest,'Checked correction binding changed.')
        binding=plan['binding'];standards=binding['standards'];hat=binding['hat']
        run=hooks['new_run'](state,standards);rid=run['id']
        run.update(status='running',_sourceRunId=source_run_id,_resumeKind='review_correction',
                   _resumeBinding=deepcopy(binding),_candidateIds=[candidate_id],
                   _inputs={'standards':deepcopy(standards),'hat':deepcopy(hat),'candidateIds':[candidate_id],'generations':{candidate_id:binding['generation']}})
        state['candidates'][candidate_id].update(stage='analyzing',error=None)
    context={'runId':rid,'candidateId':candidate_id,'standardsVersion':standards['version']['id'],'hatVersion':hat['version']['id']}
    callback=lambda event:hooks['trace'](rid,event)
    def still_bound(state):
        try:return prepare(store.root,state,provider,source_run_id,candidate_id,document_digest,correction,own_run_id=rid)['digest']==expected_digest
        except WorkflowError:return False
    errors=[]
    try:
        for role in ('jd','cv','match'):
            workflow._event(callback,context,role,'skipped',summary='Reused saved source analysis with explicit local correction; no generation call.')
        ref,artifact=plan['profileArtifact']
        workflow._diagnostic(callback,context,'cv',1,{'kind':'validated_profile','documentId':plan['document']['id'],'profile':plan['profile'],
            'providerCalled':False,'reused':True,'sourceRunId':source_run_id,'sourceArtifact':ref,'sourceArtifactSHA256':binding['artifactHashes'][ref]})
        analysis=plan['analysis'];grounding=plan['pointGrounding'];targets=plan['verificationTargets'];doc=plan['document'];profile=plan['profile']
        provenance={'sourceRunId':source_run_id,'sourceArtifactHashes':binding['artifactHashes'],'correctionDigest':binding['correctionDigest'],'providerCalled':False}
        workflow._diagnostic(callback,context,'match',1,{'kind':'match_validation','analysis':analysis,'pointGrounding':grounding,'verificationTargets':targets,'validationIssues':[],**provenance})
        require(still_bound(store.read()),'Correction binding changed before review.')
        payload={'context':context,'standards':standards,'hat':hat,'document':{'id':doc['id'],'blocks':doc['blocks']},
                 'profile':profile_navigation(profile),'analysis':analysis,'verificationTargets':targets,
                 'sourceOwnershipContext':source_ownership_context(doc,profile),'pendingSourceExclusions':[],
                 'capabilityIndexPolicy':'Retrieval candidates only, not scored claims. Irrelevant auxiliary hits alone do not block publication.',
                 **review_context(analysis,grounding,capability_index(doc,profile))}
        decision=workflow._call('review',payload,ReviewResult,callback,defer_success=True)
        approved=decision['approved'] and not decision['issues'] and not decision['sourceExclusions'] and not decision['sourceExclusionDecisions']
        workflow._diagnostic(callback,context,'review',1,{'kind':'review_decision','analysis':analysis,'pointGrounding':grounding,'verificationTargets':targets,
            'pointEvidenceRoles':point_evidence_roles(analysis,grounding),'validationIssues':[],'reviewIssues':decision['issues'],'reviewApproved':decision['approved'],
            'sourceExclusions':decision['sourceExclusions'],'sourceExclusionDecisions':decision['sourceExclusionDecisions'],'exclusionValidationIssues':[],
            'publishable':approved,'sourceRunId':source_run_id})
        workflow._event(callback,context,'review','succeeded' if approved else 'failed',summary='Reviewed local source-attribution correction')
        if not approved:raise WorkflowError('review_failed','Local correction did not pass review; original source retained and no full analysis rerun started.')
        require(still_bound(store.read()),'Correction binding changed before interview.')
        questions=workflow.interview_only(doc,standards,hat,profile,analysis,rid,candidate_id,callback,feedback=binding['feedback'],targets=targets)
        result={'profile':profile,**analysis,**questions,'reviewed':True}
        published=hooks['publish_result'](rid,candidate_id,doc,standards,hat,{candidate_id:binding['generation']},result,binding_check=still_bound)
        require(published,'Correction binding changed during execution; result was not published as current.')
    except Exception as exc:
        errors.append(hooks['safe_error'](exc))
        with store.transaction() as state:
            candidate=state['candidates'][candidate_id]
            if candidate['generation']==binding['generation'] and state.get('activeRunId')==rid:
                candidate.update(stage='needs_attention',error=errors[-1])
    hooks['finish_run'](rid,errors)
    return {'runId':rid,'status':'failed' if errors else 'succeeded','error':'; '.join(errors) or None}

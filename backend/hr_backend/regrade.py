"""Explicit zero-model migration of verified current assessments to the active policy."""
from copy import deepcopy
from .display import DisplayProjection
from .policy import POLICY_VERSION, rank, grade_reason
from .store import uid, now


def regrade_cached(store):
    with store.transaction() as state:
        if state.get('activeRunId') or any(r['status'] in ('running','queued') for r in state['runs'].values()):
            raise ValueError('Wait for active analysis before regrading cached results.')
        standards=state.get('standards');hat=state['hat']
        if not standards:return {'runId':None,'changes':[],'skipped':[]}
        projection=DisplayProjection(store.root);changes=[];skipped=[]
        eligible=[]
        for candidate in state['candidates'].values():
            old=state['assessments'].get(candidate.get('assessmentId'))
            if not old or old.get('policyVersion')==POLICY_VERSION:continue
            if (old['status']!='current' or not old['reviewed'] or old['standardsVersion']!=standards['version']['id']
                or old['hatVersion']!=hat['version']['id'] or projection.get(state,candidate) is None):
                skipped.append(candidate['id']);continue
            eligible.append((candidate,old))
        if not eligible:return {'runId':None,'changes':[],'skipped':skipped}
        rid=uid();timestamp=now()
        run=dict(id=rid,status='succeeded',mode=eligible[0][1]['mode'],standardsVersion=standards['version']['id'],
                 hatVersion=hat['version']['id'],steps=[],error=None,createdAt=timestamp,
                 _policyVersion=POLICY_VERSION,_candidateIds=[],_outputAssessmentIds=[],_sourceAssessmentIds=[])
        for candidate,old in eligible:
            fresh=deepcopy(old);fresh.update(id=uid(),runId=rid,createdAt=timestamp,policyVersion=POLICY_VERSION)
            fresh['grade'],fresh['rankValue']=rank(standards['criteria'],fresh['observations'],fresh['reviewed'])
            fresh['gradeReason']=grade_reason(standards['criteria'],fresh['observations'],fresh['reviewed'])[1]
            old['status']='stale';state['assessments'][fresh['id']]=fresh;candidate['assessmentId']=fresh['id']
            run['_candidateIds'].append(candidate['id']);run['_outputAssessmentIds'].append(fresh['id']);run['_sourceAssessmentIds'].append(old['id'])
            changes.append({'candidateId':candidate['id'],'name':candidate['name'],'oldAssessmentId':old['id'],
                            'assessmentId':fresh['id'],'before':old['grade'],'after':fresh['grade'],'reason':fresh['gradeReason']})
            run['steps'].append(dict(id=uid(),role='rank',status='succeeded',candidateId=candidate['id'],
                standardsVersion=standards['version']['id'],hatVersion=hat['version']['id'],attempt=1,
                summary=f'{POLICY_VERSION}: user-authorized evidence-based regrading; cached reviewed observations reused; zero model calls.',startedAt=timestamp,completedAt=timestamp))
        state['runs'][rid]=run
        return {'runId':rid,'changes':changes,'skipped':skipped}

from copy import deepcopy
from hr_backend.policy import rank,grade_reason
from hr_backend.regrade import regrade_cached
from fastapi.testclient import TestClient
from hr_backend.app import create_app
from hr_backend.provider import FixtureProvider
from hr_backend.display import DisplayProjection
from test_api import parser,confirm,upload


def case(values,bonus='supported'):
    criteria=[dict(id=str(i),title=f'Requirement {i}',group='must',priority=i) for i in range(len(values))]
    criteria.append(dict(id='bonus',title='Bonus',group='bonus',priority=0))
    return criteria,[dict(criterionId=str(i),coverage=v) for i,v in enumerate(values)]+[dict(criterionId='bonus',coverage=bonus)]


def test_core_missing_is_insufficient_even_with_background_and_bonus():
    c,o=case(['unknown','unknown','supported','supported','partial','partial'])
    assert rank(c,o)[0]=='C' and grade_reason(c,o)[1]['code']=='insufficient_evidence'
    assert all(x['coverage']!='contradiction' for x in o)


def test_b_requires_core_support_and_at_least_half_evidenced():
    c,o=case(['partial','unknown','partial','unknown'])
    assert rank(c,o)[0]=='B'
    o[2]['coverage']='unknown'
    assert rank(c,o)[0]=='C'


def test_conflict_and_absence_are_distinct_and_bonus_cannot_override():
    c,o=case(['unknown']*4)
    assert grade_reason(c,o)[1]['code']=='insufficient_evidence'
    o[0]['coverage']='contradiction'
    assert grade_reason(c,o)[1]['code']=='explicit_mismatch' and rank(c,o)[0]=='C'


def test_a_and_priority_core_selection_and_unreviewed_pending():
    c,o=case(['supported','supported','supported','unknown'])
    assert rank(c,o)[0]=='A'
    assert rank(c,o,reviewed=False)[0]=='pending'
    c,o=case(['unknown','unknown','supported','supported'])
    assert rank(c,o)[0]=='C'
    c[2]['priority'],c[0]['priority']=c[0]['priority'],c[2]['priority']
    assert rank(c,o)[0]=='B'


def test_regrade_preserves_facts_hr_and_display_and_is_idempotent(tmp_path):
    class NoModel(FixtureProvider):
        block=False
        def generate(self,*args,**kw):
            assert not self.block,'regrade must never call model'
            return super().generate(*args,**kw)
    provider=NoModel();app=create_app(tmp_path,provider,parser);client=TestClient(app);confirm(client);cid=upload(client)['candidateIds'][0]
    with app.state.store.transaction() as state:
        candidate=state['candidates'][cid];old=state['assessments'][candidate['assessmentId']]
        old['policyVersion']='coverage-v1';old.pop('gradeReason',None)
        candidate['hrReview']={'decision':'consider','note':'keep this','updatedAt':'test'}
    before=app.state.store.read();old=deepcopy(before['assessments'][before['candidates'][cid]['assessmentId']]);provider.block=True
    result=regrade_cached(app.state.store);after=app.state.store.read();fresh=after['assessments'][after['candidates'][cid]['assessmentId']]
    assert len(result['changes'])==1 and result['skipped']==[]
    for field in ('observations','matches','concerns','questions','summary','keyUnknown','standardsVersion','hatVersion','reviewed'):
        assert fresh[field]==old[field]
    assert after['candidates'][cid]['hrReview']==before['candidates'][cid]['hrReview']
    assert after['standards']==before['standards'] and after['hat']==before['hat']
    assert DisplayProjection(tmp_path).get(after,after['candidates'][cid])
    assert regrade_cached(app.state.store)['runId'] is None
    assert client.get('/api/candidates/'+cid).json()['candidate']['gradeReason']==fresh['gradeReason']


def test_previously_hidden_grade_is_visible_without_mutating_or_calling_model(tmp_path):
    class NoModel(FixtureProvider):
        block=False
        def generate(self,*args,**kwargs):
            assert not self.block
            return super().generate(*args,**kwargs)
    provider=NoModel();app=create_app(tmp_path,provider,parser);client=TestClient(app);confirm(client);cid=upload(client)['candidateIds'][0]
    with app.state.store.transaction() as state:
        assessment=state['assessments'][state['candidates'][cid]['assessmentId']]
        expected_grade,expected_rank=rank(state['standards']['criteria'],assessment['observations'],True)
        assessment.update(grade='pending',rankValue=0,_recommendation='needs_review',gradeReason={'code':'pending_review','label':'评级待复核','explanation':'old','criterionIds':[]})
    before=app.state.store.read();provider.block=True
    detail=client.get('/api/candidates/'+cid).json()
    listed=next(c for c in client.get('/api/candidates').json()['candidates'] if c['id']==cid)
    assert detail['candidate']['grade']==listed['grade']==detail['assessment']['grade']==expected_grade
    assert detail['assessment']['rankValue']==expected_rank
    assert detail['candidate']['gradeReason']['code']!='pending_review'
    assert 'recommendation' not in detail['candidate']
    assert app.state.store.read()==before
    with app.state.store.transaction() as state:
        state['assessments'][state['candidates'][cid]['assessmentId']]['status']='stale'
    assert client.get('/api/candidates/'+cid).json()['candidate']['grade']=='pending'

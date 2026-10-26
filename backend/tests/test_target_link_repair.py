from copy import deepcopy
import pytest
from hr_backend.evidence import rebind_repaired_verification_targets, validate_verification_targets


@pytest.fixture
def case():
    previous={'matches':[{'id':'strength','criterionIds':['skill','domain']}],'concerns':[]}
    analysis={'matches':[{'id':'strength','criterionIds':['skill']}],
              'concerns':[{'id':'unknown-domain','criterionIds':['domain']}]}
    standards={'criteria':[{'id':'skill'},{'id':'domain'}]}
    targets=[dict(id='verify'+str(i),pointId='strength',criterionId='domain',
                  singleGoal='Does relevant experience exist '+str(i)+'?',decisionReason='Domain remains unknown',
                  experienceId=None,evidence=[]) for i in range(3)]
    return targets,analysis,previous,standards


def test_unique_existing_concern_rebinds_only_point_id(case):
    targets,analysis,previous,standards=case
    originals=deepcopy(case)
    fixed,changes=rebind_repaired_verification_targets(*case)
    assert len(changes)==3 and all(t['pointId']=='unknown-domain' for t in fixed)
    for old,new in zip(targets,fixed):
        assert {k:v for k,v in old.items() if k!='pointId'}=={k:v for k,v in new.items() if k!='pointId'}
    assert case==originals
    assert not validate_verification_targets(fixed,analysis,standards,{'experiences':[]})
    assert rebind_repaired_verification_targets(fixed,analysis,previous,standards)==(fixed,[])


@pytest.mark.parametrize('change',['initial','anchored','borrowed_evidence','ambiguous','missing_concern','unknown_criterion','newly_invented_link','deleted_point','still_valid','duplicate_point'])
def test_unsafe_or_unnecessary_rebinding_not_performed(case,change):
    targets,analysis,previous,standards=case
    if change=='initial':previous=None
    elif change=='anchored':
        for t in targets:t['experienceId']='experience'
    elif change=='borrowed_evidence':
        for t in targets:t['evidence']=[{'blockId':'b','quote':'unchanged'}]
    elif change=='ambiguous':analysis['concerns'].append({'id':'another','criterionIds':['domain']})
    elif change=='missing_concern':analysis['concerns']=[]
    elif change=='unknown_criterion':standards['criteria']=[{'id':'skill'}]
    elif change=='newly_invented_link':previous['matches'][0]['criterionIds']=['skill']
    elif change=='deleted_point':analysis['matches']=[]
    elif change=='still_valid':analysis['matches'][0]['criterionIds'].append('domain')
    elif change=='duplicate_point':analysis['concerns'].append(deepcopy(analysis['concerns'][0]))
    fixed,changes=rebind_repaired_verification_targets(targets,analysis,previous,standards)
    assert fixed==targets and changes==[]
    if change not in ('still_valid',):
        assert validate_verification_targets(fixed,analysis,standards,{'experiences':[]})


def test_bad_link_error_identifies_target_and_keeps_guard(case):
    targets,analysis,_,standards=case
    errors=validate_verification_targets(targets,analysis,standards,{'experiences':[]})
    assert len(errors)==3
    assert 'verify0' in errors[0] and 'strength/domain' in errors[0] and 'criteria=skill' in errors[0]


@pytest.mark.parametrize('final_approved',[False,True])
def test_graph_rebinding_still_requires_semantic_review(final_approved):
    from hr_backend.provider import FixtureProvider
    from hr_backend.workflow import Workflow, WorkflowError

    document={'id':'doc','status':'ready','blocks':[{'id':'b1','text':'Built Python tools.'}]}
    standards={'version':{'id':'s1','confirmedAt':'2026-01-01'},
               'jdText':'Build Python tools. HR operations.',
               'criteria':[{'id':'skill','title':'Python','description':'Build Python tools.',
                            'group':'must','priority':0,'jdQuote':'Build Python tools.'},
                           {'id':'domain','title':'HR','description':'HR operations.',
                            'group':'bonus','priority':0,'jdQuote':'HR operations.'}]}
    hat={'version':{'id':'h1','confirmedAt':'2026-01-01'},'rules':[]}

    class RepairStub(FixtureProvider):
        def __init__(self):
            self.roles=[];self.matches=0;self.reviews=[]

        def generate(self,role,payload,schema):
            self.roles.append(role)
            if role=='review':
                self.reviews.append(deepcopy(payload))
                approved=final_approved and len(self.reviews)==2
                return {'approved':approved,'issues':[] if approved else ['HR scope remains unsupported']}
            result=super().generate(role,payload,schema)
            if role=='match':
                self.matches+=1
                point=result['matches'][0]
                if self.matches==1:
                    point['criterionIds'].append('domain')
                    observation=next(o for o in result['observations'] if o['criterionId']=='domain')
                    observation.update(coverage='partial',evidence=deepcopy(point['evidence']))
                else:
                    assert payload['repairScope']['mode']=='local_correction'
                    assert payload['previousAnalysis']['matches'][0]['criterionIds']==['skill','domain']
                result['verificationTargets']=[dict(id='t'+str(i),pointId=point['id'],criterionId='domain',
                    singleGoal=goal,decisionReason='HR scope remains unknown',experienceId=None,evidence=[])
                    for i,goal in enumerate(['核实个人贡献','核实结果验证方法','核实活动边界'])]
            return result

    provider=RepairStub();events=[]
    workflow=Workflow(provider,max_repairs=1)
    if final_approved:
        result=workflow.analyze(document,standards,hat,'run','candidate',trace_callback=events.append)
        assert result['reviewed'] and len(result['questions'])==3
        assert all(q['pointIds']==['point-domain'] for q in result['questions'])
    else:
        with pytest.raises(WorkflowError,match='Evidence review did not pass'):
            workflow.analyze(document,standards,hat,'run','candidate',trace_callback=events.append)
    assert provider.roles==['cv','match','review','match','review']+(['interview'] if final_approved else [])
    assert all(t['pointId']=='point-domain' for t in provider.reviews[1]['verificationTargets'])
    artifacts=[e['_diagnostic'] for e in events if '_diagnostic' in e]
    rebinding=next(a for a in artifacts if a['kind']=='verification_target_rebinding')
    assert len(rebinding['changes'])==3
    validation=[a for a in artifacts if a['kind']=='match_validation']
    assert all(a['validationIssues']==[] for a in validation)
    raw=[a for a in artifacts if a['kind']=='role_output' and a['schema']=='MatchResult'][-1]['output']
    assert all(t['pointId']=='capability-1' for t in raw['verificationTargets'])
    assert raw['observations']==validation[-1]['analysis']['observations']
    for before,after in zip(raw['verificationTargets'],validation[-1]['verificationTargets']):
        assert {k:v for k,v in before.items() if k!='pointId'}=={k:v for k,v in after.items() if k!='pointId'}
    decisions=[a for a in artifacts if a['kind']=='review_decision']
    assert decisions[-1]['publishable'] is final_approved

"""A missing point-local quote must be actionable in the existing bounded repair."""
from copy import deepcopy
import json

from hr_backend.provider import FixtureProvider
from hr_backend.workflow import Workflow, validate_refs


def test_specific_missing_reference_reaches_repair_without_weakening_source_checks():
    school={'blockId':'school-heading','quote':'Example University '}
    degree={'blockId':'degree','quote':'Bachelor of Engineering, 2023–2028'}
    document={'id':'doc','status':'ready','blocks':[
        {'id':school['blockId'],'text':school['quote']},
        {'id':degree['blockId'],'text':degree['quote']}]}
    standards={'version':{'id':'s1','confirmedAt':'2026-01-01'},'jdText':'Undergraduate degree.',
        'criteria':[{'id':'education','title':'Education','description':'Undergraduate degree.',
                     'group':'must','priority':0,'jdQuote':'Undergraduate degree.'}]}
    hat={'version':{'id':'h1','confirmedAt':'2026-01-01'},'rules':[]}
    profile={'name':'Example','education':[],'experiences':[],'languages':[],'availability':None}

    class RepairStub(FixtureProvider):
        def __init__(self):self.roles=[];self.matches=0

        def generate(self,role,payload,schema):
            self.roles.append(role)
            if role!='match':return super().generate(role,payload,schema)
            self.matches+=1
            refs=[deepcopy(degree)]
            if self.matches==2:
                assert payload['repairScope']['mode']=='local_correction'
                assert 'synchronize that same match point evidence' in payload['repairScope']['instruction']
                assert len(payload['repairIssues'])==1
                issue=payload['repairIssues'][0]
                location=json.JSONDecoder().raw_decode(issue.split(': ',1)[1])[0]
                assert location==dict(pointId='p5',claimIndex=0,**school)
                assert 'include that reference in this point.evidence' in issue
                assert not validate_refs(school,document)
                refs.append(deepcopy(school))
            return {'observations':[{'criterionId':'education','coverage':'partial',
                        'explanation':'Degree stated; completion remains unconfirmed.','evidence':[degree]}],
                    'matches':[{'id':'p5','title':'Education','criterionIds':['education'],
                        'jdRequirement':'Undergraduate degree.','kind':'match','evidence':refs}],
                    'concerns':[{'id':'k1','title':'Completion to confirm','criterionIds':['education'],
                        'jdRequirement':'Undergraduate degree.','explanation':'Verify completion.',
                        'kind':'verify','evidence':[]}],
                    'pointGrounding':[{'pointId':'p5','focus':'other','capabilities':[],
                        'scopeReason':'Education background, no capability scope',
                        'assessment':'Degree stated; completion remains unconfirmed.',
                        'claims':[{'claim':'Studying engineering at Example University.',
                                   'evidence':[school,degree]}]}],
                    'verificationTargets':[{'id':'t'+str(i),'pointId':'k1','criterionId':'education',
                        'singleGoal':goal,'decisionReason':'Verify the remaining scope.',
                        'experienceId':None,'evidence':[]}
                        for i,goal in enumerate(['核实个人贡献','核实结果验证方法','核实活动边界'])],
                    'summary':'Degree stated; completion remains unconfirmed.','keyUnknown':'Completion to confirm'}

    provider=RepairStub();events=[]
    result=Workflow(provider,max_repairs=1).analyze(document,standards,hat,'run','candidate',
        profile=profile,trace_callback=events.append)
    assert result['reviewed'] and len(result['questions'])==3
    assert provider.roles==['match','match','review','interview']
    assert result['matches'][0]['evidence']==[degree,school]
    assert not validate_refs(result,document)
    assert validate_refs(dict(school,quote='A fabricated university'),document)
    artifacts=[e['_diagnostic'] for e in events if '_diagnostic' in e]
    decisions=[a for a in artifacts if a['kind']=='review_decision']
    assert decisions[0]['providerCalled'] is False and decisions[0]['publishable'] is False
    assert decisions[1]['publishable'] is True
    matches=[a for a in artifacts if a['kind']=='match_validation']
    assert len(matches[0]['validationIssues'])==1 and matches[1]['validationIssues']==[]
    assert matches[0]['pointGrounding']==matches[1]['pointGrounding']
    assert matches[0]['analysis']['observations']==matches[1]['analysis']['observations']

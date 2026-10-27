from copy import deepcopy
import pytest
from hr_backend.joint_analysis import Brief, Reviewed, validate, project
from hr_backend.policy import reconciled_grade
from hr_backend.workflow import WorkflowError

DOC={'blocks':[{'id':'b1','page':1,'text':'Built a workflow prototype.'}]}
STANDARDS={'criteria':[{'id':'c1','group':'must','priority':1,'title':'Build','description':'Build an agent'}]}
REF={'blockId':'b1','quote':'Built a workflow prototype.'}
BASE={'observations':[{'criterionId':'c1','coverage':'partial','explanation':'Some evidence','evidence':[REF]}],'matches':[{'id':'m1','title':'Workflow','criterionIds':['c1'],'facts':[{'text':'Built a prototype','refs':[REF]}],'assessment':'Partial support'}],'concerns':[{'id':'x1','title':'Depth','criterionIds':['c1'],'kind':'unknown','basis':'Depth not established','contextRefs':[]}],'recommendation':{'action':'interview','rationale':'Relevant practice','pointIds':['m1','x1']}}
FINAL={**BASE,'summary':{'text':'Workflow practice; depth unclear','advantagePointId':'m1','unknownPointId':'x1'},'questions':[{'id':f'q{i}','mode':'experience','question':f'How did you do part {i}?','purpose':'Check depth','pointIds':['m1'],'criterionIds':['c1'],'refs':[REF]} for i in range(1,4)]}

def test_projection_and_validation():
    data=validate(__import__('hr_backend.joint_analysis',fromlist=['Final']).Final.model_validate(FINAL),DOC,STANDARDS,True)
    out=project(data,STANDARDS,{'name':'Example'})
    assert out['matches'][0]['evidence']==[REF] and len(out['questions'])==3 and out['_recommendation']=='interview'

def test_invalid_refs_and_unsupported_match_blocked():
    x=deepcopy(BASE);x['matches'][0]['facts'][0]['refs'][0]['quote']='fabricated'
    with pytest.raises(WorkflowError):validate(Brief.model_validate(x),DOC,STANDARDS)
    x=deepcopy(BASE);x['observations'][0].update(coverage='unknown',evidence=[])
    with pytest.raises(WorkflowError):validate(__import__('hr_backend.joint_analysis',fromlist=['Final']).Final.model_validate({**x,'summary':FINAL['summary'],'questions':FINAL['questions']}),DOC,STANDARDS,True)

def test_grade_conflict_is_not_silently_published():
    unknown=[{'criterionId':'c1','coverage':'unknown','explanation':'Unknown','evidence':[]}]
    g,_,reason=reconciled_grade(STANDARDS['criteria'],unknown,True,'interview')
    assert g=='pending' and reason['code']=='pending_review'

def test_joint_workflow_calls_flash_then_kimi_and_survives_legacy_review_field():
    from hr_backend.workflow import Workflow
    class Provider:
        mode='live'
        def __init__(self):self.calls=[]
        def generate(self,role,payload,schema):
            self.calls.append((role,payload['_agentRequest']['model']))
            return {'audit':{'verdict':'revised','findings':[]},'final':FINAL} if role=='review' else BASE
    p=Provider(); w=Workflow(p,max_repairs=0)
    roles={role:{'model':model,'systemPrompt':'joint-flash-kimi-v1 test','reasoningEffort':effort,'maxOutputTokens':1000,'temperature':None,'timeoutSeconds':10} for role,model,effort in [('match','deepseek-flash','none'),('review','kimi-k3.1-pro','low')]}
    snap={'roles':roles,'effectivePrompts':{r:x['systemPrompt'] for r,x in roles.items()},'version':{'id':'v1'},'runtime':{'model':'deepseek-flash','jointAnalysis':True}}
    w.settings_resolver=lambda _:snap
    standards={**STANDARDS,'version':{'id':'s','confirmedAt':'yes'},'jdText':'Build an agent','criteria':[dict(STANDARDS['criteria'][0],jdQuote='Build an agent')]}
    doc={**DOC,'id':'doc','status':'ready'};hat={'version':{'id':'h','confirmedAt':'yes'},'rules':[]}
    result=w.analyze(doc,standards,hat,'run','id',profile={'name':'Example'},trace_callback=None)
    assert p.calls==[('match','deepseek-flash'),('review','kimi-k3.1-pro')]
    assert result['summary']==FINAL['summary']['text'] and len(result['questions'])==3

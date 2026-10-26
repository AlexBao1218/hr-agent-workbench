"""Private protocol v2; semantic stub decisions are not live-model acceptance."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from hr_backend.evidence import compose_point_explanations
from hr_backend.schemas import StructuredMatchResult
from hr_backend.structured_evidence import active_structure, project_match, review_projection, validate_review
from hr_backend.workflow import Workflow, WorkflowError, validate_refs


@pytest.fixture
def case():
    refs=[{'blockId':'b1','quote':'Built a workflow prototype for inventory.'},
          {'blockId':'b2','quote':'Tuned and tested retrieval.'},
          {'blockId':'b3','quote':'Researched an optimization method.'}]
    document={'id':'doc','status':'ready','blocks':[{'id':r['blockId'],'text':r['quote']} for r in refs]}
    descriptions=['梳理业务流程，识别自动化机会，并对接HR系统。','测试检索效果，并满足数据隐私要求。','优先具备构建HR助手的实操经验。']
    standards={'version':{'id':'s1','confirmedAt':'2026-01-01'},'jdText':'\n'.join(descriptions),
        'criteria':[{'id':cid,'title':title,'description':text,'jdQuote':text,'group':group,'priority':priority}
                    for cid,title,text,group,priority in zip(['duty','improve','prior'],['工作流职责','质量职责','HR经历加分'],descriptions,['must','must','bonus'],[0,1,0])]}
    profile={'name':'Example','education':[],'experiences':[],'languages':[],'availability':None}
    hat={'version':{'id':'h1','confirmedAt':'2026-01-01'},'rules':[]}
    data={'facts':[{'id':'f1','claim':'构建了库存流程原型。','evidence':[refs[0]]},
                   {'id':'f2','claim':'调优并测试检索。','evidence':[refs[1]]}],
          'components':[{'id':'flow','criterionId':'duty','component':'梳理业务流程','coverage':'partial','factIds':['f1']},
                        {'id':'hr','criterionId':'duty','component':'对接HR系统','coverage':'unknown','factIds':[]},
                        {'id':'test','criterionId':'improve','component':'测试检索效果','coverage':'partial','factIds':['f2']},
                        {'id':'privacy','criterionId':'improve','component':'数据隐私要求','coverage':'unknown','factIds':[]},
                        {'id':'history','criterionId':'prior','component':'构建HR助手的实操经验','coverage':'unknown','factIds':[]}],
          'observations':[{'criterionId':'duty','coverage':'partial','componentIds':['flow','hr']},
                          {'criterionId':'improve','coverage':'partial','componentIds':['test','privacy']},
                          {'criterionId':'prior','coverage':'unknown','componentIds':['history']}],
          'matches':[{'id':'m1','title':'流程原型','factIds':['f1'],'componentIds':['flow','hr'],
                      'focus':'workflow','capabilities':[],'scopeReason':'Workflow prototype only','supplementaryEvidence':[refs[2]]},
                     {'id':'m2','title':'检索测试','factIds':['f2'],'componentIds':['test','privacy'],
                      'focus':'workflow','capabilities':[],'scopeReason':'Retrieval tests only','supplementaryEvidence':[]}],
          'focuses':[{'id':fid,'componentId':cid,'label':label,'objective':'existence','experienceId':None,'evidence':[]}
                     for fid,cid,label in [('goalHR','hr','HR系统集成'),('goalPrivacy','privacy','隐私处理'),('goalPrior','history','HR助手实操')]],
          'concerns':[{'id':'c'+str(i),'focusId':fid} for i,fid in enumerate(['goalHR','goalPrivacy','goalPrior'])],
          'verificationTargets':[{'id':'t'+str(i),'focusId':fid,'decisionReason':'按确认优先级核实这一独立缺口。'}
                                 for i,fid in enumerate(['goalHR','goalPrivacy','goalPrior'])]}
    for component in data['components']:component['requirementQuote']=component['component']
    return document,standards,hat,profile,StructuredMatchResult.model_validate(data).model_dump()


def render(case):
    document,standards,hat,profile,data=case
    analysis,grounding,targets,errors=project_match(data,standards,profile)
    assert not errors
    return compose_point_explanations(analysis,grounding),grounding,targets


def finding(subject='goalHR',statement='对接HR系统',criterion='duty',quote='对接HR系统'):
    return {'subjectType':'focus','subjectId':subject,'issueType':'verification_scope','severity':'blocker',
            'statement':statement,'criterionId':criterion,'requirementQuote':quote,'evidence':[],
            'message':'收窄此当前目标，保留其余未知。'}


def test_one_canonical_fact_drives_rendering_observation_and_review(case):
    original=deepcopy(case)
    analysis,grounding,targets=render(case)
    projection,_=review_projection(case[4],analysis)
    fact=projection['facts'][0]
    assert analysis['matches'][0]['explanation'].count(fact['claim'])==1
    assert fact['claim'] in analysis['observations'][0]['explanation']
    assert '材料未提及「对接HR系统」' in analysis['matches'][0]['explanation']
    assert analysis['observations'][0]['coverage']=='partial'
    assert analysis['observations'][2]['coverage']=='unknown'
    assert 'explanation' not in projection['points'][0]
    assert 'assessment' not in projection['points'][0]
    assert 'pointGrounding' not in projection and 'analysis' not in projection
    assert projection['points'][0]['factIds']==['f1']
    assert not validate_refs(analysis,case[0])
    assert case==original
    broken=deepcopy(case[4]);broken['observations'][0]['explanation']='Independent rewritten claim'
    with pytest.raises(ValidationError):StructuredMatchResult.model_validate(broken)


def test_context_is_clickable_without_becoming_a_narrative_assertion(case):
    analysis,_,_=render(case)
    projection,context=review_projection(case[4],analysis)
    assert len(context)==1
    assert context[0]['quote'] not in analysis['matches'][0]['explanation']
    assert any(r['blockId']=='b3' for r in analysis['matches'][0]['evidence'])
    review={'approved':True,'findings':[]}
    assert not validate_review(review,case[4],analysis,case[1])
    objection={'subjectType':'supplementary_source','subjectId':context[0]['id'],
        'issueType':'unsupported_fact','severity':'blocker','statement':context[0]['quote'],
        'criterionId':None,'requirementQuote':None,'evidence':[{'blockId':'b3','quote':context[0]['quote']}],
        'message':'All supplementary text must appear in the body'}
    assert any('fact/context boundary' in e for e in validate_review({'approved':False,'findings':[objection]},case[4],analysis,case[1]))
    objection.update(issueType='irrelevant_context',message='This source is irrelevant to the point scope.')
    assert not validate_review({'approved':False,'findings':[objection]},case[4],analysis,case[1])


def test_criterion_alternatives_are_not_rewritten_into_an_and_scoring_policy(case):
    document,standards,hat,profile,data=deepcopy(case)
    criterion=standards['criteria'][2]
    criterion.update(description='本科在读或攻读硕士。',jdQuote='本科在读或攻读硕士。')
    standards['jdText']='\n'.join(c['jdQuote'] for c in standards['criteria'])
    ref={'blockId':'degree','quote':'Currently an undergraduate student.'}
    document['blocks'].append({'id':ref['blockId'],'text':ref['quote']})
    data['facts'].append({'id':'degree','claim':'本科在读。','evidence':[ref]})
    data['components'][-1].update(component='本科在读',requirementQuote=criterion['description'],coverage='supported',factIds=['degree'])
    data['components'].append({'id':'master','criterionId':'prior','component':'攻读硕士',
        'requirementQuote':criterion['description'],'coverage':'unknown','factIds':[]})
    data['observations'][-1].update(coverage='supported',componentIds=['history','master'])
    data['focuses'][-1].update(componentId='master',label='硕士就读情况')
    analysis,_,_,errors=project_match(data,standards,profile)
    assert not errors and analysis['observations'][-1]['coverage']=='supported'
    assert '材料未提及「攻读硕士」' in analysis['observations'][-1]['explanation']


def test_confirmed_priority_order_and_full_observations_are_preserved(case):
    changed=deepcopy(case)
    changed[1]['criteria'][0]['priority']=1
    changed[1]['criteria'][1]['priority']=0
    analysis,_,targets=render(changed)
    assert [p['id'] for p in analysis['matches']]==['m2','m1']
    assert [p['id'] for p in analysis['concerns']]==['c1','c0','c2']
    assert len(analysis['observations'])==len(changed[1]['criteria'])
    # Like existing priority-only reorder behavior, reviewed target order is not silently regenerated.
    assert [t['id'] for t in targets]==['t0','t1','t2']


def test_unknown_context_refs_are_retained_without_becoming_a_positive_narrative(case):
    changed=deepcopy(case)
    changed[4]['components'][-1]['factIds']=['f1']
    analysis,_,_=render(changed)
    unknown=analysis['observations'][-1]
    assert unknown['coverage']=='unknown'
    assert '构建了库存流程原型' not in unknown['explanation']
    assert unknown['evidence']==changed[4]['facts'][0]['evidence']
    changed[4]['matches'][1]['factIds'].append('f1')
    changed[4]['matches'][1]['componentIds'].append('history')
    assert project_match(changed[4],changed[1],changed[3])[-1]


def test_same_fact_cannot_evade_canonical_ownership_with_a_new_id(case):
    changed=deepcopy(case)
    duplicate=deepcopy(changed[4]['facts'][0]);duplicate['id']='duplicated-fact'
    changed[4]['facts'].append(duplicate)
    changed[4]['components'][0]['factIds'].append(duplicate['id'])
    assert any('Reuse a canonical fact ID' in e for e in project_match(changed[4],changed[1],changed[3])[-1])


@pytest.mark.parametrize('change',['missing_subject','old_statement','cropped_statement','foreign_criterion','invented_requirement'])
def test_review_findings_must_name_the_current_subject_and_requirement(case,change):
    analysis,_,_=render(case);f=finding()
    if change=='missing_subject':f['subjectId']='old-draft-goal'
    elif change=='old_statement':f['statement']='A claim not present in the current output'
    elif change=='cropped_statement':f['statement']='对接'
    elif change=='foreign_criterion':f['criterionId']='prior'
    else:f['requirementQuote']='An invented hiring threshold'
    assert validate_review({'approved':False,'findings':[f]},case[4],analysis,case[1])


@pytest.mark.parametrize('change',['two_focuses','duplicate_concern','foreign_limit','missing_unknown'])
def test_single_focus_and_component_ownership_are_structural(case,change):
    data=deepcopy(case[4])
    if change=='two_focuses':
        data['concerns'][0]['focusId']=['goalHR','goalPrivacy']
        with pytest.raises(ValidationError):StructuredMatchResult.model_validate(data)
        return
    if change=='duplicate_concern':data['concerns'][1]['focusId']='goalHR'
    elif change=='foreign_limit':data['matches'][0]['componentIds'].append('privacy')
    else:data['observations'][0]['componentIds'].remove('hr')
    assert project_match(data,case[1],case[3])[-1]


class StructuredStub:
    mode='live';configured=True;model='offline-structured-stub'
    def __init__(self,data):self.data=data;self.roles=[];self.match_payloads=[];self.review_payloads=[]
    def generate(self,role,payload,schema):
        self.roles.append(role)
        if role=='match':
            self.match_payloads.append(deepcopy(payload));return deepcopy(self.data)
        if role=='review':
            self.review_payloads.append(deepcopy(payload));return {'approved':True,'findings':[]}
        if role=='cv':
            return {'name':'Example','education':[],'experiences':[],'languages':[],'availability':None}
        assert role=='interview'
        return {'questions':[{'id':'q'+str(i),'question':'你'+p['primaryGoal'],'purpose':p['primaryGoal'],
             'pointIds':[p['pointId']],'criterionIds':[p['criterionId']],'evidence':p['allowedEvidence']}
             for i,p in enumerate(payload['questionPlan'])]}


def test_graph_uses_structured_review_and_keeps_public_shapes(case):
    document,standards,hat,profile,data=case
    provider=StructuredStub(data);events=[]
    result=Workflow(provider,max_repairs=0).analyze(document,standards,hat,'run','candidate',profile=profile,trace_callback=events.append)
    assert result['reviewed'] and len(result['questions'])==3
    assert provider.roles==['match','review','interview']
    assert 'analysis' not in provider.review_payloads[0]
    assert provider.review_payloads[0]['evidenceReview']['facts']==data['facts']
    assert set(result)=={'profile','observations','matches','concerns','summary','keyUnknown','questions','reviewed'}
    assert all(set(p)=={'id','title','criterionIds','jdRequirement','explanation','evidence','kind'} for p in result['matches']+result['concerns'])
    assert all(len(c['criterionIds'])==1 for c in result['concerns'])
    assert not any(k in result for k in ('facts','components','focuses','structuredEvidence'))


def test_unused_inventory_does_not_block_or_leak_into_public_or_review_projection(case):
    document,standards,hat,profile,data=deepcopy(case)
    data['facts'].append({'id':'unused-fact','claim':'Unused intermediate stock.',
                         'evidence':[{'blockId':'unused-outside','quote':'Not a used source'}]})
    data['focuses'].append({'id':'unused-focus','componentId':'unused-component','label':'Unused plan',
        'objective':'existence','experienceId':None,'evidence':[]})
    original=deepcopy(data);provider=StructuredStub(data);events=[]
    result=Workflow(provider,max_repairs=0).analyze(document,standards,hat,'run','candidate',profile=profile,trace_callback=events.append)
    assert result['reviewed']
    projection=provider.review_payloads[0]['evidenceReview']
    assert all(f['id']!='unused-fact' for f in projection['facts'])
    assert all(f['id']!='unused-focus' for f in projection['focuses'])
    assert data==original
    artifacts=[e['_diagnostic'] for e in events if '_diagnostic' in e]
    raw=next(a['output'] for a in artifacts if a['kind']=='role_output' and a['schema']=='StructuredMatchResult')
    assert raw==original  # No fabricated source or in-place alteration of the model output.
    validated=next(a for a in artifacts if a['kind']=='match_validation')
    assert validated['inventoryCounts']=={'factsRaw':3,'factsActive':2,'focusesRaw':4,'focusesActive':3}
    assert not validated['projectionBlocked']
    used=deepcopy(data);used['components'][-1]['factIds']=['unused-fact']
    assert any(r['id']=='unused-fact' for r in active_structure(used)['facts'])
    provider=StructuredStub(used)
    with pytest.raises(WorkflowError,match='Deterministic validation did not pass'):
        Workflow(provider,max_repairs=0).analyze(document,standards,hat,'used-run','candidate',profile=profile)
    assert provider.roles==['match']  # Exact source checks still block actually used invalid inventory.


def test_bad_point_association_is_specific_and_does_not_report_empty_placeholder_errors(case):
    document,standards,hat,profile,data=deepcopy(case)
    data['facts'].append({'id':'fact-research','claim':'研究了优化方法。',
        'evidence':[{'blockId':'b3','quote':'Researched an optimization method.'}]})
    data['matches'][0]['factIds'].append('fact-research')
    events=[];provider=StructuredStub(data)
    with pytest.raises(WorkflowError,match='Deterministic validation did not pass'):
        Workflow(provider,max_repairs=0).analyze(document,standards,hat,'run','candidate',profile=profile,trace_callback=events.append)
    diagnostic=next(e['_diagnostic'] for e in events if e.get('_diagnostic',{}).get('kind')=='match_validation')
    assert diagnostic['projectionBlocked'] and len(diagnostic['validationIssues'])==1
    issue=diagnostic['validationIssues'][0]
    assert '"pointId": "m1"' in issue and '"factIds": ["fact-research"]' in issue
    assert '"componentIds": ["flow", "hr"]' in issue and '"positiveComponents"' in issue
    assert 'Observations must cover' not in issue and 'three reviewed single-purpose' not in issue
    assert data['components'][0]['factIds']==['f1']  # No automatic association guessing.


def test_preexisting_scope_metadata_error_is_visible_in_the_same_structural_pass(case):
    document,standards,hat,profile,data=deepcopy(case)
    data['matches'][1]['focus']='capability'  # Existing rule requires one controlled capability.
    data['facts'].append({'id':'research','claim':'研究了优化方法。',
        'evidence':[{'blockId':'b3','quote':'Researched an optimization method.'}]})
    data['matches'][0]['factIds'].append('research')
    errors=project_match(data,standards,profile)[-1]
    assert len(errors)==2
    assert any('"pointId": "m1"' in e for e in errors)
    assert any('"id": "m2"' in e and 'focus/capabilities mismatch' in e for e in errors)


def test_real_api_store_with_structured_stub_persists_public_result_and_restart_readback(case,tmp_path):
    from fastapi.testclient import TestClient
    from hr_backend.app import create_app
    document,standards,hat,profile,data=case
    def parser(path,output_dir,document_id):
        return {**deepcopy(document),'id':document_id,'filename':'example.pdf','mimeType':'application/pdf',
                'previewKind':'text','pages':[],'warnings':[]}
    provider=StructuredStub(data);client=TestClient(create_app(tmp_path,provider,parser))
    assert client.put('/api/standards/draft',json={k:standards[k] for k in ('jdText','criteria')}).status_code==200
    assert client.post('/api/standards/confirm',json={'actor':'Offline test'}).status_code==200
    upload=client.post('/api/candidates',files={'files':('example.pdf',b'%PDF private offline fixture','application/pdf')}).json()
    cid=upload['candidateIds'][0]
    run=client.get('/api/runs/'+upload['runId']).json()
    assert run['status']=='succeeded',run
    detail=client.get('/api/candidates/'+cid).json()
    assert detail['assessment']['reviewed'] and len(detail['assessment']['questions'])==3
    assert detail['assessment']['standardsVersion']==run['standardsVersion']
    assert provider.roles==['cv','match','review','interview']
    assert not any(k in detail['assessment'] for k in ('facts','components','focuses','structuredEvidence'))
    assert client.get(detail['document']['originalUrl']).content==b'%PDF private offline fixture'
    next_provider=StructuredStub(data)
    reopened=TestClient(create_app(tmp_path,next_provider,parser))
    assert reopened.get('/api/candidates/'+cid).json()['assessment']==detail['assessment']
    assert next_provider.roles==[]


def test_invalid_context_blocker_fails_closed_instead_of_ignoring_review(case):
    document,standards,hat,profile,data=case
    class BadReview(StructuredStub):
        def generate(self,role,payload,schema):
            if role=='review':
                self.roles.append(role)
                context=payload['evidenceReview']['supplementarySources'][0]
                return {'approved':False,'findings':[{'subjectType':'supplementary_source','subjectId':context['id'],
                    'issueType':'unsupported_fact','severity':'blocker','statement':'Researched an optimization method.',
                    'criterionId':None,'requirementQuote':None,'evidence':[{'blockId':'b3','quote':'Researched an optimization method.'}],
                    'message':'Narrate every supplementary quote'}]}
            return super().generate(role,payload,schema)
    provider=BadReview(data)
    with pytest.raises(WorkflowError,match='fact/context boundary'):
        Workflow(provider,max_repairs=2).analyze(document,standards,hat,'run','candidate',profile=profile)
    assert provider.roles==['match','review']


@pytest.mark.parametrize('final_approved',[True,False])
@pytest.mark.parametrize('failure_kind',['source','schema'])
def test_semantic_feedback_survives_a_deterministic_repair_failure(case,final_approved,failure_kind):
    document,standards,hat,profile,data=case
    class Repair(StructuredStub):
        def generate(self,role,payload,schema):
            if role=='match':
                result=super().generate(role,payload,schema)
                if len(self.match_payloads)==1:
                    result['components'][1]['component']=standards['criteria'][0]['description']
                elif len(self.match_payloads)==2:
                    if failure_kind=='schema':return {'invalid':'structured output'}
                    result['facts'][0]['evidence'][0]['quote']='Fabricated source quote'
                else:
                    assert any('[focus:goalHR]' in issue for issue in payload['repairIssues'])
                    if failure_kind=='source':assert any('Invalid source reference' in issue for issue in payload['repairIssues'])
                    else:assert any('required fields' in issue or 'missing' in issue for issue in payload['repairIssues'])
                    assert 'previousAnalysis' not in payload and 'previousPointGrounding' not in payload
                    assert payload['repairScope']['mode']=='local_correction'
                    prior=payload['previousStructuredEvidence']['facts'][0]['evidence'][0]['quote']
                    assert prior==('Fabricated source quote' if failure_kind=='source' else data['facts'][0]['evidence'][0]['quote'])
                return result
            if role=='review':
                self.roles.append(role);self.review_payloads.append(deepcopy(payload))
                approved=len(self.review_payloads)>1 and final_approved
                statement=payload['evidenceReview']['components'][1]['component']
                return {'approved':approved,'findings':[] if approved else [finding(statement=statement,quote=statement)]}
            return super().generate(role,payload,schema)
    provider=Repair(data);events=[]
    workflow=Workflow(provider,max_repairs=2)
    if final_approved:
        assert workflow.analyze(document,standards,hat,'run','candidate',profile=profile,trace_callback=events.append)['reviewed']
    else:
        with pytest.raises(WorkflowError,match='Evidence review did not pass'):
            workflow.analyze(document,standards,hat,'run','candidate',profile=profile,trace_callback=events.append)
    assert provider.roles==['match','review','match','match','review']+(['interview'] if final_approved else [])
    decisions=[e['_diagnostic'] for e in events if e.get('_diagnostic',{}).get('kind')=='review_decision']
    assert decisions[-1]['publishable'] is final_approved

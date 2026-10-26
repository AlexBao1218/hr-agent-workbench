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
          'matches':[{'id':'m1','title':'流程原型','componentIds':['flow','hr'],
                      'focus':'workflow','capabilities':[],'scopeReason':'Workflow prototype only','supplementaryEvidence':[refs[2]]},
                     {'id':'m2','title':'检索测试','componentIds':['test','privacy'],
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
    mode='live';configured=True;model='offline-structured-stub';evidence_protocol=2
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


def test_bad_component_selection_does_not_report_empty_placeholder_errors(case):
    document,standards,hat,profile,data=deepcopy(case)
    data['matches'][0]['componentIds'].append('missing-component')
    events=[];provider=StructuredStub(data)
    with pytest.raises(WorkflowError,match='Deterministic validation did not pass'):
        Workflow(provider,max_repairs=0).analyze(document,standards,hat,'run','candidate',profile=profile,trace_callback=events.append)
    diagnostic=next(e['_diagnostic'] for e in events if e.get('_diagnostic',{}).get('kind')=='match_validation')
    assert diagnostic['projectionBlocked'] and diagnostic['validationIssues']==['Match requires existing positive components: m1']
    assert data['components'][0]['factIds']==['f1']


def test_preexisting_scope_metadata_error_is_visible_in_the_same_structural_pass(case):
    document,standards,hat,profile,data=deepcopy(case)
    data['matches'][1]['focus']='capability'
    data['matches'][0]['componentIds'].append('missing-component')
    errors=project_match(data,standards,profile)[-1]
    assert len(errors)==2
    assert 'Match requires existing positive components: m1' in errors
    assert any('"id": "m2"' in e and 'focus/capabilities mismatch' in e for e in errors)


def test_live_point_schema_derives_facts_and_ignores_legacy_selection(case):
    assert 'factIds' not in StructuredMatchResult.model_json_schema()['$defs']['GroundedPoint']['properties']
    original=deepcopy(case[4])
    original['matches'][0]['factIds']=['f2','missing-fact']
    decoded=StructuredMatchResult.model_validate(original).model_dump()
    assert 'factIds' not in decoded['matches'][0]
    analysis,grounding,_,errors=project_match(original,case[1],case[3])
    assert not errors
    assert grounding[0]['claims']==[{'claim':case[4]['facts'][0]['claim'],'evidence':case[4]['facts'][0]['evidence']}]
    assert not any(r['blockId']=='b2' for r in analysis['matches'][0]['evidence'])
    assert original['matches'][0]['factIds']==['f2','missing-fact']


def test_shared_fact_supports_multiple_criteria_without_repeated_prose(case):
    changed=deepcopy(case)
    changed[4]['components'][2]['factIds']=['f1','f2']
    analysis,grounding,_=render(changed)
    claim=changed[4]['facts'][0]['claim']
    assert sum(p['explanation'].count(claim) for p in analysis['matches'])==1
    assert all(claim in p['explanation'] for p in analysis['observations'][:2])
    assert [p['criterionIds'] for p in analysis['matches']]==[['duty'],['improve']]
    assert any(r['blockId']=='b1' for r in analysis['matches'][1]['evidence'])
    projection,context=review_projection(changed[4],analysis)
    assert projection['points'][1]['factIds']==['f2']
    assert projection['points'][1]['supportFactIds']==['f1','f2']
    assert any(r['pointId']=='m2' and r['blockId']=='b1' for r in context)


def test_fully_redundant_point_merges_scopes_sources_and_target_links(case):
    changed=deepcopy(case)
    data=changed[4]
    data['components'][2]['factIds']=['f1']
    data['focuses'][1].update(componentId='test',label='检索验证')
    data['concerns']=[c for c in data['concerns'] if c['focusId']!='goalPrivacy']
    analysis,grounding,targets=render(changed)
    assert len(analysis['matches'])==len(grounding)==1
    assert analysis['matches'][0]['criterionIds']==['duty','improve']
    assert targets[1]['pointId']=='m1' and targets[1]['criterionId']=='improve'
    assert len(grounding[0]['claims'])==1
    assert '数据隐私要求' in analysis['matches'][0]['explanation']
    assert not validate_review({'approved':True,'findings':[]},data,analysis,changed[1])


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

# Criterion rows v3. Fixtures below are explicit offline judgments, not model results.
from hr_backend.schemas import CriterionRowsResult, CriterionRowsReview
from hr_backend.structured_evidence import row_payload, validate_rows, project_rows, validate_row_review
from hr_backend.prompts import ROW_TEMPLATE_VERSION, LEGACY_ROW_TASKS, row_template_migration


@pytest.fixture
def row_case(case):
    doc,standards,hat,profile,graph=deepcopy(case)
    components={c['id']:c for c in graph['components']};facts={f['id']:f for f in graph['facts']}
    focus={f['componentId']:f for f in graph['focuses']}
    rows=[]
    for observation in graph['observations']:
        evidence=[];unknowns=[]
        for cid in observation['componentIds']:
            component=components[cid]
            if component['coverage']=='unknown':
                f=focus[cid]
                unknowns.append({'requirementQuote':component['requirementQuote'],
                    'singleGoal':'是否有过「'+component['component']+'」的实际经历？','experienceId':f['experienceId'],'evidence':f['evidence']})
            else:
                for fid in component['factIds']:
                    evidence.append({'requirementQuote':component['requirementQuote'],'state':component['coverage'],
                                     'claim':facts[fid]['claim'],'evidence':facts[fid]['evidence']})
        rows.append({'criterionId':observation['criterionId'],'coverage':observation['coverage'],'evidence':evidence,'unknowns':unknowns})
    return doc,standards,hat,profile,CriterionRowsResult.model_validate({'rows':rows}).model_dump()


class RowStub(StructuredStub):
    evidence_protocol=3
    def generate(self,role,payload,schema):
        if role=='match':assert schema is CriterionRowsResult
        if role=='review':assert schema is CriterionRowsReview
        return super().generate(role,payload,schema)


def row_render(row_case):
    doc,standards,hat,profile,data=row_case
    analysis,grounding,targets,selected,errors=project_rows(data,standards,profile)
    assert not errors,errors
    return compose_point_explanations(analysis,grounding),grounding,targets,selected


def test_rows_program_titles_priority_single_goals_and_public_shape(row_case):
    doc,standards,hat,profile,data=row_case
    provider=RowStub(data);events=[]
    result=Workflow(provider,max_repairs=0).analyze(doc,standards,hat,'r','c',profile=profile,trace_callback=events.append)
    assert provider.roles==['match','review','interview'] and len(result['questions'])==3
    assert [p['title'] for p in result['matches']]==[c['title'] for c in standards['criteria'][:2]]
    assert set(result)=={'profile','observations','matches','concerns','summary','keyUnknown','questions','reviewed'}
    initial=provider.match_payloads[0]
    assert not set(initial)&{'profile','capabilityEvidenceIndex','sourceOwnershipContext','confirmedRequirementScope','repairScope','repairIssues','previousRows','previousStructuredEvidence'}
    assert initial['document']['blocks']==[{'id':b['id'],'text':b['text']} for b in doc['blocks']]
    assert 'jdText' not in initial['standards']
    assert initial['context']=={'runId':'r','candidateId':'c','standardsVersion':'s1','hatVersion':'h1'}
    assert initial['hat']['version']['id']=='h1'
    assert provider.review_payloads[0]['criterionEvidence']==data
    assert provider.review_payloads[0]['selectedUnknowns']==[{'criterionId':r['criterionId'],'index':0} for r in data['rows']]
    assert not set(provider.review_payloads[0])&{'matches','pointGrounding','evidenceReview'}
    schema=CriterionRowsResult.model_json_schema()
    assert schema['required']==['rows'] and len(schema['$defs'])==4
    assert all(e['_diagnostic']['publishable'] for e in events if e.get('_diagnostic',{}).get('kind')=='review_decision')
    changed=deepcopy(row_case);changed[1]['criteria'][0]['priority']=1;changed[1]['criteria'][1]['priority']=0
    analysis,_,targets,_=row_render(changed)
    assert [p['criterionIds'] for p in analysis['matches']]==[['improve'],['duty']]
    assert targets[0]['criterionId']=='improve'


def test_rows_same_fact_multi_criterion_deduplicates_body_keeps_all_evidence(row_case):
    changed=deepcopy(row_case);data=changed[4]
    data['rows'][1]['evidence'].insert(0,{**deepcopy(data['rows'][0]['evidence'][0]),'requirementQuote':'测试检索效果'})
    analysis,grounding,_,_=row_render(changed)
    fact=data['rows'][0]['evidence'][0]['claim']
    assert sum(p['explanation'].count(fact) for p in analysis['matches'])==1
    assert all(fact in o['explanation'] for o in analysis['observations'][:2])
    assert {r['blockId'] for p in analysis['matches'] for r in p['evidence']}=={'b1','b2'}
    assert {r['blockId'] for r in analysis['matches'][1]['evidence']}=={'b1','b2'}


def test_rows_overall_or_bonus_and_all_unknowns_preserved(row_case):
    changed=deepcopy(row_case);doc,standards,_,profile,data=changed
    criterion=standards['criteria'][-1]
    criterion.update(description='本科在读或攻读硕士。',jdQuote='本科在读或攻读硕士。')
    row=data['rows'][-1];row.update(coverage='supported',evidence=[{'requirementQuote':'本科在读','state':'supported',
        'claim':'本科在读。','evidence':[{'blockId':'degree','quote':'Currently undergraduate.'}]}],
        unknowns=[{'requirementQuote':'攻读硕士','singleGoal':'是否在攻读硕士？','experienceId':None,'evidence':[]}])
    doc['blocks'].append({'id':'degree','text':'Currently undergraduate.'})
    data['rows'][1]['unknowns'].append({'requirementQuote':'测试检索效果','singleGoal':'是否评估过检索效果的准确性？','experienceId':None,'evidence':[]})
    assert not validate_rows(data,standards,profile)
    analysis,_,_,selected=row_render(changed)
    assert next(o for o in analysis['observations'] if o['criterionId']=='prior')['coverage']=='supported'
    assert '是否在攻读硕士' in next(o for o in analysis['observations'] if o['criterionId']=='prior')['explanation']
    assert not any(s['criterionId']=='prior' for s in selected)
    assert criterion['group']=='bonus'  # No preference becomes a new must-have.
    assert data['rows'][0]['coverage']=='partial'


@pytest.mark.parametrize('change',['false_quote','wrong_owner','wrong_requirement','missing_criterion','false_conflict','unknown_conflict'])
def test_rows_fail_closed_on_source_scope_and_coverage_structure(row_case,change):
    doc,standards,hat,profile,data=deepcopy(row_case)
    if change=='false_quote':data['rows'][0]['evidence'][0]['evidence'][0]['quote']='Fabricated source.'
    elif change=='wrong_requirement':data['rows'][0]['evidence'][0]['requirementQuote']=standards['criteria'][1]['description']
    elif change=='missing_criterion':data['rows'].pop()
    elif change=='false_conflict':data['rows'][-1]['coverage']='contradiction'
    elif change=='unknown_conflict':
        data['rows'][0]['coverage']='unknown'
        data['rows'][0]['evidence'][0]['state']='contradiction'
    else:
        doc['blocks']=[{'id':'h1','text':'First Employer'},{'id':'b1','text':'Built a workflow prototype for inventory.'},
                       {'id':'h2','text':'Second Employer'},{'id':'b2','text':'Tuned and tested retrieval.'},doc['blocks'][2]]
        profile['experiences']=[{'id':eid,'organization':org,'role':'Intern','kind':'internship','start':None,'end':None,
                                'summary':'','companyGroup':None,'evidence':[{'blockId':hid,'quote':org}]} for eid,org,hid in [('e1','First Employer','h1'),('e2','Second Employer','h2')]]
        data['rows'][0]['evidence'][0]['claim']='Second Employer构建了库存流程原型。'
    provider=RowStub(data)
    with pytest.raises(WorkflowError,match='Deterministic validation did not pass'):
        Workflow(provider,max_repairs=0).analyze(doc,standards,hat,'r','c',profile=profile)
    assert provider.roles==['match']


def test_rows_unknown_is_not_mismatch_and_fewer_real_goals_are_not_padded(row_case):
    changed=deepcopy(row_case);changed[4]['rows'][2]['unknowns']=[]
    analysis,_,targets,_,errors=project_rows(changed[4],changed[1],changed[3])
    assert len(targets)==2 and any('do not invent gaps' in e for e in errors)
    assert all(p['kind']!='gap' for p in analysis['concerns'])
    assert analysis['observations'][-1]['coverage']=='unknown'
    provider=RowStub(changed[4])
    with pytest.raises(WorkflowError):Workflow(provider,max_repairs=0).analyze(*changed[:3],'r','c',profile=changed[3])
    assert provider.roles==['match']


def test_row_review_current_subjects_and_retained_unknowns(row_case):
    data=row_case[4];standard=row_case[1]
    review={'approved':False,'findings':[{'criterionId':'duty','section':'unknown','index':0,
        'statement':data['rows'][0]['unknowns'][0]['singleGoal'],'requirementQuote':'对接HR系统','evidence':[],
        'severity':'blocker','message':'Offline rejecting sentinel; do not auto-ignore review objections.'}]}
    assert not validate_row_review(review,data,standard)
    bad=deepcopy(review);bad['findings'][0]['statement']='Old or cropped goal'
    assert validate_row_review(bad,data,standard)
    assert validate_row_review({'approved':False,'findings':[]},data,standard)
    class Reject(RowStub):
        def generate(self,role,payload,schema):
            if role=='review':
                self.roles.append(role)
                assert len(payload['criterionEvidence']['rows'])==3
                return review
            return super().generate(role,payload,schema)
    provider=Reject(data)
    with pytest.raises(WorkflowError,match='Evidence review did not pass'):
        Workflow(provider,max_repairs=0).analyze(*row_case[:3],'r','c',profile=row_case[3])
    assert provider.roles==['match','review']


def test_template_migration_is_explicit_and_custom_tasks_are_not_overwritten(row_case):
    from hr_backend.agent_settings import snapshot_settings
    provider=RowStub(row_case[4]);state={};snapshot=snapshot_settings(state,provider)
    original=deepcopy(snapshot)
    snapshot['roles']['match']['systemPrompt']=LEGACY_ROW_TASKS['match']
    snapshot['roles']['review']['systemPrompt']='User custom review instruction'
    before=deepcopy(snapshot);report=row_template_migration(snapshot)
    assert snapshot==before
    assert report['roles']['match']['status']=='known_previous_default'
    assert report['roles']['review']['status']=='custom_requires_review'
    assert report['automaticMigration'] is False and report['roles']['match']['diff']
    workflow=Workflow(provider,max_repairs=0);workflow.settings_resolver=lambda _:snapshot
    with pytest.raises(WorkflowError,match='explicit reviewed'):
        workflow.analyze(*row_case[:3],'r','c',profile=row_case[3])
    assert provider.roles==[]
    workflow.settings_resolver=lambda _:original
    assert workflow.analyze(*row_case[:3],'r','c',profile=row_case[3])['reviewed']
    assert original['roles']['match']['systemPrompt'].startswith(ROW_TEMPLATE_VERSION)


def test_rows_api_store_restart_source_read_and_mixed_version_guard(row_case,tmp_path):
    from fastapi.testclient import TestClient
    from hr_backend.app import create_app
    doc,standards,hat,profile,data=row_case
    def parser(path,output_dir,document_id):
        return {**deepcopy(doc),'id':document_id,'filename':'row.pdf','mimeType':'application/pdf','previewKind':'text','pages':[],'warnings':[]}
    provider=RowStub(data);client=TestClient(create_app(tmp_path,provider,parser))
    client.put('/api/standards/draft',json={k:standards[k] for k in ('jdText','criteria')})
    confirmed=client.post('/api/standards/confirm',json={'actor':'offline rows test'}).json()['standards']
    upload=client.post('/api/candidates',files={'files':('row.pdf',b'%PDF offline row fixture','application/pdf')}).json()
    cid=upload['candidateIds'][0];detail=client.get('/api/candidates/'+cid).json()
    assert detail['assessment']['reviewed'] and len(detail['assessment']['questions'])==3
    assert detail['assessment']['standardsVersion']==confirmed['version']['id']
    assert client.get(detail['document']['originalUrl']).content==b'%PDF offline row fixture'
    reopened=TestClient(create_app(tmp_path,RowStub(data),parser))
    assert reopened.get('/api/candidates/'+cid).json()['assessment']==detail['assessment']
    state=reopened.app.state.store.read();assessment=state['assessments'][detail['assessment']['id']]
    assert assessment['standardsVersion']==state['standards']['version']['id']
    # A mismatched current HAT cannot expose the saved assessment as current.
    with reopened.app.state.store.transaction() as current:
        current['hat']['version']['id']='different-confirmed-hat'
    assert reopened.get('/api/candidates/'+cid).json()['candidate']['grade']=='pending'


def test_fully_shared_partial_row_does_not_upgrade_display_support(row_case):
    changed=deepcopy(row_case);data=changed[4]
    data['rows'][0]['coverage']='supported'
    data['rows'][1]['evidence']=[{**deepcopy(data['rows'][0]['evidence'][0]),'requirementQuote':'测试检索效果'}]
    analysis,grounding,_,_,errors=project_rows(data,changed[1],changed[3])
    assert errors==['Fewer than three distinct eligible unresolved goals; do not invent gaps to fill slots']
    analysis=compose_point_explanations(analysis,grounding)
    assert len(analysis['matches'])==1
    assert analysis['matches'][0]['criterionIds']==['duty','improve']
    assert '部分支持' in analysis['matches'][0]['explanation']
    assert [o['coverage'] for o in analysis['observations']]==['supported','partial','unknown']


def test_row_single_oversized_claim_returns_actionable_error_without_empty_match(row_case):
    doc,standards,hat,profile,data=deepcopy(row_case)
    # Direct projection guard also handles imported/legacy oversized values;
    # current live schema already bounds an individual claim to180 characters.
    long_claim='保留原文中的研究、提议和现场试点限定，不能表述为已实现的生产收益。'*16
    data['rows'][0]['evidence'][0]['claim']=long_claim
    before=deepcopy(data)
    analysis,grounding,_,_,errors=project_rows(data,standards,profile)
    assert any('No complete source-backed claim fits' in e and 'duty' in e for e in errors)
    assert all(p['criterionIds']!=['duty'] for p in analysis['matches'])
    assert all(g['claims'] for g in grounding)
    assert long_claim in analysis['observations'][0]['explanation']
    assert data==before  # Neither source-backed wording nor qualifications were cut.


def test_row_only_narrated_facts_get_owner_so_later_criterion_can_display_remainder(row_case):
    changed=deepcopy(row_case);data=changed[4]
    first='首项实际活动。'+'甲'*155+'；属于研究而不是生产交付。'
    second='另一项实际活动。'+'乙'*155+'；收益仍为提议而非已经实现。'
    assert len(first)<=180 and len(second)<=180
    a=deepcopy(data['rows'][0]['evidence'][0]);a['claim']=first
    b=deepcopy(data['rows'][1]['evidence'][0]);b['claim']=second
    data['rows'][0]['evidence']=[a,{**deepcopy(b),'requirementQuote':'梳理业务流程'}]
    data['rows'][1]['evidence']=[b]
    analysis,grounding,_,_=row_render(changed)
    assert [g['claims'][0]['claim'] for g in grounding]==[first,second]
    assert all(len(g['claims'])==1 for g in grounding)
    assert analysis['matches'][0]['criterionIds']==['duty']
    assert analysis['matches'][1]['criterionIds']==['improve']
    assert {r['blockId'] for r in analysis['matches'][0]['evidence']}=={'b1','b2'}
    assert second in analysis['matches'][1]['explanation']
    assert len(analysis['matches'][0]['explanation'])<=360


def test_unknown_row_keeps_sourced_facts_without_positive_points_or_score(row_case):
    from hr_backend.policy import rank
    changed=deepcopy(row_case);data=changed[4];data['rows'][0]['coverage']='unknown'
    baseline=[{'criterionId':r['criterionId'],'coverage':r['coverage']} for r in data['rows']]
    analysis,grounding,_,_=row_render(changed)
    row=next(o for o in analysis['observations'] if o['criterionId']=='duty')
    assert row['coverage']=='unknown' and row['evidence']==data['rows'][0]['evidence'][0]['evidence']
    assert data['rows'][0]['evidence'][0]['claim'] in row['explanation']
    assert '保留相关事实不表示满足标准片段' in row['explanation']
    assert not any('duty' in p['criterionIds'] for p in analysis['matches'])
    assert rank(changed[1]['criteria'],analysis['observations'])==rank(changed[1]['criteria'],baseline)
    provider=RowStub(data)
    assert Workflow(provider,max_repairs=0).analyze(*changed[:3],'r','c',profile=changed[3])['reviewed']
    assert provider.roles==['match','review','interview']


def test_false_hr_support_still_fails_independent_review_after_unknown_gate_relaxed(row_case):
    doc,standards,hat,profile,data=deepcopy(row_case)
    # A literal source quote exists, but the candidate claim/domain assertion is false.
    prior=data['rows'][-1];prior['coverage']='partial'
    prior['evidence']=[{'requirementQuote':'构建HR助手的实操经验','state':'partial',
        'claim':'构建了HR助手。','evidence':[{'blockId':'b1','quote':'Built a workflow prototype for inventory.'}]}]
    review={'approved':False,'findings':[{'criterionId':'prior','section':'evidence','index':0,
        'statement':prior['evidence'][0]['claim'],'requirementQuote':'构建HR助手的实操经验',
        'evidence':prior['evidence'][0]['evidence'],'severity':'blocker',
        'message':'库存流程原型不证明HR助手经历，不能用于该既有HR资格的partial支持。'}]}
    class RejectHR(RowStub):
        def generate(self,role,payload,schema):
            if role=='review':self.roles.append(role);return review
            return super().generate(role,payload,schema)
    provider=RejectHR(data);events=[]
    with pytest.raises(WorkflowError,match='Evidence review did not pass'):
        Workflow(provider,max_repairs=0).analyze(doc,standards,hat,'r','c',profile=profile,trace_callback=events.append)
    assert provider.roles==['match','review']
    decision=next(e['_diagnostic'] for e in events if e.get('_diagnostic',{}).get('kind')=='review_decision')
    assert not decision['publishable'] and decision['findings']==review['findings']


def test_unknown_default_task_revision_is_explicit_and_does_not_mutate_saved_tasks():
    from hr_backend.prompts import PREVIOUS_ROW_TASKS,ROLE_INSTRUCTIONS,UNKNOWN_ROW_TASK_RULE
    config={'roles':{r:{'systemPrompt':text} for r,text in PREVIOUS_ROW_TASKS.items()}}
    original=deepcopy(config);proposal=row_template_migration(config)
    assert config==original and proposal['automaticMigration'] is False
    for role in ('match','review'):
        assert proposal['roles'][role]['status']=='known_previous_default'
        assert ROLE_INSTRUCTIONS[role]==PREVIOUS_ROW_TASKS[role]+UNKNOWN_ROW_TASK_RULE
        assert 'never 非HR' in ROLE_INSTRUCTIONS[role]
        assert 'does not mean the whole' in ROLE_INSTRUCTIONS[role]
    config['roles']['match']['systemPrompt']+=' Custom user rule'
    assert row_template_migration(config)['roles']['match']['status']=='custom_requires_review'

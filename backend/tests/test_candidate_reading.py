"""Offline semantic-protocol regression; paid-model tests are recorded separately."""
from copy import deepcopy
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from hr_backend.app import create_app
from hr_backend.schemas import CriterionRowsResult
from hr_backend.structured_evidence import project_rows,validate_row_review
from hr_backend.outcome_reading import READING_VERSION
from hr_backend.workflow import Workflow


def case():
    refs=[{'blockId':'alpha','quote':'Built an inventory workflow prototype at Alpha.'},{'blockId':'beta','quote':'Tuned retrieval evaluation at Beta.'}]
    doc={'id':'doc','status':'ready','blocks':[{'id':r['blockId'],'text':r['quote'],'page':1,'box':None,'method':'native','confidence':None} for r in refs]}
    criteria=[dict(id='build',title='工作流职责',description='构建AI工作流并对接HR系统。',jdQuote='构建AI工作流并对接HR系统。',group='must',priority=0),dict(id='prior',title='既有HR经验',description='优先具备HR Agent实操经历。',jdQuote='优先具备HR Agent实操经历。',group='bonus',priority=0)]
    standards={'version':{'id':'std','confirmedAt':'2026-01-01'},'jdText':'\n'.join(c['description'] for c in criteria),'criteria':criteria}
    profile={'name':'Fictional Example','education':[],'languages':[],'availability':None,'experiences':[dict(id=eid,organization=org,kind='internship',role='Intern',start=None,end=None,summary='',companyGroup=None,evidence=[ref]) for eid,org,ref in zip(['a','b'],['Alpha','Beta'],refs)]}
    data={'rows':[{'criterionId':'build','coverage':'partial','conclusion':'有工作流与检索实践','evidence':[{'requirementQuote':'构建AI工作流','state':'partial','claim':claim,'scope':'实际项目组成能力，HR系统对接仍未知。','evidence':[ref]} for claim,ref in zip(['在Alpha构建了库存工作流原型。','在Beta调优了检索评估。'],refs)],'unknowns':[{'requirementQuote':'对接HR系统','singleGoal':'是否做过HR系统对接？','conclusion':'HR系统对接经历待确认','experienceId':None,'evidence':[]}]},
        {'criterionId':'prior','coverage':'unknown','conclusion':None,'evidence':[],'unknowns':[{'requirementQuote':'HR Agent实操经历','singleGoal':'是否有直接的HR Agent项目经历？','conclusion':'HR Agent实际项目经历待确认','experienceId':None,'evidence':[]}]}],
        'reading':{'strength':'有工作流原型与检索实践','strengthSources':[{'criterionId':'build','index':0},{'criterionId':'build','index':1}], 'gap':'HR系统对接经历待确认','gapSource':{'criterionId':'build','index':0},
        'interviewChoices':[{'criterionId':'build','section':'evidence','index':0,'objective':'contribution','singleGoal':'在Alpha库存工作流原型中亲自实现了哪一部分？','conclusion':'原型的个人贡献待核实','decisionReason':'核实原型开发中实际独立实现的工作。'},
        {'criterionId':'build','section':'evidence','index':1,'objective':'validation','singleGoal':'在Beta用什么方式验证检索调优有效？','conclusion':'检索调优效果待核实','decisionReason':'区分实际检索验证与泛泛工具使用。'},
        {'criterionId':'build','section':'unknown','index':0,'objective':'existence','singleGoal':None,'conclusion':None,'decisionReason':'HR系统实际对接仍是核心未决点。'}]}}
    return doc,standards,{'version':{'id':'hat','confirmedAt':'2026-01-01'},'rules':[]},profile,CriterionRowsResult.model_validate(data).model_dump()


def checks(data):
    return {'approved':True,'findings':[],'checks':[dict(criterionId=r['criterionId'],facts='pass',scope='pass',coverage='pass',unknowns='pass') for r in data['rows']], 'reading':{'conclusions':'pass','summary':'pass','interviewSelection':'pass','issues':[]}}


class Provider:
    mode='live';configured=True;model='offline-test';evidence_protocol=3;max_input_bytes=256000
    def __init__(self,data,profile):self.data=data;self.profile=profile;self.roles=[]
    def generate(self,role,payload,schema):
        self.roles.append(role)
        if role=='cv':return deepcopy(self.profile)
        if role=='match':return deepcopy(self.data)
        if role=='review':return checks(payload['criterionEvidence'])
        if role=='interview':
            return {'questions':[dict(id='q'+str(i),question=p['primaryGoal'],purpose=p['primaryGoal'],pointIds=[p['pointId']],criterionIds=[p['criterionId']],evidence=p['allowedEvidence']) for i,p in enumerate(payload['questionPlan'])]}
        raise AssertionError(role)


def test_candidate_conclusions_summary_and_distinct_actual_experience_selection():
    doc,std,hat,profile,data=case();before=deepcopy(data)
    analysis,grounding,targets,selected,errors=project_rows(data,std,profile)
    assert not errors and data==before
    assert analysis['matches'][0]['title']=='有工作流与检索实践'
    assert analysis['matches'][0]['title']!=std['criteria'][0]['title']
    assert analysis['summary']=='有工作流原型与检索实践，但HR系统对接经历待确认。'
    assert len(analysis['summary'])<=46 and analysis['_readingVersion']==READING_VERSION
    assert [t['experienceId'] for t in targets]==['a','b',None]
    assert [t['criterionId'] for t in targets]==['build']*3
    assert all(p['title'] not in [c['title'] for c in std['criteria']] for p in analysis['concerns'])
    assert [o['coverage'] for o in analysis['observations']]==['partial','unknown']
    assert len(analysis['matches'][0]['evidence'])==2
    provider=Provider(data,profile)
    result=Workflow(provider,max_repairs=0).analyze(doc,std,hat,'r','c',profile=profile)
    assert result['reviewed'] and len(result['questions'])==3
    assert provider.roles==['match','review','interview']
    assert len({q['question'] for q in result['questions']})==3


def test_interview_receives_reviewed_scope_without_promoting_unknown_results():
    from hr_backend.evidence import interview_plan,interview_context
    doc,std,hat,profile,data=case()
    data['rows'][0]['unknowns'][0].update(conclusion='部署及HR系统对接待确认',singleGoal='是否部署并对接过HR系统？')
    analysis,_,targets,_,errors=project_rows(data,std,profile)
    assert not errors
    from hr_backend.evidence import compose_point_explanations
    _,grounding,_,_,_=project_rows(data,std,profile)
    analysis=compose_point_explanations(analysis,grounding)
    before=deepcopy(analysis)
    plans=interview_plan(analysis,std,profile,targets)
    for plan in plans:
        point=next(p for p in analysis['matches']+analysis['concerns'] if p['id']==plan['pointId'])
        assert plan['assessmentScope']['point']==point['explanation']
        assert plan['assessmentScope']['criterion']==analysis['observations'][0]['explanation']
        assert '部署' in plan['assessmentScope']['point']
    context=interview_context(doc,profile,analysis,plans)
    assert analysis==before
    assert context['analysis']['observations']==analysis['observations'][:1]
    assert plans[0]['allowedEvidence']==targets[0]['evidence']
    assert plans[0]['primaryGoal']==targets[0]['singleGoal']


def test_every_distinct_reviewed_unknown_remains_visible_without_changing_coverage():
    from hr_backend.workflow import validate_presentation
    doc,std,hat,profile,data=case()
    gaps=data['rows'][0]['unknowns']
    for i in range(5):
        gaps.append(dict(requirementQuote='对接HR系统',singleGoal=f'独立缺失答案{i}？',conclusion=f'缺失答案{i}待确认',experienceId=None,evidence=[]))
    before=deepcopy(data)
    analysis,_,targets,_,errors=project_rows(data,std,profile)
    assert not errors and data==before
    displayed={p['explanation'].removeprefix('待确认：') for p in analysis['concerns']}
    expected={g['singleGoal'] for row in data['rows'] for g in row['unknowns']}
    assert displayed==expected and len(analysis['concerns'])>4
    assert not validate_presentation(analysis)
    assert [o['coverage'] for o in analysis['observations']]==['partial','unknown']
    assert len(targets)==3 and analysis['summary']=='有工作流原型与检索实践，但HR系统对接经历待确认。'


def test_identical_unknown_answer_merges_only_its_criterion_links_not_other_gaps():
    doc,std,hat,profile,data=case()
    std['criteria'][1]['description']+='并有对接HR系统经历。'
    std['criteria'][1]['jdQuote']=std['criteria'][1]['description']
    std['jdText']='\n'.join(c['description'] for c in std['criteria'])
    data['rows'][1]['unknowns'].append(deepcopy(data['rows'][0]['unknowns'][0]))
    analysis,_,_,_,errors=project_rows(data,std,profile)
    assert not errors
    same=[p for p in analysis['concerns'] if p['explanation']=='待确认：是否做过HR系统对接？']
    assert len(same)==1 and same[0]['criterionIds']==['build','prior']
    assert any(p['title']=='HR Agent实际项目经历待确认' for p in analysis['concerns'])


def test_context_basis_and_sources_are_accessible_once_without_positive_coverage():
    doc,std,hat,profile,data=case()
    related={'requirementQuote':'HR Agent实操经历','state':'context','claim':'参与通用库存原型的用户培训材料准备。','scope':'只证明通用培训准备，HR Agent采用仍未说明。','evidence':[deepcopy(doc['blocks'][0]) ]}
    related['evidence']=[{'blockId':'alpha','quote':doc['blocks'][0]['text']}]
    data['rows'][1]['evidence']=[related]
    data['rows'][1]['unknowns'].append(dict(data['rows'][1]['unknowns'][0],conclusion='HR采用方式待确认',singleGoal='如何支持HR团队采用？'))
    analysis,_,_,_,errors=project_rows(data,std,profile)
    assert not errors
    shown=[p for p in analysis['concerns'] if '参与通用库存原型' in p['explanation']]
    assert len(shown)==1 and 'HR Agent采用仍未说明' in shown[0]['explanation']
    assert shown[0]['evidence']==related['evidence']
    assert not any('prior' in p['criterionIds'] for p in analysis['matches'])
    assert next(o for o in analysis['observations'] if o['criterionId']=='prior')['coverage']=='unknown'


def test_literal_compound_clauses_enter_both_match_and_review_payloads():
    from hr_backend.structured_evidence import row_payload
    doc,std,hat,profile,data=case()
    std['criteria'][0]['description']='Track usage; optimize workflows; ensure privacy'
    std['criteria'][0]['jdQuote']='Track usage; optimize workflows; ensure privacy; Cantonese is a separate bonus'
    payload=row_payload(doc,std,hat,{},[],profile)
    assert payload['compoundRequirementClauses']==[{'criterionId':'build','basis':'confirmed_description','clauses':['Track usage','optimize workflows','ensure privacy']}]
    assert payload['standards']['criteria']==std['criteria']
    assert payload['confirmedRequirementScope'][0]['requirement']==std['criteria'][0]['description']
    assert 'Cantonese' not in payload['confirmedRequirementScope'][0]['requirement']


def test_diagnosed_seed_reaches_first_real_matching_request_with_provenance():
    doc,std,hat,profile,data=case()
    class Capture(Provider):
        def generate(self,role,payload,schema):
            if role=='match':self.request=deepcopy(payload)
            return super().generate(role,payload,schema)
    provider=Capture(data,profile);workflow=Workflow(provider,max_repairs=0)
    source={'sourceRunId':'checked-prior-run','sourceArtifactSHA256':'checked-digest'}
    state={'profile':profile,'structuredEvidence':data,'errors':['Correct one source-bound current defect.'],'repairSource':source}
    result=workflow._match_rows(doc,std,hat,{'runId':'new-run','candidateId':'candidate'},[],state,None)
    assert not result['errors']
    assert provider.roles==['match']
    assert provider.request['previousRows']==data and provider.request['repairIssues']==state['errors']
    assert provider.request['repairSource']==source and provider.request['context']['runId']=='new-run'


@pytest.mark.parametrize('mutation,message',[
    (lambda d:d['reading']['strengthSources'][0].update(index=999),'strengthSources'),
    (lambda d:d['rows'][0].update(conclusion='工作流职责'),'conclusion'),
    (lambda d:d['rows'][0]['unknowns'][0].update(conclusion=''),'conclusion'),
    (lambda d:d['reading'].update(gapSource=None),'gapSource'),
    (lambda d:d['reading'].update(strength='实'*26,gap='缺'*22),'<=46'),
    (lambda d:d['reading']['interviewChoices'][1].update(index=0),'different'),
])
def test_invalid_recipes_fail_before_model_review(mutation,message):
    doc,std,hat,profile,data=case();mutation(data)
    errors=project_rows(data,std,profile)[-1]
    assert errors and any(message in error for error in errors),errors


def test_no_material_gap_does_not_force_a_missing_claim_or_invent_unknown():
    doc,std,hat,profile,data=case()
    data['reading']['gap']=None;data['reading']['gapSource']=None
    analysis,_,_,_,errors=project_rows(data,std,profile)
    assert not errors and analysis['summary']=='有工作流原型与检索实践。'


def test_independent_reading_review_cannot_be_omitted_or_disregarded():
    doc,std,hat,profile,data=case();review=checks(data)
    assert not validate_row_review(review,data,std,require_checks=True)
    review['reading']=None
    assert validate_row_review(review,data,std,require_checks=True)
    review=checks(data);review['reading'].update(summary='block',issues=['reading.strength invents production deployment.'])
    assert validate_row_review(review,data,std,require_checks=True)
    review['approved']=False
    assert not validate_row_review(review,data,std,require_checks=True)


def test_api_persistence_projection_preserves_reviewed_short_summary_and_originals(tmp_path):
    doc,std,hat,profile,data=case();provider=Provider(data,profile)
    def parser(path,out,did):
        return {**deepcopy(doc),'id':did,'mimeType':'application/pdf','previewKind':'text','pages':[],'warnings':[]}
    app=create_app(tmp_path,provider,parser);client=TestClient(app)
    client.put('/api/standards/draft',json={k:std[k] for k in ('jdText','criteria')});client.post('/api/standards/confirm',json={'actor':'test'})
    uploaded=client.post('/api/candidates',files={'files':('fictional.pdf',b'offline test original','application/pdf')}).json();cid=uploaded['candidateIds'][0]
    detail=client.get('/api/candidates/'+cid).json();assert detail['assessment'],detail
    summary=detail['assessment']['summary'];assert detail['candidate']['display']['summary']==summary
    assert '_readingVersion' not in detail['assessment']
    assert detail['assessment']['matches'][0]['title']=='有工作流与检索实践'
    reopened=TestClient(create_app(tmp_path,provider,parser))
    persisted=reopened.get('/api/candidates/'+cid).json()
    assert persisted['candidate']['display']['summary']==summary
    assert persisted['assessment']==detail['assessment']
    assert reopened.get(persisted['document']['originalUrl']).content==b'offline test original'


def test_deepseek_nullable_object_union_transport_keeps_original_validation():
    from hr_backend.response_schema import response_schema
    from openai.lib._pydantic import to_strict_json_schema
    schema=response_schema(CriterionRowsResult)
    wire=to_strict_json_schema(schema)
    def walk(value):
        if isinstance(value,list):
            for child in value:walk(child)
        elif isinstance(value,dict):
            for branch in value.get('anyOf',[]):assert 'type' in branch
            for child in value.values():walk(child)
    walk(wire)
    data=case()[-1]
    assert schema.model_validate(data).model_dump()==CriterionRowsResult.model_validate(data).model_dump()
    assert response_schema(CriterionRowsResult) is schema


def test_review_scope_finding_binds_scope_not_a_correct_fact():
    doc,std,hat,profile,data=case()
    item=data['rows'][0]['evidence'][0]
    item['scope']='证明独立从零部署了HR Agent。'
    review=checks(data);review['approved']=False
    review['checks'][0]['scope']='block'
    review['findings']=[{'criterionId':'build','section':'scope','index':0,'statement':item['scope'],
        'requirementQuote':item['requirementQuote'],'evidence':item['evidence'],'severity':'blocker',
        'message':'Scope adds independent/from-scratch HR deployment not present in the prototype source; preserve the correct claim.'}]
    assert not validate_row_review(review,data,std,require_checks=True)
    review['findings'][0]['statement']=item['claim']
    assert validate_row_review(review,data,std,require_checks=True)


def test_review_can_report_a_missing_requirement_against_exact_current_unknown_list():
    doc,std,hat,profile,data=case();review=checks(data)
    std['criteria'][0]['description']+='并监控失败案例。';std['criteria'][0]['jdQuote']=std['criteria'][0]['description']
    review['approved']=False;review['checks'][0]['unknowns']='block'
    review['findings']=[{'criterionId':'build','section':'unknown','index':None,
        'statement':"build unknowns: 'HR系统对接经历待确认'",'requirementQuote':'监控失败案例',
        'evidence':[],'severity':'blocker','message':'The current unknown list omits failure monitoring; add that confirmed missing answer without changing evidence coverage.'}]
    assert not validate_row_review(review,data,std,require_checks=True)
    review['findings'][0]['statement']="build unknowns: 'A stale or invented title'"
    assert validate_row_review(review,data,std,require_checks=True)
    review['findings'][0]['statement']="build unknowns: 'HR系统对接经历待确认'"
    review['findings'][0]['requirementQuote']='A foreign requirement'
    assert validate_row_review(review,data,std,require_checks=True)
    review['findings'][0]['requirementQuote']='监控失败案例'
    data['rows'][0]['unknowns'].append(dict(data['rows'][0]['unknowns'][0],conclusion='另一项待确认',singleGoal='另一个独立缺失答案？'))
    assert validate_row_review(review,data,std,require_checks=True)  # Incomplete lists cannot masquerade as the current list.


def test_goal_objective_is_required_in_wire_schema_not_nullable():
    from pydantic import ValidationError
    from hr_backend.response_schema import response_schema
    schema=response_schema(CriterionRowsResult).model_json_schema()
    field=schema['$defs']['ReadingInterviewChoice']['properties']['objective']
    assert field['type']=='string' and 'anyOf' not in field
    data=case()[-1];data['reading']['interviewChoices'][-1]['objective']=None
    with pytest.raises(ValidationError):CriterionRowsResult.model_validate(data)


def test_validation_method_is_one_goal_but_an_appended_verification_is_not():
    from hr_backend.outcome_reading import objective_errors
    assert not objective_errors('确认如何验证数学模型与算法输出的一致性并迭代修正','method')
    assert not objective_errors('在该项目中，你如何验证检索效果？','method')
    assert objective_errors('你如何选择并编排LLM，效果如何验证？','method')
    assert objective_errors('你具体负责了哪些模块？你如何验证效果？','contribution')


def test_explicit_personal_implementation_is_a_valid_contribution_probe():
    from hr_backend.evidence import interview_plan,validate_interview_focus
    doc,std,hat,profile,data=case()
    analysis,_,targets,_,errors=project_rows(data,std,profile)
    assert not errors
    plans=interview_plan(analysis,std,profile,targets)
    questions=[dict(id=str(i),pointIds=[p['pointId']],criterionIds=[p['criterionId']],question=p['primaryGoal'],evidence=p['allowedEvidence']) for i,p in enumerate(plans)]
    questions[0]['question']='在 Alpha 的库存原型中，你本人具体设计和实现了哪些模块或架构？'
    assert not validate_interview_focus(questions,plans,profile)
    questions[0]['question']='在 Alpha 的库存原型中，系统用了什么架构？'
    assert any('verification angle' in e for e in validate_interview_focus(questions,plans,profile))


def test_exact_reference_resolution_never_invents_candidate_text_or_changes_coverage():
    from hr_backend.source_references import resolve_row_references
    from hr_backend.workflow import validate_refs
    doc,std,hat,profile,data=case()
    doc['blocks']=[{'id':'alpha','text':'Planned the project scope, ','page':1},{'id':'continuation','text':'then designed the user journey.','page':1}]
    item=data['rows'][0]['evidence'][0];item['evidence']=[{'blockId':'alpha','quote':'Planned the project scope, then designed the user journey.'}]
    before=deepcopy(data);resolved,repairs=resolve_row_references(data,doc,std)
    assert data==before and resolved['rows'][0]['coverage']==data['rows'][0]['coverage']
    assert len(resolved['rows'][0]['evidence'][0]['evidence'])==2 and repairs
    assert not validate_refs(resolved['rows'][0]['evidence'][0],doc)
    item['evidence'][0]['quote']='Planned and independently deployed an HR Agent.'
    rejected,_=resolve_row_references(data,doc,std)
    assert validate_refs(rejected['rows'][0]['evidence'][0],doc)


def test_disjoint_requirement_reference_can_only_expand_to_its_own_exact_source():
    from hr_backend.source_references import resolve_row_references
    doc,std,hat,profile,data=case()
    own='Detail-oriented and organized, with strong problem-solving skills and a builder mindset, and fast adaptability'
    std['criteria'][0].update(description=own,jdQuote=own)
    item=data['rows'][0]['evidence'][0];item['requirementQuote']='Detail-oriented and organized, with fast adaptability'
    resolved,repairs=resolve_row_references(data,doc,std)
    assert resolved['rows'][0]['evidence'][0]['requirementQuote']==own
    assert resolved['rows'][0]['evidence'][0]['claim']==item['claim'] and repairs
    item['requirementQuote']='A foreign Python certification requirement'
    rejected,repairs=resolve_row_references(data,doc,std)
    assert rejected['rows'][0]['evidence'][0]['requirementQuote']==item['requirementQuote']


def test_typographic_apostrophe_resolution_returns_exact_source_not_normalized_text():
    from hr_backend.source_references import resolve_row_references
    from hr_backend.workflow import validate_refs
    doc,std,hat,profile,data=case()
    doc['blocks']=[{'id':'alpha','text':'Prepared the application’s workflow.','page':1}]
    data['rows'][0]['evidence'][0]['evidence']=[{'blockId':'alpha','quote':"Prepared the application's workflow."}]
    fixed,repairs=resolve_row_references(data,doc,std)
    assert fixed['rows'][0]['evidence'][0]['evidence'][0]['quote']=='Prepared the application’s workflow.'
    assert repairs and not validate_refs(fixed['rows'][0]['evidence'][0],doc)


def test_bullet_address_resolves_only_exact_immediate_same_page_text():
    from hr_backend.source_references import resolve_row_references
    from hr_backend.workflow import validate_refs
    doc,std,hat,profile,data=case()
    doc['blocks']=[{'id':'bullet','text':'• ','page':1},{'id':'alpha','text':'Presented an inventory proposal.','page':1}]
    item=data['rows'][0]['evidence'][0];item['evidence']=[{'blockId':'bullet','quote':'Presented an inventory proposal.'}]
    fixed,repairs=resolve_row_references(data,doc,std)
    assert fixed['rows'][0]['evidence'][0]['evidence']==[{'blockId':'alpha','quote':doc['blocks'][1]['text']}]
    assert any(r['kind']=='adjacent_bullet_quote' for r in repairs)
    assert not validate_refs(fixed['rows'][0]['evidence'][0],doc)
    assert fixed['rows'][0]['coverage']==data['rows'][0]['coverage']
    for invalid in ('Presented an independently deployed HR system.','Presented an inventory proposal.'):
        item['evidence'][0]['quote']=invalid
        doc['blocks'][1]['page']=2
        rejected,_=resolve_row_references(data,doc,std)
        assert validate_refs(rejected['rows'][0]['evidence'][0],doc)
    doc['blocks'][1]['page']=1;doc['blocks'].insert(1,{'id':'different','text':'Different activity.','page':1})
    rejected,_=resolve_row_references(data,doc,std)
    assert validate_refs(rejected['rows'][0]['evidence'][0],doc)


def test_multifact_basis_uses_remaining_row_unknowns_not_first_fact_scope():
    from hr_backend.evidence import compose_point_explanations
    doc,std,hat,profile,data=case()
    data['rows'][0]['evidence'][0]['scope']='第一项事实未说明检索能力。'
    data['rows'][0]['evidence'][1]['scope']='第二项事实支持检索能力。'
    analysis,grounding,_,_,errors=project_rows(data,std,profile)
    assert not errors
    rendered=compose_point_explanations(analysis,grounding)
    assert '未说明检索能力' not in rendered['matches'][0]['explanation']
    assert 'HR系统对接经历待确认' in rendered['matches'][0]['explanation']

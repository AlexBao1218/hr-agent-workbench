"""QA's synthetic boundary cases, independently reproduced; no QA file writes."""
from hr_backend.evidence import capability_index,supplement_capability_sources,validate_capability_coverage,interview_plan,validate_interview_focus


def setup():
    refs=[{'blockId':'a','quote':'Built a RAG retrieval prototype.'},{'blockId':'b','quote':'Tested a document search service.'}]
    profile={'education':[],'experiences':[{'id':'ea','organization':'Alpha (ALP)','kind':'internship','evidence':[refs[0]]},{'id':'eb','organization':'Beta (BTA)','kind':'internship','evidence':[refs[1]]}]}
    plans=[{'slot':i+1,'pointId':f'p{i}','criterionId':f'c{i}','primaryGoal':f'Goal {i}','experience':{'id':'ea','organization':'Alpha (ALP)','kind':'internship'},'allowedEvidence':[refs[0]]} for i in range(3)]
    def qs(first):return [{'pointIds':[f'p{i}'],'criterionIds':[f'c{i}'],'question':first if i==0 else f'At ALP how did you validate stage {i}?','evidence':[refs[0]]} for i in range(3)]
    return refs,profile,plans,qs

def test_negative_ambiguous_unowned_and_hypothetical_sources_are_context_only():
    refs,profile,_,_=setup()
    blocks=[{'id':'a','text':refs[0]['quote']},{'id':'neg','text':'I have never developed RAG systems.'},{'id':'status','text':'Developed a RAG (red amber green) status board.'},{'id':'future','text':'I would develop RAG systems.'},{'id':'unowned','text':'Built a RAG assistant.'}]
    # Even known ownership does not make negation or acronym ambiguity positive evidence.
    profile['experiences'][0]['evidence'] += [{'blockId':b['id'],'quote':b['text']} for b in blocks if b['id'] in ('neg','status','future')]
    index=capability_index({'blocks':blocks},profile)
    assert {s['blockId'] for s in index['rag']}=={'a'}
    assert {s['blockId'] for s in index['contextOnly']}=={'neg','status','future','unowned'}
    assert all('scope' not in s for s in index['contextOnly'])
    base={'matches':[{'id':'m','title':'RAG','evidence':[refs[0]]}]}
    result,added=supplement_capability_sources(base,index)
    assert not added and not validate_capability_coverage(result,index)
    assert {r['blockId'] for r in result['matches'][0]['evidence']}=={'a'}

def test_known_other_organization_or_alias_in_question_text_is_rejected():
    _,profile,plans,qs=setup()
    for text in ['At ALP and Beta (BTA), how did you deploy the same system for both employers?','At ALP and BTA, how did you deploy the same system?']:
        errors=validate_interview_focus(qs(text),plans,profile)
        assert any('another known experience' in e for e in errors)


def test_assigned_name_containing_another_employers_alias_is_not_cross_experience():
    _,profile,plans,qs=setup()
    profile['experiences'][0]['organization']='China Modeling Contest (Example Cup)'
    profile['experiences'][1]['organization']='Beta (China) Ltd.'
    for plan in plans:plan['experience']['organization']='China Modeling Contest (Example Cup)'
    questions=qs('In China Modeling Contest (Example Cup), how did you build the model?')
    for question in questions:question['question']='In China Modeling Contest (Example Cup), '+question['question']
    assert not validate_interview_focus(questions,plans,profile)
    questions[0]['question']+=' How did Beta (China) Ltd. use it?'
    assert any('another known experience' in e for e in validate_interview_focus(questions,plans,profile))

def test_project_onsite_privacy_is_not_availability_but_work_frequency_is():
    _,profile,plans,qs=setup()
    assert not validate_interview_focus(qs("What privacy controls did you use in ALP's on-site pilot?"),plans,profile)
    assert any('Availability' in e for e in validate_interview_focus(qs('At ALP, how many on-site days per week can you work and what privacy controls do you know?'),plans,profile))

def test_explicit_reviewed_goals_replace_automatic_dimension_expansion():
    refs,profile,_,_=setup()
    analysis={'matches':[],'concerns':[{'id':'p','title':'Retrieval verification','criterionIds':['c0'],'evidence':[refs[0]]}]}
    standards={'criteria':[{'id':'c0','group':'must','priority':0,'description':'Verify retrieval quality'}]}
    targets=[{'id':str(i),'pointId':'p','criterionId':'c0','singleGoal':goal,'decisionReason':'Different missing answer',
              'experienceId':'ea','evidence':[refs[0]]} for i,goal in enumerate(['Personal contribution','Evaluation criterion','Known limitation'])]
    plans=interview_plan(analysis,standards,profile,targets)
    assert [p['primaryGoal'] for p in plans]==[t['singleGoal'] for t in targets]
    assert all(p['experience']['id']=='ea' for p in plans)
    assert interview_plan(analysis,standards,profile)==[]


def test_same_goal_example_followup_is_not_rejected_by_punctuation():
    _,profile,plans,qs=setup()
    plans[0]['primaryGoal']='核实实际用过的模型与框架，可举一个对应实例'
    questions=qs('在 ALP 你实际用过哪些模型与框架？能举一个对应实例吗？')
    assert not validate_interview_focus(questions,plans,profile)
    # Existing real boundaries still apply even when only one question mark is used.
    assert any('Availability' in e for e in validate_interview_focus(qs('At ALP, how many onsite days per week can you work and what privacy controls do you know?'),plans,profile))
    assert any('another known experience' in e for e in validate_interview_focus(qs('At ALP and BTA, which system did you build?'),plans,profile))

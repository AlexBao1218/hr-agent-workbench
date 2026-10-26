"""Suki-run scope regression: actual request guidance/context, not live model acceptance."""
from copy import deepcopy
import json
import pytest
from hr_backend.prompts import VERIFICATION_SCOPE_RULE,role_instructions
from hr_backend.provider import OpenAIProvider
from hr_backend.schemas import MatchResult,ReviewResult,VerificationTarget


@pytest.mark.parametrize('criterion,goal,remaining',[
    ('能端到端评估 HR 工作流，识别高杠杆 AI/自动化机会，并从零设计、开发、部署与现有 HR 系统及数据集成的 HR AI agent。',
     '核实是否有将工具与HR系统或数据实际集成的经历', '工作流机会评估及从零设计、开发、部署仍未知'),
    ('跟踪 agent 使用、性能与失败案例，优化提示词、工作流与模型选择，并确保数据隐私、安全与合规标准。',
     '核实是否实际记录过上线后的失败案例', '优化及隐私、安全、合规实践仍未知'),
])
def test_selected_component_does_not_require_whole_jd_or_erase_unknowns(criterion,goal,remaining):
    target=VerificationTarget(id='target',pointId='concern',criterionId='criterion',singleGoal=goal,
                              decisionReason='本题仅核实该具体缺口；'+remaining,experienceId=None,evidence=[]).model_dump()
    payload={'standards':{'criteria':[{'id':'criterion','description':criterion,'jdQuote':criterion}]},
             'analysis':{'observations':[{'criterionId':'criterion','coverage':'unknown','explanation':remaining,'evidence':[]}],
                         'concerns':[{'id':'concern','criterionIds':['criterion'],'explanation':'本题聚焦：'+goal+'；'+remaining}]},
             'verificationTargets':[target]}
    before=deepcopy(payload)
    for role,schema in [('match',MatchResult),('review',ReviewResult)]:
        request=OpenAIProvider().request_arguments(role,payload,schema)
        instruction=request['input'][0]['content'];sent=json.loads(request['input'][1]['content'])
        assert VERIFICATION_SCOPE_RULE in instruction
        assert 'selecting one item does not' in instruction
        assert 'claim to cover every clause' in instruction
        assert 'Each unresolved item asks ONE missing answer' in instruction
        assert 'or manufacture gaps to fill three slots' in instruction
        assert sent==before and payload==before
        assert sent['analysis']['observations'][0]['coverage']=='unknown'
        assert sent['verificationTargets'][0]['singleGoal']==goal
        assert sent['standards']['criteria'][0]['description']==criterion


def test_plan_field_guidance_and_existing_evidence_rules_remain():
    fields=VerificationTarget.model_json_schema()['properties']
    assert 'not every JD subclause' in fields['singleGoal']['description']
    assert 'does not establish the whole criterion' in fields['decisionReason']['description']
    for role in ('match','review'):
        instructions=role_instructions(role)
        assert 'Quote exact original source passages' in instructions
        assert 'Each candidate fact needs its own exact source' in instructions
        assert 'correct activity/employer' in instructions
        assert 'Company background' in instructions
        assert 'selecting one item does not' in instructions
    assert VERIFICATION_SCOPE_RULE not in role_instructions('cv')

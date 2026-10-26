"""Offline request-policy checks, not a claim of successful live semantic inference."""
import json
import pytest
from hr_backend.provider import OpenAIProvider
from hr_backend.prompts import PROJECT_DOMAIN_RULE
from hr_backend.schemas import MatchResult,ReviewResult


@pytest.mark.parametrize('role,schema',[('match',MatchResult),('review',ReviewResult)])
def test_both_roles_receive_same_project_local_domain_rule_with_full_context(role,schema):
    document={'id':'d','blocks':[{'id':'context','text':'Company develops hospital software.'},
                               {'id':'activity','text':'Classified documents into knowledge bases.'},
                               {'id':'explicit','text':'Built an assistant for the purchasing department.'}]}
    args=OpenAIProvider().request_arguments(role,{'document':document},schema)
    instruction=args['input'][0]['content']
    assert instruction.count(PROJECT_DOMAIN_RULE)==1
    assert 'correct activity/employer' in instruction
    assert 'Keep unspecified domains unknown' in instruction
    assert 'Each candidate fact needs its own exact source' in instruction
    assert 'Company background' in instruction and 'cannot establish' in instruction
    assert 'candidate claim' in instruction or 'candidate fact' in instruction
    assert instruction.index(PROJECT_DOMAIN_RULE)<instruction.index('Active role:')
    assert json.loads(args['input'][1]['content'])['document']==document

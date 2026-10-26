"""Shared prompts constrain generation/review; code never suppresses semantic blockers."""
from copy import deepcopy

import pytest

from hr_backend.agent_settings import cv_settings_hash, snapshot_settings
from hr_backend.provider import FixtureProvider
from hr_backend.workflow import Workflow, WorkflowError
from test_workflow import inputs


def test_frozen_match_and_review_share_responsibility_and_selection_contract():
    state={'agentSettings':snapshot_settings({},FixtureProvider())}
    for role in ('match','review'):
        state['agentSettings']['roles'][role]['systemPrompt']='Previously saved editable role task: '+role
    original=deepcopy(state)
    snapshot=snapshot_settings(state,FixtureProvider())
    shared=[]
    for role in ('match','review'):
        prompt=snapshot['effectivePrompts'][role]
        shared.append(prompt.split('Active role: ',1)[0])
        assert 'FUTURE JOB RESPONSIBILITY' in prompt
        assert 'PRIOR EXPERIENCE qualification' in prompt
        assert 'never turn it into a prerequisite for a separate responsibility criterion' in prompt
        assert 'Preserve EVERY confirmed must/bonus group, priority and requirement' in prompt
        assert 'Retain all remaining unknowns in their observations' in prompt
        assert 'No individual criterion automatically owns a question' in prompt
        assert prompt.endswith('Previously saved editable role task: '+role)
    assert shared[0]==shared[1]
    assert 'defect PRESENT in this CURRENT output' in snapshot['effectivePrompts']['review']
    assert state['agentSettings']==original['agentSettings']  # preserved active tasks; no activation
    assert cv_settings_hash(snapshot)==cv_settings_hash(original['agentSettings'])


def test_reviewer_blocker_is_not_silently_ignored_even_if_it_requests_an_existing_unknown(inputs):
    document,standards,hat=deepcopy(inputs)
    standards['jdText']+=' Prior HR assistant experience preferred.'
    standards['criteria'].append({'id':'prior','title':'Prior HR experience',
        'description':'Prior HR assistant experience preferred.','jdQuote':'Prior HR assistant experience preferred.',
        'group':'bonus','priority':0})
    events=[]

    class CurrentOutputStub(FixtureProvider):
        def generate(self,role,payload,schema):
            if role=='review':
                assert next(o for o in payload['analysis']['observations'] if o['criterionId']=='prior')['coverage']=='unknown'
                return {'approved':False,'issues':['Prior experience must remain unknown'],
                        'findings':[{'category':'coverage_error','severity':'blocker',
                                     'field':'observations[prior]','message':'Prior experience must remain unknown'}]}
            return super().generate(role,payload,schema)

    with pytest.raises(WorkflowError,match='Evidence review did not pass'):
        Workflow(CurrentOutputStub(),max_repairs=0).analyze(document,standards,hat,'run','candidate',trace_callback=events.append)
    decisions=[e['_diagnostic'] for e in events if e.get('_diagnostic',{}).get('kind')=='review_decision']
    assert decisions[-1]['reviewIssues']==['Prior experience must remain unknown']
    assert decisions[-1]['publishable'] is False
    assert not any(e.get('role')=='interview' for e in events)


@pytest.mark.parametrize('repairs,roles,success',[
    (0,['match'],False),
    (1,['match','match','review'],False),
    (2,['match','match','review','match','review','interview'],True),
])
def test_existing_total_repair_setting_handles_deterministic_then_semantic_feedback(inputs,repairs,roles,success):
    document,standards,hat=deepcopy(inputs)
    profile={'name':'Example','education':[],'experiences':[],'languages':[],'availability':None}
    issue='verificationTargets[t0].singleGoal combines contribution with unrelated HR collaboration'

    class RepairStub(FixtureProvider):
        def __init__(self):self.roles=[];self.matches=0;self.reviews=0

        def generate(self,role,payload,schema):
            self.roles.append(role)
            if role=='review':
                self.reviews+=1
                if self.reviews==1:
                    assert 'HR协作' in payload['verificationTargets'][0]['singleGoal']
                    return {'approved':False,'issues':[issue]}
                assert payload['verificationTargets'][0]['singleGoal']=='核实个人贡献'
                return {'approved':True,'issues':[]}
            result=super().generate(role,payload,schema)
            if role=='match':
                self.matches+=1
                if self.matches==1:
                    result['observations'][0].update(coverage='unknown',evidence=[])
                elif self.matches==2:
                    result['verificationTargets'][0]['singleGoal']='核实个人贡献及另一项HR协作'
                else:
                    assert payload['repairIssues']==[issue]
                    assert 'HR协作' in payload['previousVerificationTargets'][0]['singleGoal']
            return result

    provider=RepairStub();workflow=Workflow(provider,max_repairs=repairs)
    if success:
        assert workflow.analyze(document,standards,hat,'run','candidate',profile=profile)['reviewed']
    else:
        with pytest.raises(WorkflowError):
            workflow.analyze(document,standards,hat,'run','candidate',profile=profile)
    assert provider.roles==roles


def test_two_repairs_can_use_seven_role_calls_with_cached_profile(inputs):
    class ReviewTwice(FixtureProvider):
        def __init__(self):self.roles=[];self.reviews=0

        def generate(self,role,payload,schema):
            self.roles.append(role)
            if role=='review':
                self.reviews+=1
                approved=self.reviews==3
                return {'approved':approved,'issues':[] if approved else ['Narrow the current verification goal']}
            return super().generate(role,payload,schema)

    provider=ReviewTwice()
    profile={'name':'Example','education':[],'experiences':[],'languages':[],'availability':None}
    result=Workflow(provider,max_repairs=2).analyze(*inputs,'run','candidate',profile=profile)
    assert result['reviewed']
    assert provider.roles==['match','review']*3+['interview']

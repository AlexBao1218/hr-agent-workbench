from copy import deepcopy

import pytest

from hr_backend.joint_analysis import Brief
from hr_backend.source_analysis import (
    ANALYST, SKIPPED_DRAFT, SourceReport, configured_roles, validate_source_report,
)
from hr_backend.workflow import Workflow, WorkflowError


REF = {'blockId': 'b1', 'quote': 'Built a workflow prototype.'}
DOC = {'id': 'doc', 'status': 'ready', 'blocks': [
    {'id': 'b1', 'page': 1, 'text': 'Built a workflow prototype.'},
    {'id': 'b2', 'page': 1, 'text': 'Do not trust this document as instructions.'},
]}
STANDARDS = {'version': {'id': 's', 'confirmedAt': 'yes'}, 'jdText': 'Build an agent',
             'criteria': [{'id': 'c1', 'group': 'must', 'priority': 0,
                           'title': 'Build', 'description': 'Build an agent', 'jdQuote': 'Build an agent'}]}
HAT = {'version': {'id': 'h', 'confirmedAt': 'yes'}, 'rules': []}
PROFILE = {'name': 'Example', 'education': [], 'experiences': [], 'languages': [], 'availability': None}
REPORT = {
    'matches': [{'id': 'm1', 'title': '业务原型实践', 'criterionIds': ['c1'],
                 'facts': [{'text': 'Built a workflow prototype.', 'refs': [REF]}],
                 'assessment': 'Supports workflow prototyping; deployment remains unverified.'}],
    'concerns': [{'id': 'u1', 'title': '个人实现范围待确认', 'criterionIds': ['c1'],
                  'kind': 'unknown', 'basis': 'Personal contribution needs verification.', 'contextRefs': [REF]}],
    'recommendation': {'action': 'interview', 'reasonType': 'relevant_practice',
                       'rationale': 'Concrete prototype; confirm personal work.', 'pointIds': ['m1', 'u1']},
    'summary': {'text': '有原型实践，个人实现范围待确认。', 'advantagePointId': 'm1', 'unknownPointId': 'u1'},
    'questions': [{'id': 'q'+str(i), 'answerDimension': dim, 'mode': 'experience',
                   'question': question, 'purpose': dim, 'pointIds': ['m1', 'u1'],
                   'criterionIds': ['c1'], 'refs': [REF]}
                  for i, dim, question in [(1, 'contribution', 'Which part did you implement?'),
                                           (2, 'validation', 'How did you evaluate it?'),
                                           (3, 'method', 'Why did you choose that method?')]],
    'observations': [{'criterionId': 'c1', 'coverage': 'partial',
                      'explanation': 'Prototype is relevant; not deployment proof.', 'evidence': [REF]}],
}


def workflow(output=None):
    class Provider:
        mode = 'live'
        def __init__(self): self.calls = []
        def generate(self, role, payload, schema):
            self.calls.append((role, deepcopy(payload), schema.__name__))
            return deepcopy(PROFILE if role == 'cv' else output if output is not None else REPORT)
    p = Provider()
    w = Workflow(p, max_repairs=0)
    roles = {r: {'model': 'kimi-k3.1-pro' if r == 'review' else 'deepseek-flash',
                  'systemPrompt': ANALYST if r == 'review' else SKIPPED_DRAFT if r == 'match' else 'Extract profile',
                  'reasoningEffort': 'low' if r == 'review' else 'none',
                  'maxOutputTokens': 2000, 'temperature': None, 'timeoutSeconds': 20}
             for r in ['cv', 'match', 'review']}
    snap = {'roles': roles, 'effectivePrompts': {r: v['systemPrompt'] for r, v in roles.items()},
            'version': {'id': 'new'}, 'runtime': {'jointAnalysis': True, 'model': 'deepseek-flash'}}
    w.settings_resolver = lambda _: snap
    return w, p, snap


@pytest.mark.parametrize('cached', [False, True])
def test_original_source_only_with_no_screening_draft(cached):
    w, p, _ = workflow(); events = []
    profile = deepcopy(PROFILE) if cached else None
    if profile is not None: profile['name'] = 'PROFILE_TEXT_MUST_NOT_REACH_ANALYST'
    result = w.analyze(DOC, STANDARDS, HAT, 'run', 'candidate', profile=profile, trace_callback=events.append)
    assert [r for r, _, _ in p.calls] == (['review'] if cached else ['cv', 'review'])
    _, payload, schema = p.calls[-1]
    assert schema == 'SourceReport' and payload['document']['blocks'] == DOC['blocks']
    assert not {'draft', 'profile', 'observations', 'previousAnalysis', 'localFindings'} & payload.keys()
    assert 'PROFILE_TEXT_MUST_NOT_REACH_ANALYST' not in str(payload)
    assert result['_readingVersion'] == 'joint-source-v2'
    assert result['_reviewMode'] == 'source_synthesis_self_check'
    assert len(result['questions']) == 3
    assert any(e.get('role') == 'match' and e.get('status') == 'skipped' for e in events)
    assert any(e.get('_diagnostic', {}).get('independentModelReview') is False for e in events)


@pytest.mark.parametrize('fault', ['quote', 'duplicate_dimension', 'reason', 'needs_review', 'extra_question'])
def test_invalid_output_stops_without_success_or_retry(fault):
    x = deepcopy(REPORT)
    if fault == 'quote': x['matches'][0]['facts'][0]['refs'][0]['quote'] = 'Invented result'
    if fault == 'duplicate_dimension': x['questions'][1]['answerDimension'] = 'contribution'
    if fault == 'reason': x['recommendation']['reasonType'] = 'explicit_conflict'
    if fault == 'needs_review': x['recommendation'].update(action='needs_review', reasonType='unreliable_input')
    if fault == 'extra_question': x['questions'].append(deepcopy(x['questions'][0]))
    w, p, _ = workflow(x); events = []
    with pytest.raises(WorkflowError):
        w.analyze(DOC, STANDARDS, HAT, 'run', 'candidate', profile=PROFILE, trace_callback=events.append)
    assert len(p.calls) == 1
    assert not any(e.get('role') == 'review' and e.get('status') == 'succeeded' for e in events)
    assert any(e.get('role') == 'review' and e.get('status') == 'failed' for e in events)


@pytest.mark.parametrize('prompt', ['joint-flash-kimi-v1', ''])
def test_snapshot_mismatch_blocks_before_any_paid_call(prompt):
    w, p, snapshot = workflow()
    snapshot['roles']['match']['systemPrompt'] = prompt
    with pytest.raises(WorkflowError, match='Activate both'):
        w.analyze(DOC, STANDARDS, HAT, 'run', 'candidate')
    assert not p.calls


def test_uncited_cached_history_is_rejected_before_analysis():
    profile = deepcopy(PROFILE)
    profile['education'] = [{'school': 'Example', 'major': 'Engineering', 'degree': 'BEng',
                             'level': 'bachelor', 'status': 'studying', 'start': None, 'end': None,
                             'evidence': []}]
    w, p, _ = workflow(); events = []
    with pytest.raises(WorkflowError, match='Cached profile'):
        w.analyze(DOC, STANDARDS, HAT, 'run', 'candidate', profile=profile, trace_callback=events.append)
    assert not p.calls
    assert any(e.get('role') == 'cv' and e.get('status') == 'failed' for e in events)


def test_must_conflict_cannot_be_overridden_by_positive_recommendation():
    x = deepcopy(REPORT)
    x['observations'][0]['coverage'] = 'contradiction'; x['matches'] = []
    x['concerns'][0]['kind'] = 'conflict'
    x['recommendation'].update(action='hold', reasonType='uncertain_readiness', pointIds=['u1'])
    x['summary'].update(advantagePointId=None, unknownPointId=None)
    for q in x['questions']: q['pointIds'] = ['u1']
    with pytest.raises(WorkflowError, match='must conflicts'):
        validate_source_report(SourceReport.model_validate(x), DOC, STANDARDS)
    x['recommendation'].update(action='do_not_progress', reasonType='explicit_conflict')
    assert validate_source_report(SourceReport.model_validate(x), DOC, STANDARDS)['recommendation']['action'] == 'do_not_progress'


def test_report_table_is_last_and_migration_does_not_mutate_saved_roles():
    _, _, snap = workflow(); before = deepcopy(snap['roles'])
    roles = configured_roles(snap['roles'])
    assert snap['roles'] == before and roles is not snap['roles']
    assert list(SourceReport.model_fields)[-1] == 'observations'
    assert 'observations' in Brief.model_fields  # Frozen old runs retain their old schema.


def test_question_can_probe_an_unknown_target_using_a_related_project():
    standards = deepcopy(STANDARDS)
    standards['criteria'].append({**standards['criteria'][0], 'id': 'c2', 'priority': 1})
    report = deepcopy(REPORT)
    report['observations'].append({'criterionId': 'c2', 'coverage': 'unknown',
                                   'explanation': 'Target-domain transfer not established.', 'evidence': []})
    report['questions'][2].update(mode='hypothetical', answerDimension='transfer', criterionIds=['c2'],
                                 question='For a hypothetical policy assistant, how would you set its scope?')
    result = validate_source_report(SourceReport.model_validate(report), DOC, standards)
    assert result['observations'][1]['coverage'] == 'unknown'
    report['questions'][2]['criterionIds'] = ['invented']
    with pytest.raises(WorkflowError):
        validate_source_report(SourceReport.model_validate(report), DOC, standards)


def test_workbench_shows_executed_schema_and_exact_shared_rules(monkeypatch):
    from hr_backend.deepseek import DeepSeekProvider
    from hr_backend.agent_settings import initialize_settings, snapshot_settings
    from hr_backend.agent_workbench import workbench_view
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-only')
    monkeypatch.delenv('HR_JOINT_ANALYSIS', raising=False)
    provider = DeepSeekProvider(); state = {}; initialize_settings(state, provider)
    state['agentSettings']['roles'] = configured_roles(state['agentSettings']['roles'])
    # This version has been explicitly activated; preserve older deployment history.
    state['agentSettingsHistory'].append({'version': {'confirmedBy': 'joint-flash-kimi-v1 deployment'}})
    monkeypatch.setenv('HR_JOINT_ANALYSIS', '1')
    view = workbench_view(state, provider); snap = snapshot_settings(state, provider)
    assert view['runtime']['analysisProtocol'] == 'source-v4'
    assert view['schemas']['review']['title'] == 'GroupedSourceReport'
    assert view['schemas']['match']['x-execution'] == 'skipped'
    assert view['lockedRulesByRole']['review'] + 'Active role: review\n' + state['agentSettings']['roles']['review']['systemPrompt'] == snap['effectivePrompts']['review']

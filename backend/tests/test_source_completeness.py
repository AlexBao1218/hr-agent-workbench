"""Source-only completeness checks; every provider in this file is an offline fake."""
from copy import deepcopy

import pytest

from hr_backend.source_analysis import CompleteSourceReport, SourceReport, validate_source_report
from hr_backend.workflow import WorkflowError
from test_source_analysis import DOC, HAT, PROFILE, REF, REPORT, STANDARDS, workflow


def complete_report():
    report = deepcopy(REPORT)
    report['matches'][0]['capabilityKey'] = 'workflow design'
    report['matches'][0]['facts'][0]['primaryRef'] = deepcopy(REF)
    report['observations'][0]['unresolvedAnswers'] = [
        {'text': report['concerns'][0]['basis'], 'concernId': 'u1'}]
    return report


def checked(report, document=DOC, standards=STANDARDS):
    return validate_source_report(CompleteSourceReport.model_validate(report), document, standards)


@pytest.mark.parametrize('contained', [False, True])
def test_renamed_capability_cannot_repeat_primary_fact(contained):
    report = complete_report()
    duplicate = deepcopy(report['matches'][0])
    duplicate.update(id='m2', title='A differently worded title', capabilityKey='another capability')
    if contained:
        duplicate['facts'][0]['primaryRef']['quote'] = 'workflow prototype'
        duplicate['facts'][0]['refs'].append(deepcopy(duplicate['facts'][0]['primaryRef']))
    report['matches'].append(duplicate)
    with pytest.raises(WorkflowError):
        checked(report)


def test_normalized_capability_key_cannot_repeat_with_distinct_facts():
    report, document = distinct_fact_report()
    report['matches'][1]['capabilityKey'] = '  workflow   design  '
    with pytest.raises(WorkflowError):
        checked(report, document)


def distinct_fact_report():
    report, document = complete_report(), deepcopy(DOC)
    document['blocks'][0]['text'] += ' Tested retrieval on a labelled dataset.'
    second_ref = {'blockId': 'b1', 'quote': 'Tested retrieval on a labelled dataset.'}
    second = deepcopy(report['matches'][0])
    second.update(id='m2', title='Retrieval validation', capabilityKey='retrieval validation')
    second['facts'] = [{'text': 'Tested retrieval.', 'primaryRef': second_ref,
                        'refs': [second_ref, deepcopy(REF)]}]
    report['matches'].append(second)
    return report, document


def test_independent_facts_same_criterion_and_block_are_allowed():
    report, document = distinct_fact_report()
    result = checked(report, document)
    assert len(result['matches']) == 2
    # The first match's primary source may be background in another match,
    # and may remain context for concerns and questions.
    assert result['matches'][1]['facts'][0]['refs'][1] == REF


def test_primary_source_must_be_one_of_fact_references():
    report = complete_report()
    report['matches'][0]['facts'][0]['primaryRef'] = {
        'blockId': 'b2', 'quote': DOC['blocks'][1]['text']}
    with pytest.raises((WorkflowError, ValueError)):
        checked(report)


def six_concerns_report():
    report, standards = complete_report(), deepcopy(STANDARDS)
    for number in range(2, 7):
        cid, uid = f'c{number}', f'u{number}'
        text = 'Weekly onsite days are unknown.' if number == 6 else f'Independent answer {number} is unknown.'
        standards['criteria'].append({**standards['criteria'][0], 'id': cid, 'priority': number})
        report['concerns'].append({'id': uid, 'title': text, 'criterionIds': [cid],
                                   'kind': 'unknown', 'basis': text, 'contextRefs': []})
        report['observations'].append({'criterionId': cid, 'coverage': 'unknown',
                                       'explanation': text, 'evidence': [],
                                       'unresolvedAnswers': [{'text': text, 'concernId': uid}]})
    return report, standards


def test_six_independent_concerns_preserve_availability():
    report, standards = six_concerns_report()
    result = checked(report, standards=standards)
    assert len(result['concerns']) == 6
    assert result['concerns'][-1]['basis'] == 'Weekly onsite days are unknown.'


def test_compound_requirement_answer_cannot_be_hidden_behind_valid_link():
    report = complete_report()
    report['observations'][0]['unresolvedAnswers'].append(
        {'text': 'Deployment reliability remains unknown.', 'concernId': 'u1'})
    with pytest.raises(WorkflowError):
        checked(report)
    report['concerns'][0]['basis'] += ' Deployment reliability remains unknown.'
    assert len(checked(report)['observations'][0]['unresolvedAnswers']) == 2


@pytest.mark.parametrize('fault', ['match_id', 'wrong_criterion', 'missing_answer', 'supported_answer', 'question_match_only'])
def test_visible_accounting_rejects_invalid_destinations(fault):
    report = complete_report()
    if fault == 'match_id':
        report['observations'][0]['unresolvedAnswers'][0]['concernId'] = 'm1'
    elif fault == 'wrong_criterion':
        report, standards = six_concerns_report()
        report['observations'][0]['unresolvedAnswers'][0] = {
            'text': report['concerns'][1]['basis'], 'concernId': 'u2'}
        with pytest.raises(WorkflowError):
            checked(report, standards=standards)
        return
    elif fault == 'missing_answer':
        report['observations'][0]['unresolvedAnswers'] = []
    elif fault == 'supported_answer':
        report['observations'][0]['coverage'] = 'supported'
    else:
        report['questions'][0]['pointIds'] = ['m1']
    with pytest.raises(WorkflowError):
        checked(report)


def test_bonus_unknown_also_needs_visible_answer():
    report, standards = six_concerns_report()
    standards['criteria'][-1]['group'] = 'bonus'
    report['observations'][-1]['unresolvedAnswers'] = []
    with pytest.raises(WorkflowError):
        checked(report, standards=standards)


def test_whitespace_only_answer_cannot_pass_substring_accounting():
    report = complete_report()
    report['observations'][0]['unresolvedAnswers'][0]['text'] = ' \t\n '
    with pytest.raises((WorkflowError, ValueError)):
        checked(report)


def test_known_period_does_not_promote_unknown_weekly_attendance():
    report, document = complete_report(), deepcopy(DOC)
    document['blocks'].append({'id': 'dates', 'page': 1, 'text': 'Available January through August.'})
    period = {'blockId': 'dates', 'quote': 'Available January through August.'}
    report['observations'][0].update(coverage='partial', evidence=[period],
        explanation='The period is available; weekly onsite days are unknown.',
        unresolvedAnswers=[{'text': 'Weekly onsite days are unknown.', 'concernId': 'u1'}])
    report['concerns'][0].update(basis='Available January through August. Weekly onsite days are unknown.',
                                 contextRefs=[period])
    assert checked(report, document)['observations'][0]['coverage'] == 'partial'


def test_v2_keeps_original_schema_and_output():
    w, provider, _ = workflow()
    result = w.analyze(DOC, STANDARDS, HAT, 'run', 'candidate', profile=PROFILE)
    assert provider.calls[0][2] == 'SourceReport'
    assert result['_readingVersion'] == 'joint-source-v2'
    assert 'unresolvedAnswers' not in SourceReport.model_fields['observations'].annotation.__args__[0].model_fields


def test_completeness_failure_never_retries_or_emits_success():
    report = complete_report()
    report['observations'][0]['unresolvedAnswers'] = []
    w, provider, snapshot = workflow(report)
    for role in ('match', 'review'):
        snapshot['roles'][role]['systemPrompt'] = 'joint-source-v3\nCompleteness test'
        snapshot['effectivePrompts'][role] = snapshot['roles'][role]['systemPrompt']
    events = []
    with pytest.raises(WorkflowError):
        w.analyze(DOC, STANDARDS, HAT, 'run', 'candidate', profile=PROFILE, trace_callback=events.append)
    assert len(provider.calls) == 1
    assert provider.calls[0][2] == 'CompleteSourceReport'
    assert any(e.get('role') == 'review' and e.get('status') == 'failed' for e in events)
    assert not any(e.get('role') == 'review' and e.get('status') == 'succeeded' for e in events)


def test_projection_removes_accounting_metadata_but_role_diagnostic_retains_it():
    w, provider, snapshot = workflow(complete_report())
    for role in ('match', 'review'):
        snapshot['roles'][role]['systemPrompt'] = 'joint-source-v3\nCompleteness test'
        snapshot['effectivePrompts'][role] = snapshot['roles'][role]['systemPrompt']
    events = []
    result = w.analyze(DOC, STANDARDS, HAT, 'run', 'candidate', profile=PROFILE, trace_callback=events.append)
    assert len(provider.calls) == 1
    assert 'unresolvedAnswers' not in result['observations'][0]
    outputs = [e['_diagnostic']['output'] for e in events
               if e.get('_diagnostic', {}).get('kind') == 'role_output']
    assert outputs and outputs[-1]['observations'][0]['unresolvedAnswers']

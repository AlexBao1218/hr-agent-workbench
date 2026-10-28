from copy import deepcopy
import pytest
from pydantic import ValidationError
from hr_backend.source_analysis import (GroupedSourceReport, CompleteSourceReport, group_existing_concerns,
    validate_source_report, GROUPED_ANALYST, configured_roles, report_schema)
from test_source_completeness import six_concerns_report
from test_source_analysis import DOC


def plan():
    return [{'id': 'g'+str(i), 'title': 'Theme '+str(i),
             'memberIds': ['u'+str(i)] if i < 5 else ['u5', 'u6']} for i in range(1, 6)]


def test_five_theme_limit_preserves_every_answer_and_source():
    raw, standards = six_concerns_report()
    before = deepcopy(raw)
    with pytest.raises(ValidationError): GroupedSourceReport.model_validate(raw)
    grouped = group_existing_concerns(raw, plan())
    validated = validate_source_report(GroupedSourceReport.model_validate(grouped), DOC, standards)
    assert len(validated['concerns']) == 5 and raw == before
    for old in raw['concerns']:
        new = next(p for p in validated['concerns'] if old['basis'] in p['basis'])
        assert set(old['criterionIds']) <= set(new['criterionIds'])
        assert all(r in new['contextRefs'] for r in old['contextRefs'])
    assert validated['observations'][-1]['unresolvedAnswers'][0]['concernId'] == 'g5'
    assert validated['questions'][0]['pointIds'] == ['m1', 'g1']
    assert validated['summary']['unknownPointId'] == 'g1'
    assert [o['coverage'] for o in raw['observations']] == [o['coverage'] for o in validated['observations']]
    assert CompleteSourceReport.model_validate(raw)  # frozen v3 stays compatible


@pytest.mark.parametrize('fault', ['omit', 'duplicate', 'too_many', 'mixed_kind'])
def test_grouping_cannot_drop_or_duplicate_or_mix_conflicts(fault):
    raw, _ = six_concerns_report(); groups = plan()
    if fault == 'omit': groups[-1]['memberIds'] = ['u5']
    if fault == 'duplicate': groups[-1]['memberIds'].append('u1')
    if fault == 'too_many': groups = [{'id': p['id'], 'title':p['title'], 'memberIds':[p['id']]} for p in raw['concerns']]
    if fault == 'mixed_kind': raw['concerns'][-1]['kind'] = 'conflict'
    with pytest.raises(ValueError): group_existing_concerns(raw, groups)


def test_new_activation_enforces_v4_but_preserves_v3():
    roles = configured_roles({'review':{}, 'match':{}})
    assert report_schema({'roles': roles}) is GroupedSourceReport
    assert report_schema({'roles': {'review': {'systemPrompt': 'joint-source-v3'}}}) is CompleteSourceReport
    assert '最多5个可见疑点主题' in GROUPED_ANALYST
    assert '可增加至12个' not in GROUPED_ANALYST

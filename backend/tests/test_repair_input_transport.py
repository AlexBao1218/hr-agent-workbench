"""Actual SDK -> MockTransport proof that repair fields reach Responses input."""
from copy import deepcopy
import json

import pytest

from hr_backend.workflow import Workflow,WorkflowError
from test_deepseek import setup
from test_structured_evidence import case


def test_repair_changes_sdk_message_then_identical_next_input_stops_without_another_call(setup,case):
    provider,requests,outputs=setup
    document,standards,hat,profile,data=deepcopy(case)
    data['matches'][0]['componentIds'].append('missing-component')
    outputs.extend([deepcopy(data),deepcopy(data)])
    events=[]
    with pytest.raises(WorkflowError,match='stopped without repeating the same model request') as caught:
        Workflow(provider,max_repairs=2).analyze(document,standards,hat,'run','candidate',profile=profile,trace_callback=events.append)
    assert caught.value.code=='repair_stalled'
    assert len(requests)==2 and provider.usage_summary()['attempts']==2
    initial=json.loads(requests[0]['input'][1]['content'])
    repair=json.loads(requests[1]['input'][1]['content'])
    assert initial['repairIssues']==[] and initial['previousStructuredEvidence'] is None
    assert repair['repairScope']['mode']=='local_correction'
    assert repair['previousStructuredEvidence']['matches']==data['matches']
    assert len(repair['repairIssues'])==1
    assert repair['repairIssues']==['Match requires existing positive components: m1']
    assert requests[0]['input']!=requests[1]['input']
    stalled=next(e['_diagnostic'] for e in events if e.get('_diagnostic',{}).get('kind')=='repair_stalled')
    assert stalled['providerCalled'] is False and stalled['previousAttempt']==2
    assert not any(e.get('role')=='interview' for e in events)


from test_structured_evidence import row_case
from hr_backend.structured_evidence import project_rows


def test_row_sdk_transport_has_compact_initial_input_and_three_role_public_result(setup,row_case):
    provider,requests,outputs=setup;provider.evidence_protocol=3
    doc,standards,hat,profile,data=deepcopy(row_case)
    _,_,targets,_,errors=project_rows(data,standards,profile);assert not errors
    questions=[{'id':'q'+str(i),'question':'你'+t['singleGoal'],'purpose':t['singleGoal'],
                'pointIds':[t['pointId']],'criterionIds':[t['criterionId']],'evidence':t['evidence']} for i,t in enumerate(targets)]
    outputs.extend([data,{'approved':True,'findings':[]},{'questions':questions}])
    result=Workflow(provider,max_repairs=0).analyze(doc,standards,hat,'r','c',profile=profile)
    assert result['reviewed'] and len(requests)==3
    first=json.loads(requests[0]['input'][1]['content'])
    assert requests[0]['text']['format']['name']=='CriterionRowsResult'
    assert 'profile' not in first and 'capabilityEvidenceIndex' not in first
    assert not any(k.startswith('previous') or k.startswith('repair') for k in first)
    assert requests[1]['text']['format']['name']=='CriterionRowsReview'
    assert json.loads(requests[1]['input'][1]['content'])['criterionEvidence']==data


def test_row_sdk_repair_only_sends_prior_rows_after_failure_and_never_repeats_input(setup,row_case):
    provider,requests,outputs=setup;provider.evidence_protocol=3
    doc,standards,hat,profile,data=deepcopy(row_case);data['rows'].pop()
    outputs.extend([deepcopy(data),deepcopy(data)])
    with pytest.raises(WorkflowError) as caught:
        Workflow(provider,max_repairs=2).analyze(doc,standards,hat,'r','c',profile=profile)
    assert caught.value.code=='repair_stalled'
    assert len(requests)==provider.usage_summary()['attempts']==2
    initial=json.loads(requests[0]['input'][1]['content']);repair=json.loads(requests[1]['input'][1]['content'])
    assert 'previousRows' not in initial and 'repairIssues' not in initial
    assert repair['previousRows']==data
    assert repair['repairIssues']==['Rows must cover each confirmed criterion exactly once']

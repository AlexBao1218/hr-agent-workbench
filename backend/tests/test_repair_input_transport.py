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

"""Real SDK + MockTransport: safe incomplete metadata, no network/inference."""
import json
import sqlite3

import httpx
import pytest

from hr_backend.agent_workbench import public_data
from hr_backend.provider import ProviderError
from hr_backend.schemas import ChatResult
from hr_backend.workflow import Workflow
from test_deepseek import setup

SENTINEL='PRIVATE_PROVIDER_PAYLOAD_AND_KEY'


def invoke(provider,outputs,envelope):
    outputs.append(httpx.Response(200,json=envelope))
    events=[]
    with pytest.raises(ProviderError) as caught:
        Workflow(provider,max_repairs=2)._call('match',{'context':{'runId':'safe-run'},'document':SENTINEL},ChatResult,events.append)
    assert caught.value.code=='model_incomplete'
    diagnostic=next(e['_diagnostic'] for e in events if e.get('_diagnostic',{}).get('kind')=='provider_response')
    assert caught.value.response_diagnostic=={k:v for k,v in diagnostic.items() if k!='kind'}
    assert public_data(diagnostic)==diagnostic  # Numeric reasoning count survives existing redaction.
    assert SENTINEL not in json.dumps(diagnostic)+str(caught.value)
    assert SENTINEL.encode() not in provider.usage_path.read_bytes()
    assert b'dummy-offline-key' not in provider.usage_path.read_bytes()
    return diagnostic


@pytest.mark.parametrize('reason,expected',[('max_output_tokens','max_output_tokens'),('content_filter','content_filter'),(None,None),(SENTINEL,'unrecognized')])
def test_incomplete_reason_status_and_counts_are_saved_without_response_content(setup,reason,expected):
    provider,requests,outputs=setup
    envelope={'status':'incomplete','incomplete_details':{'reason':reason,'message':SENTINEL},
        'usage':{'input_tokens':100,'output_tokens':12000,'output_tokens_details':{'reasoning_tokens':11900,'reasoning_content':SENTINEL}},
        'output':[{'type':'reasoning','content':[{'type':'reasoning_text','text':SENTINEL}]}],
        'error':{'message':SENTINEL},'api_key':SENTINEL}
    diagnostic=invoke(provider,outputs,envelope)
    assert diagnostic=={'kind':'provider_response','responseStatus':'incomplete','incompleteReason':expected,
                        'usage':{'inputTokens':100,'outputTokens':12000,'reasoningTokens':11900}}
    assert len(requests)==provider.usage_summary()['attempts']==1
    with sqlite3.connect(provider.usage_path) as db:
        assert db.execute('SELECT status,inputTokens,outputTokens FROM calls').fetchone()==('incomplete',100,12000)


@pytest.mark.parametrize('usage,expected',[
    (None,{'inputTokens':None,'outputTokens':None,'reasoningTokens':None}),
    ({'input_tokens':True,'output_tokens':'0','output_tokens_details':{'reasoning_tokens':-1}},
     {'inputTokens':None,'outputTokens':None,'reasoningTokens':None}),
    ({'input_tokens':0,'output_tokens':0,'output_tokens_details':{'reasoning_tokens':0}},
     {'inputTokens':0,'outputTokens':0,'reasoningTokens':0}),
])
def test_missing_or_malformed_usage_stays_unknown_and_explicit_zero_stays_zero(setup,usage,expected):
    provider,requests,outputs=setup
    diagnostic=invoke(provider,outputs,{'status':'incomplete','incomplete_details':{'reason':'content_filter'},'usage':usage})
    assert diagnostic['usage']==expected
    assert len(requests)==1
    with sqlite3.connect(provider.usage_path) as db:
        assert db.execute('SELECT inputTokens,outputTokens FROM calls').fetchone()==(expected['inputTokens'],expected['outputTokens'])
    assert provider.usage_summary()['unknownUsageCalls']==int(expected['inputTokens'] is None or expected['outputTokens'] is None)


@pytest.mark.parametrize('status,expected',[('failed','failed'),(SENTINEL,'unrecognized')])
def test_actual_noncompleted_status_is_not_overwritten_by_existing_error_classification(setup,status,expected):
    provider,requests,outputs=setup
    diagnostic=invoke(provider,outputs,{'status':status,'error':{'message':SENTINEL}})
    assert diagnostic['responseStatus']==expected
    assert diagnostic['incompleteReason'] is None
    assert len(requests)==1 and provider.usage_summary()['unknownUsageCalls']==1


def test_balance_error_is_actionable_and_never_auto_retried_or_leaks_body(setup):
    provider,requests,outputs=setup
    outputs.append(httpx.Response(402,json={'error':{'message':SENTINEL}}))
    events=[]
    with pytest.raises(ProviderError) as caught:
        Workflow(provider,max_repairs=2)._call('match',{'context':{'runId':'balance-test'}},ChatResult,events.append)
    assert caught.value.code=='model_balance_exhausted'
    assert '余额不足' in caught.value.message and '402' in caught.value.message
    assert len(requests)==1 and provider.usage_summary()['attempts']==1
    assert SENTINEL not in str(caught.value)+json.dumps(events)
    diagnostic=next(e['_diagnostic'] for e in events if e.get('_diagnostic',{}).get('kind')=='provider_response')
    assert diagnostic['httpStatus']==402 and diagnostic['failureKind']=='http_status'

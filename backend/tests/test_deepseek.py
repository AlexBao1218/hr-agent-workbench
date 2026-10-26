"""Real SDK serialization + mocked HTTP transport; no paid/API/network calls."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import httpx
import openai
import pytest
from hr_backend.provider import create_provider,ProviderError,OpenAIProvider
from hr_backend.deepseek import DeepSeekProvider
from hr_backend.schemas import ChatResult
from hr_backend.workflow import Workflow

@pytest.fixture
def setup(monkeypatch,tmp_path):
    for key in ['OPENAI_MODEL','OPENAI_BASE_URL','HR_MAX_REPAIRS','HR_MODEL_MAX_CALLS','HR_MODEL_MAX_INPUT_BYTES','HR_MODEL_MAX_OUTPUT_TOKENS','HR_MODEL_TIMEOUT_SECONDS','HR_MODEL_REASONING_EFFORT','HR_MODEL_MATCH_REASONING_EFFORT','HR_MODEL_REVIEW_REASONING_EFFORT']:
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('OPENAI_API_KEY','dummy-offline-key')
    monkeypatch.setenv('HR_MODEL_PROVIDER','deepseek')
    monkeypatch.setenv('HR_DATA_DIR',str(tmp_path))
    real_client=openai.OpenAI
    requests=[]
    outputs=[]
    def handler(request):
        requests.append(json.loads(request.content))
        body=outputs.pop(0) if outputs else {'text':'依据当前材料','evidence':[]}
        if isinstance(body,Exception):raise body
        if isinstance(body,httpx.Response):return body
        return httpx.Response(200,json={'id':'resp_test','object':'response','created_at':1,'status':'completed','model':'deepseek-flash','output':[{'id':'msg','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':json.dumps(body),'annotations':[]}]}],'usage':{'input_tokens':100,'output_tokens':20,'total_tokens':120,'input_tokens_details':{'cached_tokens':60}}})
    def client(**kwargs):
        assert kwargs['max_retries']==0
        assert kwargs['base_url']=='https://api.deepseek.com'
        assert kwargs['timeout']==90
        return real_client(**kwargs,http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(openai,'OpenAI',client)
    provider=create_provider('live')
    provider.evidence_protocol=2  # Explicit archived-v2 SDK fixtures, never production fallback.
    return provider,requests,outputs

def test_responses_nonthinking_schema_and_safe_persistent_usage(setup):
    provider,requests,outputs=setup
    assert isinstance(provider,DeepSeekProvider) and isinstance(provider,OpenAIProvider)
    assert Workflow(provider).max_repairs==0
    result=provider.generate('chat',{'context':{'runId':'r1'},'document':'PRIVATE CV BODY'},ChatResult)
    assert result['text']=='依据当前材料'
    req=requests[0]
    assert req['model']=='deepseek-flash' and req['reasoning']=={'effort':'none'}
    assert req['temperature']==0.1 and req['max_output_tokens']==6000 and req['store'] is False
    assert req['text']['format']['type']=='json_schema' and req['text']['format']['strict'] is True
    assert provider.usage_summary()=={'attempts':1,'inputTokens':100,'outputTokens':20,'cacheHitTokens':60,'cacheMissTokens':40,'unknownUsageCalls':0}
    contents=provider.usage_path.read_bytes()
    assert b'PRIVATE CV BODY' not in contents and b'dummy-offline-key' not in contents
    assert DeepSeekProvider().usage_summary()==provider.usage_summary()

def test_invalid_output_usage_recorded_and_zero_automatic_retry(setup):
    provider,requests,outputs=setup;outputs.append({'not_the_required':'schema'})
    with pytest.raises(ProviderError) as error:provider.generate('chat',{},ChatResult)
    assert error.value.code=='model_invalid_output' and len(requests)==1
    assert provider.usage_summary()['outputTokens']==20
    with sqlite3.connect(provider.usage_path) as db:assert db.execute('SELECT status FROM calls').fetchone()[0]=='invalid_output'

def test_failed_network_attempt_consumes_budget_and_unknown_usage(setup,monkeypatch):
    provider,requests,outputs=setup;provider.max_calls=1
    outputs.append(httpx.ConnectError('dummy-offline-key private detail'))
    with pytest.raises(ProviderError) as error:provider.generate('chat',{},ChatResult)
    assert error.value.code=='model_failed' and 'dummy' not in str(error.value)
    assert len(requests)==1 and provider.usage_summary()['unknownUsageCalls']==1
    with pytest.raises(ProviderError,match='limit reached'):provider.generate('chat',{},ChatResult)
    assert len(requests)==1

def test_persistent_call_cap_and_input_cap_prevent_network(setup):
    provider,requests,outputs=setup;provider.max_calls=1
    provider.generate('chat',{},ChatResult)
    another=DeepSeekProvider();another.max_calls=1
    with pytest.raises(ProviderError) as error:another.generate('chat',{},ChatResult)
    assert error.value.code=='budget_exhausted' and len(requests)==1
    provider.max_input_bytes=1000
    with pytest.raises(ProviderError) as error:provider.generate('chat',{'message':'x'*5000},ChatResult)
    assert error.value.code=='input_limit' and len(requests)==1

def test_budget_reservation_is_atomic(setup):
    provider,requests,outputs=setup;provider.max_calls=1
    def reserve():
        try:return provider._reserve('chat',{})
        except ProviderError:return 'denied'
    with ThreadPoolExecutor() as pool:results=list(pool.map(lambda _:reserve(),range(2)))
    assert results.count('denied')==1 and not requests

def test_repair_setting_requires_explicit_opt_in(setup,monkeypatch):
    provider,_,_=setup
    assert Workflow(provider).max_repairs==0
    monkeypatch.setenv('HR_MAX_REPAIRS','1');assert Workflow(provider).max_repairs==1
    monkeypatch.setenv('HR_MAX_REPAIRS','99')
    with pytest.raises(Exception,match='HR_MAX_REPAIRS'):Workflow(provider)
    monkeypatch.setenv('HR_MODEL_PROVIDER','openai')
    assert type(create_provider('live')) is OpenAIProvider
    assert create_provider('fixture').mode=='fixture'


def test_role_reasoning_overrides_defaults_and_invalid_config_before_call(setup,monkeypatch):
    provider,requests,_=setup
    provider.generate('chat',{},ChatResult)
    assert requests[-1]['reasoning']=={'effort':'none'} and requests[-1]['temperature']==0.1
    monkeypatch.setenv('HR_MODEL_MATCH_REASONING_EFFORT','high')
    monkeypatch.setenv('HR_MODEL_REVIEW_REASONING_EFFORT','high')
    provider=DeepSeekProvider()
    for role in ('match','review'):
        provider.generate(role,{},ChatResult)  # SDK transport only, no semantic fixture claim
        assert requests[-1]['reasoning']=={'effort':'high'} and 'temperature' not in requests[-1]
    monkeypatch.setenv('HR_MODEL_REASONING_EFFORT','low')
    monkeypatch.setenv('HR_MODEL_MATCH_REASONING_EFFORT','none')
    monkeypatch.setenv('HR_MODEL_REVIEW_REASONING_EFFORT','max')
    provider=DeepSeekProvider()
    for role,effort in [('match','none'),('review','max'),('interview','low')]:
        provider.generate(role,{},ChatResult)
        assert requests[-1]['reasoning']=={'effort':effort}
        assert ('temperature' in requests[-1])==(effort=='none')
    count=len(requests);attempts=provider.usage_summary()['attempts']
    for variable in ('HR_MODEL_REASONING_EFFORT','HR_MODEL_MATCH_REASONING_EFFORT','HR_MODEL_REVIEW_REASONING_EFFORT'):
        with monkeypatch.context() as patch:
            patch.setenv(variable,'INVALID_PRIVATE_SENTINEL')
            with pytest.raises(ProviderError) as error:DeepSeekProvider()
            assert error.value.code=='invalid_config' and 'INVALID_PRIVATE_SENTINEL' not in str(error.value)
    assert len(requests)==count==attempts==6
    assert provider.usage_summary()['attempts']==attempts
    assert all(r['store'] is False for r in requests)


from test_workflow import inputs


def test_larger_output_cap_is_bounded_and_default_unchanged(setup,monkeypatch):
    provider,requests,_=setup
    assert provider.max_tokens==6000
    for cap in (32768,49152,65536):
        monkeypatch.setenv('HR_MODEL_MAX_OUTPUT_TOKENS',str(cap))
        assert DeepSeekProvider().max_tokens==cap
    monkeypatch.setenv('HR_MODEL_MAX_OUTPUT_TOKENS','65537')
    with pytest.raises(ProviderError) as error:DeepSeekProvider()
    assert error.value.code=='invalid_config' and not requests


@pytest.mark.parametrize('failure',['incomplete','timeout','input_limit'])
def test_provider_failure_does_not_repeat_match_with_repair_enabled(setup,inputs,monkeypatch,failure):
    _,requests,outputs=setup
    monkeypatch.setenv('HR_MODEL_MAX_OUTPUT_TOKENS','49152')
    provider=DeepSeekProvider()
    if failure=='incomplete':
        outputs.append(httpx.Response(200,json={'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'},
            'output':[{'type':'message','content':[{'type':'output_text','text':'{"unfinished":'}]}],
            'usage':{'input_tokens':100,'output_tokens':16000}}))
        expected='model_incomplete'
    elif failure=='timeout':
        outputs.append(httpx.ReadTimeout('private timeout detail'));expected='model_failed'
    else:
        provider.max_input_bytes=1000;expected='input_limit'
    profile={'name':'Fictional','education':[],'experiences':[],'languages':[],'availability':None}
    with pytest.raises(ProviderError) as error:
        Workflow(provider,max_repairs=1).analyze(*inputs,'r','c',profile=profile)
    assert error.value.code==expected
    assert len(requests)==(0 if failure=='input_limit' else 1)
    assert provider.usage_summary()['attempts']==len(requests)
    if requests:assert requests[0]['max_output_tokens']==49152
    if failure=='incomplete':
        assert provider.usage_summary()['outputTokens']==16000
        with sqlite3.connect(provider.usage_path) as db:assert db.execute('SELECT status FROM calls').fetchone()[0]=='incomplete'


def test_input_limit_reports_measured_bytes_and_cap_without_payload(setup):
    provider,requests,_=setup
    from openai.lib._pydantic import to_strict_json_schema
    payload={'document':'PRIVATE_SENTINEL输入'*600}
    args=provider.request_arguments('chat',payload,ChatResult)
    args.update(max_output_tokens=provider.max_tokens,reasoning={'effort':provider.reasoning_effort},temperature=0.1)
    measured=len(json.dumps({**args,'text_format':to_strict_json_schema(ChatResult)},ensure_ascii=False).encode('utf-8'))
    provider.max_input_bytes=1000
    with pytest.raises(ProviderError) as error:provider.generate('chat',payload,ChatResult)
    assert error.value.code=='input_limit'
    assert str(measured) in error.value.message and '1000' in error.value.message
    assert '字节' in error.value.message and 'PRIVATE_SENTINEL' not in error.value.message
    assert not requests and provider.usage_summary()['attempts']==0

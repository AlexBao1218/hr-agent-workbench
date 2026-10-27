import json
import httpx
import pytest
from pydantic import BaseModel
from hr_backend.deepseek import DeepSeekProvider
from hr_backend.provider import ProviderError

class Output(BaseModel):
    result:str

def setup(monkeypatch,tmp_path,handler):
    monkeypatch.setenv('OPENAI_API_KEY','dummy-deepseek')
    monkeypatch.setenv('MOONSHOT_API_KEY','dummy-kimi')
    monkeypatch.setenv('HR_MODEL_MAX_CALLS','10')
    monkeypatch.setenv('HR_DATA_DIR',str(tmp_path))
    requests=[]
    original=httpx.Client
    def factory(*a,**kw):
        def send(req): requests.append(req); return handler(req)
        return original(transport=httpx.MockTransport(send))
    monkeypatch.setattr(httpx,'Client',factory)
    return DeepSeekProvider(), requests

def payload(): return {'context':{'runId':'test'},'_agentRequest':{'model':'kimi-k3.1-pro','reasoningEffort':'low','systemPrompt':'test','maxOutputTokens':800,'timeoutSeconds':10}}

def test_kimi_sends_correct_model_and_records_usage(monkeypatch,tmp_path):
    p,reqs=setup(monkeypatch,tmp_path,lambda req:httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"result":"ok"}','reasoning_content':'private'}}],'usage':{'prompt_tokens':4,'completion_tokens':5}}))
    assert p.generate('review',payload(),Output)=={'result':'ok'}
    body=json.loads(reqs[0].content); assert body['model']=='kimi-k3.1-pro' and body['reasoning_effort']=='low'
    assert reqs[0].url.host=='api.moonshot.cn' and 'temperature' not in body
    assert p.usage_summary()['attempts']==1 and p.usage_summary()['outputTokens']==5

def test_failure_is_not_retried(monkeypatch,tmp_path):
    p,reqs=setup(monkeypatch,tmp_path,lambda req:httpx.Response(429,json={'error':'ignored'}))
    with pytest.raises(ProviderError): p.generate('review',payload(),Output)
    assert len(reqs)==1 and p.usage_summary()['attempts']==1

def test_invalid_or_truncated_response_is_not_published(monkeypatch,tmp_path):
    p,reqs=setup(monkeypatch,tmp_path,lambda req:httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}))
    with pytest.raises(ProviderError) as e: p.generate('review',payload(),Output)
    assert e.value.code=='model_incomplete' and len(reqs)==1

def test_review_wrapper_normalization_does_not_change_payload():
    from hr_backend.kimi import normalize_review_wrapper
    class Reviewed: pass
    content={'matches':[{'title':'original'}]}
    original={'audit':{'verdict':'revised','findings':[],'final':content}}
    result=normalize_review_wrapper(original,Reviewed)
    assert result=={'audit':{'verdict':'revised','findings':[]},'final':content}
    ambiguous={'audit':original['audit'],'final':None}
    assert normalize_review_wrapper(ambiguous,Reviewed) is ambiguous

def test_highspeed_model_is_routed_to_kimi(monkeypatch,tmp_path):
    p,reqs=setup(monkeypatch,tmp_path,lambda req:httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"result":"ok"}'}}],'usage':{'prompt_tokens':3,'completion_tokens':4}}))
    data=payload();data['_agentRequest']['model']='kimi-k3.1-pro-highspeed'
    assert p.generate('review',data,Output)=={'result':'ok'}
    assert json.loads(reqs[0].content)['model']=='kimi-k3.1-pro-highspeed' and len(reqs)==1

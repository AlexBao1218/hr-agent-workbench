"""Kimi chat adapter for roles selected by the versioned workbench settings."""
import json, os
import httpx
from pydantic import ValidationError
from .provider import ProviderError, structured_output_error

def normalize_review_wrapper(value,schema):
    """Repair a single unambiguous provider envelope nesting without changing content."""
    if schema.__name__ != 'Reviewed' or not isinstance(value,dict) or set(value)!={'audit'}:
        return value
    audit=value.get('audit')
    if not isinstance(audit,dict) or set(audit)!={'verdict','findings','final'}:
        return value
    return {'audit':{'verdict':audit['verdict'],'findings':audit['findings']},'final':audit['final']}

def generate_kimi(provider,role,payload,schema):
    key=os.getenv('MOONSHOT_API_KEY')
    if not key: raise ProviderError('provider_unconfigured','请在服务器环境配置 MOONSHOT_API_KEY 后重试。')
    config=payload.get('_agentRequest',{})
    model=config.get('model')
    if model not in ('kimi-k3.1-pro','kimi-k3.1-pro-highspeed'): raise ProviderError('invalid_model','Unsupported Kimi model; no request sent.')
    effort=config.get('reasoningEffort','low')
    if effort not in ('low','high'): raise ProviderError('invalid_config','Kimi K3.1 requires low or high reasoning effort.')
    schema_json=schema.model_json_schema()
    system=(config.get('systemPrompt') or '')+'\n\n严格按以下JSON Schema返回单个JSON对象，不输出附加说明：\n'+json.dumps(schema_json,ensure_ascii=False,separators=(',',':'))
    body={'model':model,'reasoning_effort':effort,'max_completion_tokens':config.get('maxOutputTokens',provider.max_tokens),'stream':False,'response_format':{'type':'json_object'},'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps({k:v for k,v in payload.items() if k!='_agentRequest'},ensure_ascii=False)}]}
    raw=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()
    if len(raw)>provider.max_input_bytes: raise ProviderError('input_limit',f'模型输入为 {len(raw)} 字节，超过当前上限；本次未发送模型请求。')
    call=provider._reserve(role,payload); usage=None; status='failed'
    try:
        timeout=config.get('timeoutSeconds',provider.timeout)
        with httpx.Client(transport=httpx.HTTPTransport(retries=0),timeout=httpx.Timeout(timeout,connect=15),follow_redirects=False) as client:
            response=client.post('https://api.moonshot.cn/v1/chat/completions',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},content=raw)
            if response.status_code>=400:
                raise ProviderError('model_failed',f'Kimi 请求失败（HTTP {response.status_code}）；请检查模型权限、余额和服务器配置，不会自动重试。',response_diagnostic={'responseStatus':'failed','httpStatus':response.status_code})
            envelope=response.json(); u=envelope.get('usage') or {}; details=u.get('prompt_tokens_details') or {}; hit=details.get('cached_tokens')
            usage={'input_tokens':u.get('prompt_tokens'),'output_tokens':u.get('completion_tokens'),'input_tokens_details':{'cached_tokens':hit}}
            choice=envelope['choices'][0]
            if choice.get('finish_reason')!='stop' or not choice.get('message',{}).get('content'):
                status='incomplete'; raise ProviderError('model_incomplete','Kimi 未返回完整结果；未自动重试。')
            def strict(pairs):
                out={}
                for k,v in pairs:
                    if k in out: raise ValueError('duplicate key')
                    out[k]=v
                return out
            result=json.loads(choice['message']['content'],object_pairs_hook=strict)
            result=normalize_review_wrapper(result,schema)
            result=schema.model_validate(result).model_dump(); status='succeeded'; return result
    except ProviderError: raise
    except (ValidationError,json.JSONDecodeError,ValueError) as exc:
        status='invalid_output'; raise structured_output_error(exc,schema) from None
    except httpx.TimeoutException:
        raise ProviderError('model_timeout','Kimi 请求超时；本次未发布结果，也未自动重试。') from None
    except Exception:
        raise ProviderError('model_failed','Kimi 请求失败，请检查连接和服务器配置；未自动重试。') from None
    finally: provider._record(call,status,usage)

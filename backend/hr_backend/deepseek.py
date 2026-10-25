"""DeepSeek Responses adapter and persistent, content-free call budget ledger."""
import json
import os
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from uuid import uuid4
from pydantic import ValidationError
from .provider import ProviderError, OpenAIProvider, structured_output_error


def integer_setting(name,default,minimum,maximum):
    try:value=int(os.getenv(name,str(default)))
    except ValueError:raise ProviderError('invalid_config',f'{name} must be an integer.') from None
    if not minimum<=value<=maximum:raise ProviderError('invalid_config',f'{name} must be between {minimum} and {maximum}.')
    return value


def reasoning_setting(name,default):
    value=os.getenv(name,default)
    if value not in ('none','low','high','max'):
        raise ProviderError('invalid_config',f'{name} must be none, low, high or max.')
    return value


class DeepSeekProvider(OpenAIProvider):
    mode='live'
    default_max_repairs=0
    def __init__(self):
        self.model=os.getenv('OPENAI_MODEL') or 'deepseek-flash'
        self._key=os.getenv('OPENAI_API_KEY')
        self._base=os.getenv('OPENAI_BASE_URL') or 'https://api.deepseek.com'
        self.configured=bool(self._key and self.model)
        self.max_tokens=integer_setting('HR_MODEL_MAX_OUTPUT_TOKENS',6000,128,65536)
        self.max_input_bytes=integer_setting('HR_MODEL_MAX_INPUT_BYTES',80000,1000,1000000)
        self.max_calls=integer_setting('HR_MODEL_MAX_CALLS',6,1,10000)
        self.timeout=integer_setting('HR_MODEL_TIMEOUT_SECONDS',90,1,300)
        self.reasoning_effort=reasoning_setting('HR_MODEL_REASONING_EFFORT','none')
        self.role_reasoning={role:reasoning_setting(f'HR_MODEL_{role.upper()}_REASONING_EFFORT',self.reasoning_effort) for role in ('match','review')}
        self.usage_path=Path(os.getenv('HR_DATA_DIR',Path(__file__).resolve().parents[2]/'runtime'))/'model-usage.sqlite3'

    def configure_usage_root(self,root):self.usage_path=Path(root)/'model-usage.sqlite3'

    @contextmanager
    def _connect(self):
        self.usage_path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.usage_path,timeout=30)
        db.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, createdAt TEXT, role TEXT, runId TEXT, status TEXT, inputTokens INTEGER, outputTokens INTEGER, cacheHitTokens INTEGER, cacheMissTokens INTEGER)')
        try:
            with db:yield db
        finally:db.close()

    def _reserve(self,role,payload):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM calls').fetchone()[0]>=self.max_calls:
                raise ProviderError('budget_exhausted','Local model call limit reached. Review the usage ledger before explicitly increasing HR_MODEL_MAX_CALLS.')
            call_id=uuid4().hex
            run_id=payload.get('context',{}).get('runId','')
            if not isinstance(run_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',run_id):run_id=None
            db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?)',(call_id,datetime.now(timezone.utc).isoformat(),role,run_id,'attempted',None,None,None,None))
        return call_id

    def _record(self,call_id,status,usage=None):
        def count(name):
            value=usage.get(name) if isinstance(usage,dict) else None
            return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None
        prompt,output=count('input_tokens'),count('output_tokens')
        hit,miss=None,None
        if hit is None and usage is not None:
            details=usage.get('input_tokens_details',{})
            value=details.get('cached_tokens') if isinstance(details,dict) else None
            hit=value if isinstance(value,int) and value>=0 else None
        if miss is None and prompt is not None and hit is not None:miss=max(0,prompt-hit)
        with self._connect() as db:
            db.execute('UPDATE calls SET status=?,inputTokens=?,outputTokens=?,cacheHitTokens=?,cacheMissTokens=? WHERE id=?',(status,prompt,output,hit,miss,call_id))

    def usage_summary(self):
        with self._connect() as db:
            row=db.execute('SELECT count(*),coalesce(sum(inputTokens),0),coalesce(sum(outputTokens),0),coalesce(sum(cacheHitTokens),0),coalesce(sum(cacheMissTokens),0),coalesce(sum(CASE WHEN inputTokens IS NULL OR outputTokens IS NULL THEN 1 ELSE 0 END),0) FROM calls').fetchone()
        return dict(zip(('attempts','inputTokens','outputTokens','cacheHitTokens','cacheMissTokens','unknownUsageCalls'),row))

    def generate(self,role,payload,schema):
        if role not in ('jd','cv','match','review','interview','chat','standards_chat'):raise ProviderError('unsupported_role','Unsupported workflow role.')
        if not self.configured:raise ProviderError('provider_unconfigured','Configure the server API key and model before live inference.')
        arguments=self.request_arguments(role,payload,schema)
        config=payload.get('_agentRequest',{})
        effort=config.get('reasoningEffort',self.role_reasoning.get(role,self.reasoning_effort))
        arguments.update(max_output_tokens=config.get('maxOutputTokens',self.max_tokens),reasoning={'effort':effort})
        if effort=='none':
            temperature=config.get('temperature',0.1)
            if temperature is not None:arguments['temperature']=temperature
            else:arguments.pop('temperature',None)
        else:arguments.pop('temperature',None)
        from openai.lib._pydantic import to_strict_json_schema
        measured={**arguments,'text_format':to_strict_json_schema(schema)}
        if len(json.dumps(measured,ensure_ascii=False).encode('utf-8'))>self.max_input_bytes:
            raise ProviderError('input_limit','Model input exceeds HR_MODEL_MAX_INPUT_BYTES; narrow the input or explicitly review the cap. No call sent.')
        call_id=self._reserve(role,payload)
        usage=None;status='failed';completed=False
        try:
            from openai import OpenAI
            with OpenAI(api_key=self._key,base_url=self._base,timeout=config.get('timeoutSeconds',self.timeout),max_retries=0) as client:
                raw=client.responses.with_raw_response.parse(**arguments)
                # Capture numeric usage before SDK structure parsing can fail; never log body/text.
                envelope=raw.http_response.json()
                usage=envelope.get('usage')
                if envelope.get('status')!='completed':
                    status='incomplete'
                    raise ProviderError('model_incomplete','Model response did not complete; no automatic repair. Review output cap, timeout and provider status before a new run.')
                completed=True
                response=raw.parse()
            if response.status!='completed' or response.output_parsed is None:
                status='incomplete'
                raise ProviderError('model_refused','Model response did not provide a final structured answer; no automatic repair or assessment publication.')
            result=schema.model_validate(response.output_parsed).model_dump()
            status='succeeded'
            return result
        except ProviderError:raise
        except (json.JSONDecodeError,ValidationError) as exc:
            if not completed:
                raise ProviderError('model_failed','Provider response envelope was invalid; no automatic repair.') from None
            status='invalid_output'
            raise structured_output_error(exc,schema) from None
        except Exception:
            raise ProviderError('model_failed','DeepSeek request failed; check server configuration, API access and connectivity. No automatic retry.') from None
        finally:
            self._record(call_id,status,usage)

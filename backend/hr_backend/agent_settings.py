"""Versioned role tasks; executable policy and graph boundaries remain program-owned."""
from copy import deepcopy
import hashlib
import os
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from . import prompts
from .store import uid, version

ROLES = ('jd', 'cv', 'match', 'review', 'interview')

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class RoleSettings(StrictModel):
    systemPrompt: str = Field(min_length=1, max_length=24000)
    model: str | None = Field(default=None, min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]*$')
    reasoningEffort: Literal['none', 'low', 'medium', 'high']
    maxOutputTokens: int = Field(ge=128, le=65536)
    temperature: float | None = Field(ge=0, le=2)
    timeoutSeconds: int = Field(ge=1, le=900)

    @field_validator('systemPrompt')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('Task must not be blank')
        return value

class FlowSettings(StrictModel):
    matchRepairs: int = Field(ge=0, le=2)
    interviewRepairs: int = Field(ge=0, le=2)

class EditableConfig(StrictModel):
    roles: dict[str, RoleSettings]
    flow: FlowSettings

    @field_validator('roles')
    @classmethod
    def exact_roles(cls, value):
        if set(value) != set(ROLES): raise ValueError('Exactly five core roles required')
        return value

class DraftUpdate(StrictModel):
    expectedDraftRevision: str = Field(min_length=1, max_length=100)
    config: EditableConfig

class Activate(StrictModel):
    expectedDraftRevision: str = Field(min_length=1, max_length=100)
    expectedActiveVersion: str = Field(min_length=1, max_length=100)
    actor: str = Field(min_length=1, max_length=120)

    @field_validator('actor')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('Actor required')
        return value.strip()

class Restore(StrictModel):
    versionId: str = Field(min_length=1, max_length=100)
    expectedDraftRevision: str = Field(min_length=1, max_length=100)


def runtime_settings(provider):
    """Deliberate allowlist: never serialize provider.__dict__ or environment."""
    name = 'fixture' if provider.mode == 'fixture' else 'deepseek' if type(provider).__name__ == 'DeepSeekProvider' else 'openai'
    return dict(model=provider.model, provider=name, mode=provider.mode,
                maxOutputTokens=min(getattr(provider, 'max_tokens', 12000), 65536),
                maxInputBytes=getattr(provider, 'max_input_bytes', None),
                maxCalls=getattr(provider, 'max_calls', None),
                timeoutSeconds=min(getattr(provider, 'timeout', 90), 900))


def locked_rules(role=None):
    shared = prompts.PROJECT_DOMAIN_RULE+'\n'+prompts.PRIOR_EXPERIENCE_RULE+'\n'+prompts.VERIFICATION_SCOPE_RULE+'\n'+prompts.STRUCTURED_EVIDENCE_RULE
    return prompts.COMMON+'\n'+(shared+'\n' if role is None or role in ('match', 'review') else '')


def default_settings(provider):
    runtime = runtime_settings(provider)
    roles = {}
    for role in ROLES:
        effort = getattr(provider, 'role_reasoning', {}).get(role, getattr(provider, 'reasoning_effort', 'none'))
        if effort not in ('none', 'low', 'medium', 'high'):
            raise ValueError('Configured reasoning effort is unsupported by the agent editor; use none, low, medium or high.')
        roles[role] = dict(systemPrompt=prompts.ROLE_INSTRUCTIONS[role], model=None,
                           reasoningEffort=effort,
                           maxOutputTokens=runtime['maxOutputTokens'],
                           temperature=0.1 if runtime['provider'] == 'deepseek' and effort == 'none' else None,
                           timeoutSeconds=runtime['timeoutSeconds'])
    repairs = int(os.getenv('HR_MAX_REPAIRS', str(getattr(provider, 'default_max_repairs', 1))))
    config = EditableConfig.model_validate(dict(roles=roles, flow=dict(matchRepairs=repairs, interviewRepairs=0))).model_dump()
    return dict(version=version(1, 'initial configuration'), **config)


def initialize_settings(state, provider):
    if 'agentSettings' not in state:
        state['agentSettings'] = default_settings(provider)
    state.setdefault('agentSettingsHistory', [deepcopy(state['agentSettings'])])
    state.setdefault('agentSettingsDraft', deepcopy(state['agentSettings']))
    state.setdefault('agentSettingsDraftRevision', uid())
    # Explicit opt-in deployment migration. Preserve any existing draft and history.
    if type(provider).__name__ == 'DeepSeekProvider' and provider.mode == 'live' and os.getenv('HR_JOINT_ANALYSIS') == '1':
        marker='joint-flash-kimi-v1 deployment'
        if not any(item.get('version',{}).get('confirmedBy')==marker for item in state['agentSettingsHistory']):
            if not os.getenv('MOONSHOT_API_KEY'):
                raise ValueError('Joint analysis requires MOONSHOT_API_KEY on the server.')
            if runtime_settings(provider)['maxOutputTokens']<20000 or runtime_settings(provider)['timeoutSeconds']<600:
                raise ValueError('Joint analysis requires HR_MODEL_MAX_OUTPUT_TOKENS >= 20000 and HR_MODEL_TIMEOUT_SECONDS >= 600.')
            from .joint_analysis import DRAFT, REVIEW
            active=deepcopy(state['agentSettings'])
            for role in ('jd','cv'):
                active['roles'][role].update(model='deepseek-flash',reasoningEffort='none',temperature=0.1)
            active['roles']['match'].update(systemPrompt='joint-flash-kimi-v1\n'+DRAFT,model='deepseek-flash',reasoningEffort='none',maxOutputTokens=12000,temperature=0.1,timeoutSeconds=300)
            active['roles']['review'].update(systemPrompt='joint-flash-kimi-v1\n'+REVIEW,model='kimi-k3.1-pro',reasoningEffort='low',maxOutputTokens=20000,temperature=None,timeoutSeconds=600)
            active['flow'].update(matchRepairs=0,interviewRepairs=0)
            active['version']=version(state['agentSettings']['version']['number']+1,marker)
            state['agentSettings']=active
            state['agentSettingsHistory'].append(deepcopy(active))


def validate_caps(config, provider):
    runtime = runtime_settings(provider)
    for settings in config['roles'].values():
        if settings['maxOutputTokens'] > runtime['maxOutputTokens'] or settings['timeoutSeconds'] > runtime['timeoutSeconds']:
            raise ValueError('Role token or timeout limit exceeds the server cap; reduce it before saving.')


def snapshot_settings(state, provider):
    initialize_settings(state, provider)
    snapshot = deepcopy(state['agentSettings'])
    joint = type(provider).__name__ == 'DeepSeekProvider' and provider.mode == 'live' and os.getenv('HR_JOINT_ANALYSIS') == '1'
    snapshot['effectivePrompts'] = {role: (prompts.COMMON+'\n' if joint and role in ('match','review') else locked_rules(role))+'Active role: '+role+'\n'+snapshot['roles'][role]['systemPrompt'] for role in ROLES}
    snapshot['promptHashes'] = {role: hashlib.sha256(text.encode()).hexdigest() for role, text in snapshot['effectivePrompts'].items()}
    snapshot['runtime'] = runtime_settings(provider)
    snapshot['runtime']['jointAnalysis'] = joint
    return snapshot


def cv_settings_hash(snapshot):
    """Cache profile extraction only across identical effective CV settings."""
    if not snapshot:return None
    import json
    role=deepcopy(snapshot['roles']['cv'])
    role['model']=role.get('model') or snapshot['runtime']['model']
    return hashlib.sha256(json.dumps({'inputProtocol':'cv-source-only-v2','prompt':snapshot['effectivePrompts']['cv'],'settings':role},sort_keys=True).encode()).hexdigest()

from copy import deepcopy
import pytest
from hr_backend.agent_settings import default_settings,initialize_settings
from hr_backend.deepseek import DeepSeekProvider

def make_provider(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','fixture-key')
    monkeypatch.setenv('HR_MODEL_MAX_OUTPUT_TOKENS','32768')
    monkeypatch.setenv('HR_MODEL_TIMEOUT_SECONDS','600')
    return DeepSeekProvider()

def test_opt_in_migrates_once_preserving_existing_draft(monkeypatch):
    monkeypatch.delenv('HR_JOINT_ANALYSIS',raising=False); p=make_provider(monkeypatch);state={};initialize_settings(state,p)
    state['agentSettingsDraft']['roles']['cv']['systemPrompt']='HR draft to preserve';draft=deepcopy(state['agentSettingsDraft']);revision=state['agentSettingsDraftRevision']
    monkeypatch.setenv('HR_JOINT_ANALYSIS','1');monkeypatch.setenv('MOONSHOT_API_KEY','fixture-key')
    initialize_settings(state,p)
    assert state['agentSettingsDraft']==draft and state['agentSettingsDraftRevision']==revision
    assert state['agentSettings']['roles']['review']['model']=='kimi-k3.1-pro'
    assert state['agentSettings']['roles']['review']['timeoutSeconds']==600
    assert state['agentSettings']['roles']['match']['model']=='deepseek-flash'
    active=deepcopy(state['agentSettings']);initialize_settings(state,p);assert state['agentSettings']==active and len(state['agentSettingsHistory'])==2

def test_opt_in_without_key_fails_clearly(monkeypatch):
    monkeypatch.setenv('HR_JOINT_ANALYSIS','1');monkeypatch.delenv('MOONSHOT_API_KEY',raising=False);p=make_provider(monkeypatch)
    with pytest.raises(ValueError,match='MOONSHOT_API_KEY'):initialize_settings({},p)

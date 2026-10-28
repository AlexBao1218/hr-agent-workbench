from copy import deepcopy
import pytest
from hr_backend.agent_settings import initialize_settings, snapshot_settings
from hr_backend.deepseek import DeepSeekProvider


def base(monkeypatch):
    monkeypatch.delenv('HR_ANALYSIS_PROTOCOL', raising=False)
    monkeypatch.delenv('HR_JOINT_ANALYSIS', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-key')
    monkeypatch.setenv('HR_MODEL_MAX_OUTPUT_TOKENS', '32768')
    monkeypatch.setenv('HR_MODEL_TIMEOUT_SECONDS', '600')
    provider=DeepSeekProvider();state={};initialize_settings(state,provider)
    state['agentSettingsDraft']['roles']['cv']['systemPrompt']='Keep HR draft'
    state['candidates']={'example':{'assessmentId':'unchanged','hrReview':{'decision':'consider'}}}
    return provider,state


def test_source_v4_explicit_hosted_activation_is_once_and_preserves_data(monkeypatch):
    p,s=base(monkeypatch);before=deepcopy(s)
    monkeypatch.setenv('HR_ANALYSIS_PROTOCOL','source-v4')
    monkeypatch.setenv('MOONSHOT_API_KEY','fixture-key')
    initialize_settings(s,p);first=deepcopy(s)
    snap=snapshot_settings(s,p)
    assert s==first and len(s['agentSettingsHistory'])==2
    for key in before:
        if key not in ('agentSettings','agentSettingsHistory'):assert s[key]==before[key]
    assert snap['runtime']['analysisProtocol']=='source-v4' and snap['runtime']['jointAnalysis']
    assert snap['roles']['review']['model']=='kimi-k3.1-pro'
    assert snap['roles']['review']['systemPrompt'].startswith('joint-source-v4\n')
    assert snap['flow']=={'matchRepairs':0,'interviewRepairs':0}


@pytest.mark.parametrize('fault',['key','tokens','timeout'])
def test_source_deploy_missing_requirements_do_not_change_active(monkeypatch,fault):
    p,s=base(monkeypatch);before=deepcopy(s)
    monkeypatch.setenv('HR_ANALYSIS_PROTOCOL','source-v4')
    monkeypatch.setenv('MOONSHOT_API_KEY','fixture-key')
    if fault=='key':monkeypatch.delenv('MOONSHOT_API_KEY')
    elif fault=='tokens':p.max_tokens=12000
    else:p.timeout=300
    with pytest.raises(ValueError):initialize_settings(s,p)
    assert s==before

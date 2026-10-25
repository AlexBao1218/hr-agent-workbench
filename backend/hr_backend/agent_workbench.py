"""Local role settings and bounded, indexed, read-only run diagnostics."""
from copy import deepcopy
import json
from pathlib import Path
from fastapi.responses import JSONResponse
from .agent_settings import (Activate, DraftUpdate, Restore, ROLES, default_settings,
    initialize_settings, locked_rules, runtime_settings, validate_caps)
from .schemas import JDResult, CandidateProfile, MatchResult, ReviewResult, InterviewResult
from .store import uid, version

MAX_DIAGNOSTIC_BYTES = 4 * 1024 * 1024
SCHEMAS = dict(zip(ROLES, (JDResult, CandidateProfile, MatchResult, ReviewResult, InterviewResult)))
PUBLIC_RUN_FIELDS = ('id','status','mode','standardsVersion','hatVersion','steps','error','createdAt')
# Diagnostics are structured evidence, never provider HTTP bodies/private reasoning.
PRIVATE_KEYS = {'apikey','authorization','secret','password','token','accesstoken','refreshtoken','reasoning','reasoningcontent','chainofthought','httpbody','rawresponse','providerresponse'}


def public_data(value):
    if isinstance(value, dict):
        return {key: public_data(item) for key, item in value.items()
                if not key.startswith('_') and ''.join(c for c in key.lower() if c.isalnum()) not in PRIVATE_KEYS}
    if isinstance(value, list): return [public_data(item) for item in value]
    return value


def error(code, message, status):
    return JSONResponse({'error': {'code': code, 'message': message}}, status_code=status)


def workbench_view(state, provider):
    return deepcopy(dict(active=state['agentSettings'], draft=state['agentSettingsDraft'],
        draftRevision=state['agentSettingsDraftRevision'], history=state['agentSettingsHistory'],
        defaults=default_settings(provider), lockedRules=locked_rules(), lockedRulesByRole={role:locked_rules(role) for role in ROLES},
        schemas={role: schema.model_json_schema() for role, schema in SCHEMAS.items()},
        runtime=runtime_settings(provider)))


def read_artifacts(root, run):
    directory = (Path(root)/'diagnostics').resolve()
    artifacts, total, truncated = [], 0, False
    references = run.get('_diagnosticFiles', [])
    if not isinstance(references, list): return [], True
    for reference in references[:1000]:
        try:
            if not isinstance(reference, str): raise ValueError()
            relative = Path(reference)
            if relative.is_absolute() or '..' in relative.parts or len(relative.parts) != 2 or relative.parts[0] != 'diagnostics' or relative.suffix != '.json':
                raise ValueError()
            path = Path(root)/relative
            if path.is_symlink() or path.resolve().parent != directory: raise ValueError()
            # Also reject redirecting the entire diagnostics directory outside the store.
            if directory.parent != Path(root).resolve(): raise ValueError()
            size = path.stat().st_size
            if size > MAX_DIAGNOSTIC_BYTES-total:
                truncated = True
                continue
            with path.open('rb') as file: raw = file.read(MAX_DIAGNOSTIC_BYTES-total+1)
            total += len(raw)
            if total > MAX_DIAGNOSTIC_BYTES:
                truncated = True
                break
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get('runId') != run['id'] or data.get('role') not in ROLES:
                raise ValueError()
            artifacts.append(dict(reference=reference, role=data['role'], attempt=data.get('attempt',1), kind=data.get('kind','diagnostic'), data=public_data(data)))
        except (OSError, ValueError, UnicodeError, RecursionError):
            truncated = True
    return artifacts, truncated or len(references)>1000


def install_workbench(app, store, provider):
    with store.transaction() as state: initialize_settings(state, provider)

    @app.get('/api/agent-workbench')
    def get_settings(): return workbench_view(store.read(), provider)

    @app.put('/api/agent-workbench/draft')
    def save_draft(body: DraftUpdate):
        config = body.config.model_dump()
        try: validate_caps(config, provider)
        except ValueError as exc: return error('invalid_agent_settings', str(exc), 422)
        with store.transaction() as state:
            if body.expectedDraftRevision != state['agentSettingsDraftRevision']:
                return error('draft_conflict', 'Draft changed. Reload before saving.', 409)
            state['agentSettingsDraft'] = dict(version=version(), **config)
            state['agentSettingsDraftRevision'] = uid()
            return workbench_view(state, provider)

    @app.post('/api/agent-workbench/activate')
    def activate(body: Activate):
        with store.transaction() as state:
            if body.expectedDraftRevision != state['agentSettingsDraftRevision'] or body.expectedActiveVersion != state['agentSettings']['version']['id']:
                return error('settings_conflict', 'Active settings or draft changed. Reload before activating.', 409)
            try: validate_caps(state['agentSettingsDraft'], provider)
            except ValueError as exc: return error('invalid_agent_settings', str(exc), 422)
            active = deepcopy(state['agentSettingsDraft'])
            active['version'] = version(state['agentSettings']['version']['number']+1, body.actor)
            state['agentSettings'] = active
            state['agentSettingsHistory'].append(deepcopy(active))
            state['agentSettingsDraft'] = deepcopy(active)
            state['agentSettingsDraftRevision'] = uid()
            return workbench_view(state, provider)

    @app.post('/api/agent-workbench/restore')
    def restore(body: Restore):
        with store.transaction() as state:
            if body.expectedDraftRevision != state['agentSettingsDraftRevision']:
                return error('draft_conflict', 'Draft changed. Reload before restoring.', 409)
            previous = next((item for item in state['agentSettingsHistory'] if item['version']['id']==body.versionId), None)
            if previous is None: return error('not_found', 'Settings version not found.', 404)
            state['agentSettingsDraft'] = deepcopy(previous)
            state['agentSettingsDraft']['version'] = version()
            state['agentSettingsDraftRevision'] = uid()
            return workbench_view(state, provider)

    @app.get('/api/agent-workbench/runs')
    def list_runs():
        state = store.read()
        items = []
        for run in sorted(state['runs'].values(), key=lambda r:r.get('createdAt',''), reverse=True)[:30]:
            ids = list(dict.fromkeys(run.get('_candidateIds', []) + [step['candidateId'] for step in run.get('steps',[]) if step.get('candidateId')]))
            names = [(state['candidates'][cid].get('profile') or {}).get('name') or state['candidates'][cid].get('name',cid) for cid in ids if cid in state['candidates']]
            snapshot = run.get('_agentSettings')
            items.append(dict(id=run['id'],status=run['status'],createdAt=run['createdAt'],candidateNames=names,
                              settingsVersion=snapshot['version']['id'] if snapshot else None,hasSnapshot=bool(snapshot)))
        return items

    @app.get('/api/agent-workbench/runs/{rid}')
    def inspect_run(rid: str):
        run = store.read()['runs'].get(rid)
        if run is None: return error('not_found', 'Run not found.', 404)
        artifacts, truncated = read_artifacts(store.root, run)
        snapshot = run.get('_agentSettings')
        return dict(run=public_data({key:run[key] for key in PUBLIC_RUN_FIELDS if key in run}),
                    settings=deepcopy(snapshot), historicalWarning=None if snapshot else '此历史运行未保存 Agent 设置快照，无法重建当时的提示词或参数。',
                    artifacts=artifacts, truncated=truncated)

"""Recoverable removal uses no inference and never loses the original records."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from hr_backend.app import create_app
from hr_backend.provider import FixtureProvider

JD = 'Build reliable AI workflows.'
CRITERIA = [dict(id='build', title='AI workflows', description=JD, group='must', priority=0, jdQuote=JD)]

def parser(path, out, did):
    text = Path(path).read_text()
    return dict(id=did, filename=Path(path).name, mimeType='application/pdf', status='ready', previewKind='text', pages=[dict(number=1, width=600, height=800, imagePath=None)], blocks=[dict(id=did+'-b1', page=1, text=text, box=None, method='native', confidence=None)], warnings=[], text=text)

class RecordingProvider(FixtureProvider):
    def __init__(self): self.roles = []
    def generate(self, role, payload, schema):
        self.roles.append(role)
        return super().generate(role, payload, schema)

@pytest.fixture
def ready(tmp_path):
    provider = RecordingProvider()
    app = create_app(tmp_path, provider, parser)
    client = TestClient(app)
    assert client.put('/api/standards/draft', json={'jdText':JD, 'criteria':CRITERIA}).status_code == 200
    assert client.post('/api/standards/confirm', json={'actor':'test'}).status_code == 200
    data = client.post('/api/candidates', files={'files':('removal-test.pdf', b'Fictional Removal Test\nBuilt reliable AI workflows using Python.', 'application/pdf')}).json()
    cid = data['candidateIds'][0]
    assert client.get('/api/candidates/'+cid).json()['candidate']['stage'] == 'ready'
    provider.roles.clear()
    return app, client, provider, cid, tmp_path

def delete(client, cid):
    return client.delete('/api/candidates/'+cid+'?permanent=true')


def test_permanent_delete_files_records_restart_and_reupload(ready):
    app, client, provider, cid, root = ready
    client.put('/api/candidates/'+cid+'/hr-review',json={'decision':'consider','note':'PRIVATE_DELETION_NOTE'})
    before = app.state.store.read();document = before['documents'][before['candidates'][cid]['documentId']]
    path = Path(document['path']);assert path.exists()
    response = delete(client,cid)
    assert response.status_code == 200 and response.json() == {'candidateId':cid,'deleted':True}
    assert delete(client,cid).json() == response.json()
    assert client.get('/api/candidates/'+cid).status_code == 404
    assert client.get('/api/documents/'+document['id']+'/original').status_code == 404
    assert not path.exists()
    state = app.state.store.read()
    assert cid not in state['candidates'] and document['id'] not in state['documents']
    assert not state['assessments'] and not state['chats'] and not state['feedback']
    assert not list((root/'diagnostics').glob('*.json'))
    assert 'PRIVATE_DELETION_NOTE' not in str(state)
    assert provider.roles == []
    reopened = TestClient(create_app(root,provider,parser))
    assert reopened.get('/api/candidates').json()['candidates'] == []
    assert reopened.post('/api/candidates/'+cid+'/restore').status_code == 404
    assert reopened.get('/api/removed-candidates').status_code == 404
    result=reopened.post('/api/candidates',files={'files':('same.pdf',b'Fictional Removal Test\nBuilt reliable AI workflows using Python.','application/pdf')}).json()
    assert not result['errors'] and result['candidateIds'][0] != cid


def test_legacy_requests_and_unknown_ids_are_safe(ready):
    app,client,provider,cid,_=ready;before=app.state.store.read()
    assert client.delete('/api/candidates/'+cid).status_code == 409
    assert client.delete('/api/candidates/'+cid+'?permanent=false').status_code == 409
    assert delete(client,'missing').status_code == 404
    assert app.state.store.read() == before and provider.roles == []


def test_shared_run_preserves_other_candidate_and_diagnostics(ready):
    app,client,provider,first,root=ready
    batch=client.post('/api/candidates',files=[('files',('one.pdf',b'PRIVATE_DELETE_PERSON\nBuilt AI workflows.','application/pdf')),('files',('two.pdf',b'Keep Other Person\nBuilt Python tools.','application/pdf'))]).json()
    cid,other=batch['candidateIds'];rid=batch['runId']
    kept=client.get('/api/candidates/'+other).json();provider.roles.clear()
    assert delete(client,cid).status_code == 200
    assert client.get('/api/candidates/'+other).json()==kept
    state=app.state.store.read();run=state['runs'][rid]
    assert cid not in run['_candidateIds'] and cid not in run['_inputs']['candidateIds']
    assert all(step.get('candidateId') != cid for step in run['steps'])
    assert 'PRIVATE_DELETE_PERSON' not in str(state)
    for path in (root/'diagnostics').glob('*.json'):
        assert 'PRIVATE_DELETE_PERSON' not in path.read_text()
    assert len(client.get('/api/candidates').json()['candidates'])==2 and provider.roles==[]


@pytest.mark.parametrize('during_call',[False,True])
def test_delete_stops_inflight_work_and_prevents_recreation(ready,during_call):
    app,client,provider,cid,root=ready
    started,release=Event(),Event()
    if during_call:
        original=provider.generate
        def blocked(role,payload,schema):
            if role=='cv':started.set();assert release.wait(10)
            return original(role,payload,schema)
        provider.generate=blocked
    else:
        original=app.state.workflow.analyze
        def blocked(*args,**kwargs):
            started.set();assert release.wait(10)
            return original(*args,**kwargs)
        app.state.workflow.analyze=blocked
    with ThreadPoolExecutor() as pool:
        future=pool.submit(client.post,'/api/candidates/'+cid+'/analyze')
        assert started.wait(10)
        try:assert delete(client,cid).status_code==200
        finally:release.set()
        assert future.result().status_code==200
    state=app.state.store.read()
    assert cid not in state['candidates'] and not state['assessments']
    assert not state['documents'] and not list((root/'diagnostics').glob('*.json'))
    assert provider.roles == (['cv'] if during_call else [])


def test_storage_failure_is_retryable_without_false_success(ready,monkeypatch):
    app,client,provider,cid,_=ready
    original=app.state.store.delete_candidate_diagnostics
    def fail(*a):raise OSError('private storage error')
    monkeypatch.setattr(app.state.store,'delete_candidate_diagnostics',fail)
    response=delete(client,cid)
    assert response.status_code==503 and 'private storage error' not in response.text
    pending=client.get('/api/candidates').json()['candidates']
    assert len(pending)==1 and pending[0]['deletionPending'] is True
    assert pending[0]['grade']=='pending'
    assert app.state.store.read()['candidates'][cid]['_deletePending'] is True
    monkeypatch.setattr(app.state.store,'delete_candidate_diagnostics',original)
    assert delete(client,cid).status_code==200 and provider.roles==[]

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

def test_remove_restore_preserves_records_and_survives_restart(ready):
    app, client, provider, cid, root = ready
    note = client.put('/api/candidates/'+cid+'/hr-review', json={'decision':'consider','note':'Keep this HR note.'}).json()
    original = client.get('/api/candidates/'+cid).json()
    source = client.get(original['document']['originalUrl']).content
    before = app.state.store.read()
    removed = client.delete('/api/candidates/'+cid)
    assert removed.status_code == 200
    assert removed.json()['candidateId'] == cid and removed.json()['removedAt']
    assert client.delete('/api/candidates/'+cid).json() == removed.json()
    assert client.get('/api/candidates').json()['candidates'] == []
    assert client.get('/api/candidates/'+cid).status_code == 404
    assert client.post('/api/candidates/'+cid+'/analyze').status_code == 404
    assert client.put('/api/candidates/'+cid+'/hr-review', json={'decision':None,'note':''}).status_code == 404
    archived = client.get('/api/removed-candidates').json()['candidates']
    assert archived == [{'id':cid,'name':original['candidate']['name'],'removedAt':removed.json()['removedAt']}]
    after = app.state.store.read()
    for key in ('documents','assessments','chats','feedback'):
        assert after[key] == before[key]
    assert client.get(original['document']['originalUrl']).content == source
    reopened = TestClient(create_app(root, provider, parser))
    assert reopened.get('/api/candidates').json()['candidates'] == []
    assert reopened.get('/api/removed-candidates').json()['candidates'] == archived
    assert reopened.post('/api/candidates/'+cid+'/restore').status_code == 200
    assert reopened.post('/api/candidates/'+cid+'/restore').status_code == 200
    restored = reopened.get('/api/candidates/'+cid).json()
    assert restored['assessment'] == original['assessment']
    assert restored['candidate']['hrReview'] == note
    assert restored['candidate']['stage'] == 'ready'
    assert restored['document'] == original['document']
    assert reopened.get('/api/removed-candidates').json()['candidates'] == []
    assert provider.roles == []

def test_removed_duplicate_upload_points_to_restore(ready):
    app, client, provider, cid, _ = ready
    client.delete('/api/candidates/'+cid)
    data = client.post('/api/candidates', files={'files':('again.pdf', b'Fictional Removal Test\nBuilt reliable AI workflows using Python.', 'application/pdf')}).json()
    assert not data['candidateIds'] and data['runId'] is None
    assert '恢复' in data['errors'][0]['message']
    assert len(app.state.store.read()['candidates']) == 1 and provider.roles == []

@pytest.mark.parametrize('change', ['standards','hat'])
def test_removed_candidates_are_excluded_from_automatic_analysis(ready, change):
    app, client, provider, cid, _ = ready
    client.delete('/api/candidates/'+cid)
    if change == 'standards':
        criteria = deepcopy(CRITERIA); criteria[0]['description'] += ' independently'
        assert client.put('/api/standards/draft',json={'jdText':JD,'criteria':criteria}).status_code == 200
        response = client.post('/api/standards/confirm',json={'actor':'test'})
    else:
        rules = client.get('/api/workspace').json()['hat']['rules']
        rules[0]['text'] += ' Test clarification.'
        assert client.put('/api/hat/draft',json={'rules':rules}).status_code == 200
        response = client.post('/api/hat/confirm',json={'actor':'test'})
    assert response.status_code == 200 and response.json()['runId'] is None
    client.post('/api/candidates/'+cid+'/restore')
    restored = client.get('/api/candidates/'+cid).json()['candidate']
    assert restored['stage'] == 'needs_attention' and restored['grade'] == 'pending'
    assert '重新分析' in restored['error'] and provider.roles == []

def test_inflight_removed_and_restored_run_cannot_call_or_publish(ready):
    app, client, provider, cid, _ = ready
    started, release = Event(), Event()
    original = app.state.workflow.analyze
    def blocked(*args, **kwargs):
        started.set(); assert release.wait(10)
        return original(*args, **kwargs)
    app.state.workflow.analyze = blocked
    before = app.state.store.read()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(client.post, '/api/candidates/'+cid+'/analyze')
        assert started.wait(10)
        try:
            assert client.delete('/api/candidates/'+cid).status_code == 200
            assert client.post('/api/candidates/'+cid+'/restore').status_code == 200
        finally:
            release.set()
        rid = future.result().json()['runId']
    assert client.get('/api/runs/'+rid).json()['status'] == 'failed'
    after = app.state.store.read()
    assert set(after['assessments']) == set(before['assessments'])
    assert client.get('/api/candidates/'+cid).json()['candidate']['stage'] == 'needs_attention'
    assert provider.roles == []
    # The cancelled run is not reused when explicitly analyzing after restoration.
    app.state.workflow.analyze = original
    response = client.post('/api/candidates/'+cid+'/analyze').json()
    assert response['runId'] != rid
    assert client.get('/api/candidates/'+cid).json()['candidate']['stage'] == 'ready'

def test_unknown_ids_do_not_change_state(ready):
    app, client, provider, _, _ = ready
    before = app.state.store.read()
    assert client.delete('/api/candidates/missing').status_code == 404
    assert client.post('/api/candidates/missing/restore').status_code == 404
    assert app.state.store.read() == before and provider.roles == []

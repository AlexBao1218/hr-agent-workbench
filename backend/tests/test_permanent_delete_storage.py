"""Offline remote-adapter deletion tests; no external database or private files."""
from contextlib import contextmanager
import json
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from hr_backend.app import create_app
from hr_backend.blob_store import SupabaseBlobStore,BlobStoreError
from hr_backend.postgres_store import PostgresStore
from hr_backend.store import Store,uid
from test_candidate_removal import RecordingProvider,JD,CRITERIA,delete


def test_remote_blob_delete_is_acknowledged_and_validated(tmp_path):
    requests=[];fail=[True]
    def handler(request):
        requests.append(request)
        assert request.method=='DELETE' and request.url.path=='/storage/v1/object/private-cvs'
        assert json.loads(request.content)=={'prefixes':['uploads/one.pdf','previews/one.png']}
        return httpx.Response(503 if fail[0] else 200,json=[])
    blobs=SupabaseBlobStore(tmp_path,'https://example.supabase.co','test-only-key','private-cvs',transport=httpx.MockTransport(handler))
    for key in ('uploads/one.pdf','previews/one.png'):blobs._cache(key,b'cached')
    with pytest.raises(BlobStoreError):blobs.delete_many(['uploads/one.pdf','previews/one.png'])
    assert (tmp_path/'uploads/one.pdf').exists()
    fail[0]=False;blobs.delete_many(['uploads/one.pdf','previews/one.png'])
    assert not (tmp_path/'uploads/one.pdf').exists() and not (tmp_path/'previews/one.png').exists()
    count=len(requests)
    with pytest.raises(ValueError):blobs.delete_many(['../outside'])
    assert len(requests)==count


def test_remote_diagnostic_delete_is_parameterized():
    calls=[]
    class Cursor:
        def execute(self,sql,params):calls.append((sql,params))
        def fetchall(self):return [('diagnostics/a.json',),('diagnostics/b.json',)]
    @contextmanager
    def transaction():yield Cursor()
    store=object.__new__(PostgresStore);store.sql_transaction=transaction
    assert store.delete_candidate_diagnostics('candidate-one',['run-one'])=={'diagnostics/a.json','diagnostics/b.json'}
    assert calls[0][1]==(['run-one'],'candidate-one')
    assert 'RETURNING reference' in calls[0][0] and 'candidate-one' not in calls[0][0]


def test_remote_original_preview_diagnostics_deleted_other_candidate_kept(tmp_path,monkeypatch):
    class RemoteStore(Store):
        is_remote=True
        def __init__(self,root):super().__init__(root);self.artifacts={}
        def write_diagnostic(self,artifact):
            key='diagnostics/'+uid()+'.json';self.artifacts[key]=json.dumps(artifact).encode();return key
        def read_diagnostic(self,key):return self.artifacts[key]
        def delete_candidate_diagnostics(self,cid,owned):
            refs={key for key,value in self.artifacts.items() if (a:=json.loads(value)).get('candidateId')==cid or a.get('runId') in owned}
            for key in refs:self.artifacts.pop(key)
            return refs
        def close(self):pass
    state=RemoteStore(tmp_path/'state');objects={};deletes=[]
    def handler(request):
        prefix='/storage/v1/object/private-cvs/'
        if request.method=='POST':objects[request.url.path.removeprefix(prefix)]=request.content;return httpx.Response(200,json={})
        if request.method=='DELETE':
            keys=json.loads(request.content)['prefixes'];deletes.extend(keys)
            for key in keys:objects.pop(key,None)
            return httpx.Response(200,json=[])
        raise AssertionError(request.method)
    blobs=SupabaseBlobStore(tmp_path/'cache','https://example.supabase.co','test-only-key','private-cvs',transport=httpx.MockTransport(handler))
    monkeypatch.setattr('hr_backend.storage.create_storage',lambda *a:(state,blobs))
    def parser(path,out,did):
        text=Path(path).read_text();preview=Path(out)/'page.png';preview.parent.mkdir(parents=True,exist_ok=True);preview.write_bytes(b'preview')
        return dict(id=did,status='ready',previewKind='rendered',pages=[dict(number=1,width=600,height=800,imagePath=str(preview))],blocks=[dict(id=did+'-b',page=1,text=text,box=None,method='native',confidence=None)],warnings=[],text=text)
    provider=RecordingProvider();client=TestClient(create_app(tmp_path/'state',provider,parser))
    client.put('/api/standards/draft',json={'jdText':JD,'criteria':CRITERIA});client.post('/api/standards/confirm',json={'actor':'test'})
    batch=client.post('/api/candidates',files=[('files',('one.pdf',b'PRIVATE_DELETE_PERSON\nBuilt Python tools.','application/pdf')),('files',('two.pdf',b'Keep Other Person\nBuilt Python tools.','application/pdf'))]).json()
    cid,other=batch['candidateIds'];before=state.read();doc=before['documents'][before['candidates'][cid]['documentId']]
    keys={doc['blobKey'],doc['pages'][0]['imageBlobKey']};assert keys <= objects.keys()
    kept=client.get('/api/candidates/'+other).json();provider.roles.clear()
    response=delete(client,cid);assert response.status_code==200,response.text
    assert keys <= set(deletes) and not keys.intersection(objects)
    assert client.get('/api/candidates/'+other).json()==kept
    assert all('PRIVATE_DELETE_PERSON' not in value.decode() for value in state.artifacts.values())
    assert provider.roles==[]

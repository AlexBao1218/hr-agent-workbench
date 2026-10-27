"""Offline transfer/provenance integration; synthetic adapters, no paid calls."""
from copy import deepcopy
import hashlib
import pytest
from fastapi.testclient import TestClient
from hr_backend.app import create_app
from hr_backend.verified_import import checked
from test_candidate_reading import case,Provider


@pytest.fixture
def transfer(tmp_path):
    doc,std,hat,profile,data=case()
    def parser(path,out,did):return {**deepcopy(doc),'id':did,'mimeType':'application/pdf','previewKind':'text','pages':[],'warnings':[]}
    source_provider=Provider(data,profile);source=create_app(tmp_path/'source',source_provider,parser);sc=TestClient(source)
    sc.put('/api/standards/draft',json={k:std[k] for k in ('jdText','criteria')});sc.post('/api/standards/confirm',json={'actor':'offline fixture'})
    content=b'fictional offline transfer bytes'
    s=sc.post('/api/candidates',data={'fictional':'true'},files={'files':('fictional.pdf',content,'application/pdf')}).json();sid=s['candidateIds'][0]
    detail=sc.get('/api/candidates/'+sid).json();debug=sc.get('/api/agent-workbench/runs/'+s['runId']).json();assert debug['run']['status']=='succeeded'
    target_provider=Provider(data,profile);target=create_app(tmp_path/'target',target_provider,parser);tc=TestClient(target)
    original=source.state.store.read()
    with target.state.store.transaction() as state:
        for key in ('standards','standardsHistory','hat','hats','agentSettings','agentSettingsDraft','agentSettingsHistory'):
            state[key]=deepcopy(original[key])
    t=tc.post('/api/candidates',data={'fictional':'true'},files={'files':('fictional.pdf',content,'application/pdf')}).json();cid=t['candidateIds'][0]
    tc.put('/api/candidates/'+cid+'/hr-review',json={'decision':'consider','note':'Keep this owner note.'})
    td=tc.get('/api/candidates/'+cid).json();target_provider.roles.clear()
    payload={'candidateId':cid,'expectedAssessmentId':td['assessment']['id'],'originalSHA256':hashlib.sha256(content).hexdigest(),
        'standards':original['standards'],'hat':original['hat'],'sourceDocument':detail['document'],'sourceDebug':debug,
        'sourceUsage':{'modelRequests':len(source_provider.roles)}}
    return target,tc,target_provider,payload,content


def test_import_revalidates_live_trace_and_recomputes_grade_without_inference(transfer):
    app,client,provider,payload,content=transfer;cid=payload['candidateId'];before=app.state.store.read();payload['grade']='A'
    response=client.post('/api/agent-workbench/import-verified-run',json=payload)
    assert response.status_code==200,response.text
    receipt=response.json();assert receipt['imported'] and receipt['modelCalls']==0 and provider.roles==[]
    detail=client.get('/api/candidates/'+cid).json();a=detail['assessment'];run=client.get('/api/runs/'+receipt['runId']).json()
    assert a['runId']==receipt['runId'] and a['reviewed'] and a['status']=='current' and a['grade']=='B'
    assert detail['candidate']['stage']=='ready' and len(a['questions'])==3
    assert detail['candidate']['hrReview']['note']=='Keep this owner note.'
    assert client.get(detail['document']['originalUrl']).content==content
    assert len(app.state.store.read()['candidates'])==len(before['candidates'])
    assert run['status']=='succeeded' and all(s['status']=='skipped' for s in run['steps'])
    debug=client.get('/api/agent-workbench/runs/'+receipt['runId']).json()
    proof=next(a['data'] for a in debug['artifacts'] if a['kind']=='verified_live_import_provenance')
    assert proof['sourceRunId']==payload['sourceDebug']['run']['id'] and proof['providerCalled'] is False
    assert client.post('/api/agent-workbench/import-verified-run',json=payload).json()==receipt


@pytest.mark.parametrize('kind',['hash','standards','hat','settings','source_failed','fixture','quote','missing_trace','changed_questions','stale_assessment','active_work','extraction'])
def test_invalid_transfers_never_write_or_call_model(transfer,kind):
    app,client,provider,payload,_=transfer;p=deepcopy(payload)
    if kind=='hash':p['originalSHA256']='0'*64
    elif kind=='standards':p['standards']['criteria'][0]['description']='A changed requirement'
    elif kind=='hat':p['hat']['version']['id']='changed'
    elif kind=='settings':p['sourceDebug']['settings']['roles']['match']['maxOutputTokens']+=1
    elif kind=='source_failed':p['sourceDebug']['run']['status']='failed'
    elif kind=='fixture':p['sourceDebug']['run']['mode']='fixture'
    elif kind=='stale_assessment':p['expectedAssessmentId']='changed'
    elif kind=='extraction':p['sourceDocument']['blocks'][0]['text']='An invented original'
    elif kind=='missing_trace':p['sourceDebug']['artifacts']=[a for a in p['sourceDebug']['artifacts'] if a['kind']!='review_decision']
    elif kind=='quote':
        a=next(a for a in p['sourceDebug']['artifacts'] if a['kind']=='role_output' and a['role']=='match')
        a['data']['output']['rows'][0]['evidence'][0]['evidence'][0]['quote']='Invented HR production success'
    elif kind=='changed_questions':
        a=next(a for a in p['sourceDebug']['artifacts'] if a['kind']=='role_output' and a['role']=='interview')
        a['data']['output']['questions'][0]['question']='An unvalidated new question'
    elif kind=='active_work':
        with app.state.store.transaction() as state:state['runs']['other']={'status':'running','_candidateIds':[p['candidateId']]}
    before=app.state.store.read()
    response=client.post('/api/agent-workbench/import-verified-run',json=p)
    assert response.status_code==409,response.text
    assert app.state.store.read()==before and provider.roles==[]


def test_failure_before_publication_is_actionable_and_preserves_owner_data(transfer,monkeypatch):
    app,client,provider,payload,content=transfer;cid=payload['candidateId'];old=client.get('/api/candidates/'+cid).json()['assessment']['id']
    def broken(*args,**kwargs):raise OSError('Injected offline storage failure')
    monkeypatch.setitem(app.state.resume_hooks,'trace',broken)
    response=client.post('/api/agent-workbench/import-verified-run',json=payload)
    assert response.status_code==503
    detail=client.get('/api/candidates/'+cid).json()
    assert detail['candidate']['stage']=='needs_attention' and 'verified_transfer_failed' in detail['candidate']['error']
    assert detail['assessment']['id']==old and detail['candidate']['hrReview']['note']=='Keep this owner note.'
    assert client.get(detail['document']['originalUrl']).content==content and provider.roles==[]


def test_lost_response_after_publication_returns_replayable_success_receipt(transfer,monkeypatch):
    app,client,provider,payload,_=transfer;original=app.state.resume_hooks['publish_result']
    def committed_then_lost(*args,**kwargs):
        assert original(*args,**kwargs)
        raise OSError('Injected response loss after commit')
    monkeypatch.setitem(app.state.resume_hooks,'publish_result',committed_then_lost)
    first=client.post('/api/agent-workbench/import-verified-run',json=payload)
    assert first.status_code==200 and first.json()['warning']
    second=client.post('/api/agent-workbench/import-verified-run',json=payload)
    assert second.json()==first.json()
    detail=client.get('/api/candidates/'+payload['candidateId']).json()
    assert detail['candidate']['stage']=='ready' and detail['assessment']['runId']==first.json()['runId']
    assert client.get('/api/runs/'+first.json()['runId']).json()['status']=='succeeded' and provider.roles==[]

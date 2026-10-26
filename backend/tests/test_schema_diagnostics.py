"""Actual SDK/mock HTTP errors; never live provider traffic."""
from copy import deepcopy
import json
import pytest
from pydantic import ValidationError
from hr_backend.provider import structured_output_error
from hr_backend.schemas import MatchResult
from hr_backend.workflow import Workflow
from test_deepseek import setup
from test_content_planning import case
from test_structured_evidence import case as structured_case
from hr_backend.structured_evidence import project_match


def test_nested_sdk_error_reaches_saved_diagnostic_and_bounded_repair(setup,structured_case):
    provider,requests,outputs=setup
    doc,standards,hat,profile,valid=structured_case
    bad=deepcopy(valid)
    del bad['verificationTargets'][0]['focusId']
    bad['verificationTargets'][0]['PRIVATE_KEY_SENTINEL']='PRIVATE_VALUE_SENTINEL'
    *_,targets,errors=project_match(valid,standards,profile)
    assert not errors
    questions=[{'id':str(i),'question':'你'+t['singleGoal'],'purpose':t['singleGoal'],'pointIds':[t['pointId']],
                'criterionIds':[t['criterionId']],'evidence':[]} for i,t in enumerate(targets)]
    outputs.extend([bad,valid,{'approved':True,'findings':[]},{'questions':questions}]);events=[]
    result=Workflow(provider,max_repairs=1).analyze(doc,standards,hat,'r','c',profile=profile,trace_callback=events.append)
    assert result['reviewed'] and len(requests)==4
    artifact=next(e['_diagnostic'] for e in events if e.get('_diagnostic',{}).get('kind')=='schema_validation')
    assert artifact['schema']=='StructuredMatchResult'
    assert {'loc':['verificationTargets',0,'focusId'],'type':'missing'} in artifact['issues']
    assert {'loc':['verificationTargets',0,'<field>'],'type':'extra_forbidden'} in artifact['issues']
    repair=json.loads(requests[1]['input'][1]['content'])['repairIssues']
    assert 'verificationTargets.0.focusId: missing' in repair[0]
    assert 'PRIVATE_' not in json.dumps(events)+json.dumps(repair)
    assert provider.usage_summary()['attempts']==4  # no adapter retry, existing graph bound only


def test_safe_length_null_and_json_errors_without_values():
    *_,valid=case()
    bad=deepcopy(valid);bad['verificationTargets'][0]['singleGoal']='PRIVATE_VALUE_SENTINEL'*20
    bad['verificationTargets'][1]['evidence']=None
    with pytest.raises(ValidationError) as caught:MatchResult.model_validate(bad)
    error=structured_output_error(caught.value,MatchResult)
    assert {'loc':['verificationTargets',0,'singleGoal'],'type':'string_too_long'} in error.schema_issues
    assert {'loc':['verificationTargets',1,'evidence'],'type':'list_type'} in error.schema_issues
    assert 'PRIVATE_' not in str(error)+json.dumps(error.schema_issues)
    error=structured_output_error(json.JSONDecodeError('PRIVATE_MESSAGE','PRIVATE_BODY',1),MatchResult)
    assert error.schema_issues==[{'loc':[],'type':'json_invalid'}] and 'PRIVATE_' not in str(error)

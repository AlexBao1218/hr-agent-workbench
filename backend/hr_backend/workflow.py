"""Five-role LangGraph with deterministic gates and one bounded repair pass."""
from copy import deepcopy
from datetime import datetime, timezone
import unicodedata
import os
import json
import hashlib
from typing import TypedDict, Any
from uuid import uuid4
from pydantic import ValidationError
from langgraph.graph import StateGraph, START, END
from langsmith import tracing_context
from .provider import ProviderError, structured_output_error
from .evidence import source_ownership_context, validate_source_ownership, reading_order, rebind_repaired_verification_targets, validate_verification_targets, interview_context, profile_navigation, review_context, compose_point_explanations, point_evidence_roles, validate_point_grounding, exclusion_review_context, confirm_source_exclusions, identity_free_summary, validate_source_exclusions, canonical_requirements, capability_index, supplement_capability_sources, validate_capability_coverage, interview_plan, validate_interview_focus
from .schemas import (JDResult, CandidateProfile, MatchResult, ReviewResult,
                      StructuredMatchResult, StructuredReviewResult, CriterionRowsResult, CriterionRowsReview,
                      InterviewResult, ChatResult, StandardsChatResult)
from .structured_evidence import active_structure, project_match, review_projection, validate_review as validate_structured_review, repair_findings, row_payload, project_rows, row_claim_grounding, validate_row_review, row_repair_findings

class WorkflowError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message

def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).split())

def validate_refs(value, document, detailed=False):
    """Every supplied quote must be a contiguous normalized exact substring."""
    raw_blocks={b['id']:b['text'] for b in document.get('blocks',[])}
    blocks={key:normalized(text) for key,text in raw_blocks.items()}
    errors=[]
    def walk(v,path=''):
        if isinstance(v,dict):
            if 'blockId' in v:
                quote=normalized(v.get('quote',''))
                if not quote or v['blockId'] not in blocks or quote not in blocks[v['blockId']]:
                    message='Invalid source reference or quote'
                    if detailed:
                        message+=' at '+path+': '+json.dumps({'reference':v,'originalBlock':raw_blocks.get(v['blockId']),
                            'correction':'Copy a contiguous exact original substring, preserving internal word spacing. A sentence spanning blocks needs separate blockId/quote references; do not join text under one ID.'},ensure_ascii=False)
                    errors.append(message)
            for key,child in v.items(): walk(child,path+'.'+key)
        elif isinstance(v,list):
            for index,child in enumerate(v): walk(child,path+'.'+str(index))
    walk(value)
    return errors

def validate_standards(standards):
    criteria=standards.get('criteria',[])
    ids=[c['id'] for c in criteria]
    if not ids or len(set(ids)) != len(ids):
        raise WorkflowError('invalid_standards','Standards need nonempty unique criteria.')
    jd=normalized(standards.get('jdText',''))
    for c in criteria:
        quote=normalized(c.get('jdQuote',''))
        if not quote or quote not in jd:
            raise WorkflowError('invalid_jd_evidence','Every criterion needs an exact quotation from the original JD.')

def validate_analysis(analysis, document, standards):
    errors=validate_refs(analysis,document)
    ids={c['id'] for c in standards['criteria']}
    observations=analysis.get('observations',[])
    seen=[o['criterionId'] for o in observations]
    if set(seen)!=ids or len(seen)!=len(ids): errors.append('Observations must cover every criterion exactly once')
    for o in observations:
        if o['coverage'] != 'unknown' and not o['evidence']:
            errors.append('Positive, partial and contradiction observations need source evidence')
    points=analysis.get('matches',[])+analysis.get('concerns',[])
    point_ids=[p['id'] for p in points]
    if len(set(point_ids))!=len(point_ids): errors.append('Analysis point IDs must be unique')
    observation_map={o['criterionId']:o for o in observations}
    for p in points:
        if not p['criterionIds'] or not set(p['criterionIds']) <= ids:
            errors.append('Analysis point references unknown or empty criteria')
        if not p['jdRequirement'].strip(): errors.append('Analysis point must explain JD requirement')
        if p in analysis.get('matches',[]):
            if p['kind']!='match' or not p['evidence']:
                errors.append('Match points need positive evidence and match kind')
            for cid in p['criterionIds']:
                coverage=observation_map.get(cid,{}).get('coverage','missing')
                if coverage not in ('supported','partial'):
                    location=json.dumps({'pointId':p['id'],'title':p['title'],'criterionId':cid,'coverage':coverage},ensure_ascii=False)
                    errors.append('Match point contradicts criterion coverage: '+location+
                        '. Remove this unsupported criterion link or relink only to a directly supported confirmed criterion. '
                        'If no supported/partial link remains, remove this point from matches and retain the uncertainty in observations/concerns. '
                        'Change the observation only when exact source evidence genuinely supports that criterion; never mechanically change unknown to partial to pass validation.')
        elif p['kind']=='match': errors.append('Concern cannot be a match point')
        if p['kind']=='gap':
            point_blocks={ref['blockId'] for ref in p['evidence']}
            if not point_blocks:errors.append('Explicit gaps require evidence; absence is unknown')
            for cid in p['criterionIds']:
                observation=observation_map.get(cid,{})
                if observation.get('coverage')!='contradiction' or not observation.get('evidence'):
                    errors.append('Gap requires explicit source-backed contradiction for every linked criterion; partial/unknown must use unknown/verify with a 待确认 title')
                elif not point_blocks.intersection(ref['blockId'] for ref in observation['evidence']):
                    errors.append('Gap must cite the conflicting source passage from its linked contradiction observation')
    return list(dict.fromkeys(errors))

def validate_presentation(analysis):
    """Live display limits preserve complete observations; violations go through repair."""
    errors=[]
    if len(analysis.get('matches',[]))>6: errors.append('Visible matches must contain at most 6 focused capabilities')
    if not analysis.get('_readingVersion') and len(analysis.get('concerns',[]))>4: errors.append('Select about 3 material concerns, at most 4')
    for point in analysis.get('matches',[])+analysis.get('concerns',[]):
        body_limit=(6000 if point in analysis.get('concerns',[]) else 600) if analysis.get('_readingVersion') else 360
        for field,limit in [('title',32),('explanation',body_limit)]:
            if len(point[field].strip())>limit:
                errors.append(f'Visible point {field} exceeds {limit} characters; summarize without losing source scope')
    return errors

def validate_live_titles(criteria):
    import re
    if any(len(c['title'].strip())>32 or not re.search(r'[\u4e00-\u9fff]',c['title']) for c in criteria):
        raise WorkflowError('invalid_display_title','Use short Chinese criterion titles (up to 32 characters); preserve full JD quotations separately.')

class State(TypedDict, total=False):
    payload: dict
    profile: dict
    analysis: dict
    questions: list
    errors: list
    attempt: int
    approved: bool
    sourceExclusions: list
    pointGrounding: list
    verificationTargets: list
    structuredEvidence: dict
    semanticErrors: list
    matchInputHash: str
    matchInputAttempt: int

class Workflow:
    def __init__(self, provider, max_repairs=None):
        self.provider=provider
        self.structured_evidence=provider.mode=='live' and not getattr(provider,'legacy_evidence_fixture',False)
        self.inline_evidence=self.structured_evidence and getattr(provider,'evidence_protocol',3)==3
        self.settings_resolver=None
        if max_repairs is None:
            try:max_repairs=int(os.getenv('HR_MAX_REPAIRS',str(getattr(provider,'default_max_repairs',1))))
            except ValueError:raise WorkflowError('invalid_config','HR_MAX_REPAIRS must be 0, 1 or 2.') from None
        if max_repairs not in (0,1,2):raise WorkflowError('invalid_config','HR_MAX_REPAIRS must be 0, 1 or 2.')
        self.max_repairs=max_repairs

    def run_settings(self,run_id):
        return self.settings_resolver(run_id) if self.settings_resolver and run_id else None

    def _event(self,callback,context,role,status,attempt=1,summary=''):
        if callback:
            now=datetime.now(timezone.utc).isoformat()
            callback({'id':str(uuid4()),'role':role,'status':status,'attempt':attempt,
                      'summary':summary,'candidateId':context.get('candidateId'),
                      'standardsVersion':context.get('standardsVersion'),
                      'hatVersion':context.get('hatVersion'), 'runId':context.get('runId'),
                      'startedAt':now if status=='running' else None,
                      'completedAt':now if status in ('succeeded','failed','skipped') else None})

    def _diagnostic(self,callback,context,role,attempt,details):
        # Structured artifacts only: never hidden reasoning or provider HTTP bodies.
        if callback:
            callback({'role':role,'attempt':attempt,'candidateId':context.get('candidateId'),
                      'standardsVersion':context.get('standardsVersion'),'hatVersion':context.get('hatVersion'),
                      'runId':context.get('runId'),'_diagnostic':deepcopy(details)})

    def _call(self,role,payload,schema,callback=None,attempt=1,defer_success=False):
        context=payload.get('context',{})
        snapshot=self.run_settings(context.get('runId'))
        request=deepcopy(payload)
        request_settings={}
        if snapshot and role in snapshot['roles']:
            request_settings=deepcopy(snapshot['roles'][role])
            request_settings['model']=request_settings.get('model') or snapshot['runtime']['model']
            request_settings['systemPrompt']=snapshot['effectivePrompts'][role]
            request['_agentRequest']=request_settings
        from .prompts import role_instructions
        system_prompt=request_settings.get('systemPrompt') or role_instructions(role)
        self._diagnostic(callback,context,role,attempt,{'kind':'role_input','input':deepcopy(payload),
            'schema':schema.__name__,'systemPrompt':system_prompt,
            'settingsVersion':snapshot['version']['id'] if snapshot else None,
            'promptHash':__import__('hashlib').sha256(system_prompt.encode()).hexdigest(),
            'requestSettings':{k:v for k,v in request_settings.items() if k!='systemPrompt'}})
        self._event(callback,context,role,'running',attempt,'Structured role execution')
        try:
            result=schema.model_validate(self.provider.generate(role,request,schema)).model_dump()
        except ProviderError as exc:
            if exc.response_diagnostic is not None:
                self._diagnostic(callback,context,role,attempt,{'kind':'provider_response',**exc.response_diagnostic})
            if exc.schema_issues is not None:
                self._diagnostic(callback,context,role,attempt,{'kind':'schema_validation','schema':schema.__name__,'issues':exc.schema_issues})
            self._event(callback,context,role,'failed',attempt,'Provider call failed')
            raise
        except ValidationError as exc:
            self._event(callback,context,role,'failed',attempt,'Invalid structured output')
            safe=structured_output_error(exc,schema)
            self._diagnostic(callback,context,role,attempt,{'kind':'schema_validation','schema':schema.__name__,'issues':safe.schema_issues})
            error=WorkflowError('invalid_model_output',safe.message);error.schema_issues=safe.schema_issues
            raise error from None
        except (ValueError,TypeError,KeyError):
            self._event(callback,context,role,'failed',attempt,'Invalid structured output')
            raise WorkflowError('invalid_model_output','Model output failed the required structure; retry the analysis.') from None
        self._diagnostic(callback,context,role,attempt,{'kind':'role_output','schema':schema.__name__,'output':deepcopy(result)})
        if role=='review':
            result.setdefault('issues',[])
            for finding in result.get('findings',[]):
                if finding['severity']=='blocker' and finding['message'] not in result['issues']:
                    result['issues'].append(finding['message'])
            if result['issues']:result['approved']=False
        if not defer_success:self._event(callback,context,role,'succeeded',attempt,'Structured output received')
        return result

    def parse_jd(self,jd_text,context,trace_callback=None):
        result=self._call('jd',{'jdText':jd_text,'context':context},JDResult,trace_callback,defer_success=True)
        try:
            validate_standards({'jdText':jd_text,**result})
            if self.provider.mode=='live': validate_live_titles(result['criteria'])
        except WorkflowError:
            self._event(trace_callback,context,'jd','failed',summary='JD evidence/display validation failed')
            raise
        self._event(trace_callback,context,'jd','succeeded',summary='JD evidence and structure validated')
        return result['criteria']

    def analyze(self,document,standards,hat,run_id,candidate_id,feedback=None,profile=None,trace_callback=None):
        if document.get('status')!='ready' or not document.get('blocks'):
            raise WorkflowError('document_unreadable','Document extraction is incomplete. Repair or replace the document before analysis.')
        if not standards['version'].get('confirmedAt') or not hat['version'].get('confirmedAt'):
            raise WorkflowError('unconfirmed_snapshot','Confirm standards and HAT before candidate analysis.')
        context={'runId':run_id,'candidateId':candidate_id,'standardsVersion':standards['version']['id'],'hatVersion':hat['version']['id']}
        snapshot=self.run_settings(run_id)
        if self.inline_evidence and snapshot:
            from .prompts import ROW_TEMPLATE_VERSION
            if any(not snapshot['roles'][r]['systemPrompt'].startswith(ROW_TEMPLATE_VERSION) for r in ('match','review')):
                raise WorkflowError('template_migration_required','Criterion-row protocol requires explicit reviewed match/review template migration. No model call made.')
        max_repairs=snapshot['flow']['matchRepairs'] if snapshot else self.max_repairs
        # No filenames, manifest metadata, gold labels, credentials or filesystem paths enter model payloads.
        payload={'document':{'id':document['id'],'blocks':deepcopy(document['blocks'])},
                 'confirmedRequirementScope':[{'criterionId':c['id'],'requirement':c['description'],'jdQuote':c['jdQuote'],'additionalThresholdsAllowed':False} for c in standards['criteria']],
                 'standards':deepcopy(standards),'hat':deepcopy(hat),'context':context,'feedback':deepcopy(feedback or [])}

        def jd_node(state):
            self._event(trace_callback,context,'jd','running',summary='Validate confirmed JD snapshot')
            try:validate_standards(standards)
            except WorkflowError:
                self._event(trace_callback,context,'jd','failed',summary='Confirmed JD snapshot validation failed')
                raise
            self._event(trace_callback,context,'jd','succeeded',summary='Confirmed criteria and JD quotations validated; no reinterpretation')
            return {}

        def cv_node(state):
            result=deepcopy(profile) if profile is not None else self._call('cv',{'document':payload['document'],'hat':payload['hat'],'context':context},CandidateProfile,trace_callback,defer_success=True)
            result=CandidateProfile.model_validate(result).model_dump()
            # Group is reserved for HR configuration, even when the CV or cached model supplies it.
            for experience in result['experiences']: experience['companyGroup']=None
            errors=validate_refs(result,document)
            if self.provider.mode=='live':
                for language in result['languages']:
                    if not any(normalized(language) in normalized(b['text']) for b in document['blocks']):
                        errors.append('Language entries must preserve an exact original proficiency phrase; no inference from JD, name or teaching language')
            for item in result['education']+result['experiences']:
                if not item['evidence']: errors.append('Profile history requires evidence')
            if errors:
                self._event(trace_callback,context,'cv','failed',summary='Profile evidence validation failed')
                raise WorkflowError('invalid_profile_evidence','CV extraction contains unsupported history or invalid citations.')
            self._diagnostic(trace_callback,context,'cv',1,{'kind':'validated_profile','profile':result,'documentId':document['id']})
            if profile is not None: self._event(trace_callback,context,'cv','skipped',summary='Reused validated source profile')
            else:self._event(trace_callback,context,'cv','succeeded',summary='Profile structure and source evidence validated')
            return {'profile':result}

        def match_node(state):
            if self.inline_evidence:
                return self._match_rows(document,standards,hat,context,feedback or [],state,trace_callback)
            attempt=state.get('attempt',0)+1
            index=capability_index(document,state['profile'])
            repair_errors=list(dict.fromkeys(state.get('semanticErrors',[])+state.get('errors',[])))
            current={**payload,'profile':profile_navigation(state['profile']),'capabilityEvidenceIndex':index,'previousPointGrounding':state.get('pointGrounding',[]),'previousVerificationTargets':state.get('verificationTargets',[]),'sourceExclusions':state.get('sourceExclusions',[]),'repairIssues':repair_errors,'previousAnalysis':state.get('analysis'),
                     'sourceOwnershipContext':source_ownership_context(document,state['profile']),
                     'repairScope':{'mode':'local_correction' if state.get('analysis') or (self.structured_evidence and state.get('structuredEvidence')) else 'initial_analysis',
                                    'preserveValidFacts':True,'preserveUnrelatedFields':True,
                                    'instruction':'Correct only supplied defects and their directly dependent fields. Reuse valid analysis, exact quotes and target goals; do not regenerate unrelated content. If a match loses a criterion, update dependent verificationTargets.pointId to an existing concern for that same criterion. Keep every target criterionId inside its selected point criterionIds; never restore unsupported match criteria merely to retain old links. For a point-local claim evidence error, use the supplied pointId/claimIndex/blockId/quote to verify the existing claim reference against the original document, then synchronize that same match point evidence with the valid claim reference. Correct or remove unsupported claims/references instead; never invent or rewrite quotes.'}}
            if self.structured_evidence:
                for field in ('previousPointGrounding','previousVerificationTargets','previousAnalysis'):current.pop(field)
                current['previousStructuredEvidence']=state.get('structuredEvidence')
                current['repairScope']['instruction']='Correct the supplied defects in the private fact/component/focus graph and dependent references. Preserve unrelated facts, exact sources and IDs. Keep every component in its observation; derive point wording and target links through the same objects. Do not add a separate assessment/narrative or invent evidence.'
            input_hash=hashlib.sha256(json.dumps(current,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() if self.structured_evidence else ''
            if self.structured_evidence and state.get('matchInputHash')==input_hash:
                self._event(trace_callback,context,'match','failed',attempt,'Stopped unchanged repair request; no provider call')
                self._diagnostic(trace_callback,context,'match',attempt,{'kind':'repair_stalled','inputHash':input_hash,
                    'previousAttempt':state.get('matchInputAttempt'),'repairIssues':repair_errors,'providerCalled':False})
                raise WorkflowError('repair_stalled','Matching repair input did not change; stopped without repeating the same model request. Inspect the last matching diagnostics.')
            grounding=[];targets=[];structured={};inventory_counts={};structure_errors=[]
            try:
                result=self._call('match',current,StructuredMatchResult if self.structured_evidence else MatchResult,trace_callback,attempt,defer_success=True)
                if self.structured_evidence:
                    structured=active_structure(result)
                    inventory_counts={'factsRaw':len(result['facts']),'factsActive':len(structured['facts']),
                        'focusesRaw':len(result['focuses']),'focusesActive':len(structured['focuses'])}
                    result,grounding,targets,structure_errors=project_match(structured,standards,state['profile'])
                else:
                    structure_errors=[]
                    grounding=result.pop('pointGrounding')
                    targets=result.pop('verificationTargets')
                errors=list(structure_errors)
                if structured:
                    errors+=validate_refs(structured,document)
                    errors+=validate_source_ownership({'observations':[],'concerns':[]},
                        [{'pointId':f['id'],'claims':[f]} for f in structured['facts']],document,state['profile'])
                if not structure_errors:
                    result=compose_point_explanations(result,grounding)
                    result=canonical_requirements(result,standards)
                    result,additions=supplement_capability_sources(result,index,exclusions=state.get('sourceExclusions',[]),grounding=grounding)
                    if additions:self._diagnostic(trace_callback,context,'match',attempt,{'kind':'source_coverage','additions':additions})
                    result=reading_order(result,standards)
                    targets,rebindings=rebind_repaired_verification_targets(targets,result,state.get('analysis'),standards)
                    if rebindings:self._diagnostic(trace_callback,context,'match',attempt,{'kind':'verification_target_rebinding','changes':rebindings})
                    errors+=validate_analysis(result,document,standards)
                    errors+=validate_verification_targets(targets,result,standards,state['profile'])+validate_refs(targets,document)
                    errors+=validate_point_grounding(result,grounding,require_facts=self.provider.mode=='live')+validate_refs(grounding,document)
                    errors+=validate_source_ownership(result,grounding,document,state['profile'])
                    errors+=validate_capability_coverage(result,index,exclusions=state.get('sourceExclusions',[]),grounding=grounding)
                    result['summary']=identity_free_summary(result,standards)
                    if self.provider.mode=='live': errors+=validate_presentation(result)
            except ProviderError as exc:
                # Only completed but malformed structured output is repairable.
                # Truncation/refusal/transport/input-budget failures terminate this run.
                if exc.code!='model_invalid_output': raise
                result={}
                errors=[exc.message]
            except WorkflowError as exc:
                result={}
                errors=[exc.message]
            self._event(trace_callback,context,'match','failed' if errors else 'succeeded',attempt,'Deterministic match validation failed' if errors else 'Match structure and evidence validated')
            self._diagnostic(trace_callback,context,'match',attempt,{'kind':'match_validation','analysis':result,'pointGrounding':grounding,'verificationTargets':targets,'validationIssues':errors,
                **({'evidenceProtocol':2,'structuredEvidence':structured,'inventoryCounts':inventory_counts,
                    'projectionBlocked':bool(structure_errors) if structured else True} if self.structured_evidence else {})})
            return {'analysis':result,'pointGrounding':grounding,'verificationTargets':targets,'errors':errors,'attempt':attempt,'approved':False,
                    'structuredEvidence':structured or state.get('structuredEvidence',{}),'matchInputHash':input_hash,'matchInputAttempt':attempt}

        def review_node(state):
            if self.inline_evidence:
                return self._review_rows(document,standards,hat,context,feedback or [],state,max_repairs,trace_callback)
            if state['errors']:
                self._event(trace_callback,context,'review','skipped',state['attempt'],'Deterministic validation blocked semantic review; no provider call')
                self._diagnostic(trace_callback,context,'review',state['attempt'],{'kind':'review_decision','analysis':state['analysis'],'verificationTargets':state['verificationTargets'],
                    'pointGrounding':state['pointGrounding'],'validationIssues':state['errors'],'reviewIssues':[],
                    'reviewApproved':False,'publishable':False,'providerCalled':False})
                if state['attempt']>max_repairs:
                    raise WorkflowError('review_failed','Deterministic validation did not pass after bounded repair; semantic review was not called.')
                return {'approved':False,'errors':state['errors']}
            index=capability_index(document,state['profile'])
            pending=exclusion_review_context(state['analysis'],state.get('sourceExclusions',[]),index,state['pointGrounding'])
            review_data={'analysis':state['analysis'],'verificationTargets':state['verificationTargets'],
                         **review_context(state['analysis'],state['pointGrounding'],index)}
            if self.structured_evidence:
                projection,_=review_projection(state['structuredEvidence'],state['analysis'])
                review_data={'evidenceReview':projection,'capabilityEvidenceIndex':review_data['capabilityEvidenceIndex']}
            review=self._call('review',{**payload,'profile':profile_navigation(state['profile']),**review_data,
                'pendingSourceExclusions':pending,'sourceOwnershipContext':source_ownership_context(document,state['profile']),
                'capabilityIndexPolicy':'Retrieval candidates only, not scored claims. An irrelevant index hit alone is not a publication blocker; reject only when it causes unsupported analysis, grounding or coverage. Do not ask match to edit a code-generated index.'},StructuredReviewResult if self.structured_evidence else ReviewResult,trace_callback,state['attempt'],defer_success=True)
            review_contract_errors=[]
            if self.structured_evidence:
                review_contract_errors=validate_structured_review(review,state['structuredEvidence'],state['analysis'],standards)+validate_refs(review,document)
                review['issues']=repair_findings(review)
            accepted,exclusion_errors=validate_source_exclusions(review['sourceExclusions'],state['analysis'],index,state['pointGrounding'])
            if review['sourceExclusions'] and (review['approved'] or not review['issues']):
                exclusion_errors.append('New source exclusions require rejected review with an actionable blocking issue')
                accepted=[]
            retained,confirmation_errors=confirm_source_exclusions(pending,review['sourceExclusionDecisions'])
            exclusion_errors+=confirmation_errors
            exclusions=retained+accepted
            errors=state['errors']+review['issues']+exclusion_errors+review_contract_errors
            approved=review['approved'] and not errors
            self._event(trace_callback,context,'review','succeeded' if approved else 'failed',state['attempt'],'Evidence review passed' if approved else 'Evidence review rejected publication')
            self._diagnostic(trace_callback,context,'review',state['attempt'],{'kind':'review_decision','analysis':state['analysis'],'verificationTargets':state['verificationTargets'],'pointEvidenceRoles':point_evidence_roles(state['analysis'],state['pointGrounding']),'pointGrounding':state['pointGrounding'],'validationIssues':state['errors'],'reviewIssues':review['issues'],'findings':review.get('findings',[]),'reviewApproved':review['approved'],'sourceExclusions':exclusions,'pendingSourceExclusions':pending,'sourceExclusionDecisions':review['sourceExclusionDecisions'],'exclusionValidationIssues':exclusion_errors,'reviewContractIssues':review_contract_errors,'publishable':approved})
            if review_contract_errors:
                raise WorkflowError('review_contract_invalid','Review findings are not grounded in current subjects/sources: '+'; '.join(review_contract_errors))
            if not approved and state['attempt']>max_repairs:
                self._event(trace_callback,context,'review','failed',state['attempt'],'Bounded repair exhausted; assessment not publishable')
                raise WorkflowError('review_failed','Evidence review did not pass after bounded repair. Inspect sources or correct the input and retry.')
            remaining=errors or ([] if approved else ['Independent review rejected output'])
            return {'approved':approved,'sourceExclusions':exclusions,'errors':remaining,'semanticErrors':[] if approved else remaining}

        def interview_node(state):
            return self.interview_only(document,standards,hat,state['profile'],state['analysis'],run_id,candidate_id,trace_callback,feedback=feedback,targets=state['verificationTargets'])

        graph=StateGraph(State)
        for name,node in [('jd',jd_node),('cv',cv_node),('match',match_node),('review',review_node),('interview',interview_node)]: graph.add_node(name,node)
        graph.add_edge(START,'jd'); graph.add_edge('jd','cv'); graph.add_edge('cv','match'); graph.add_edge('match','review')
        graph.add_conditional_edges('review',lambda s:'interview' if s['approved'] else 'match')
        graph.add_edge('interview',END)
        # Disable opt-in environment tracing: candidate evidence must not leave via LangSmith.
        with tracing_context(enabled=False):
            result=graph.compile().invoke({'payload':payload,'attempt':0,'errors':[]},config={'recursion_limit':20,'callbacks':[]})
        return {'profile':result['profile'],**result['analysis'],'questions':result['questions'],'reviewed':True}

    def _match_rows(self,document,standards,hat,context,feedback,state,callback):
        attempt=state.get('attempt',0)+1
        current=row_payload(document,standards,hat,context,feedback,state['profile'])
        issues=list(dict.fromkeys(state.get('semanticErrors',[])+state.get('errors',[])))
        if attempt>1 or (state.get('structuredEvidence') and issues):
            current['previousRows']=deepcopy(state.get('structuredEvidence',{}))
            current['repairIssues']=issues
            if state.get('repairSource'):current['repairSource']=deepcopy(state['repairSource'])
            current['repairInstruction']='Patch the supplied previousRows at these exact current defects; preserve valid facts/quotes and unrelated rows. Before returning, verify every listed defect is actually eliminated. Return the full corrected object, not an unchanged copy.'
        input_hash=hashlib.sha256(json.dumps(current,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if state.get('matchInputHash')==input_hash:
            self._diagnostic(callback,context,'match',attempt,{'kind':'repair_stalled','inputHash':input_hash,'providerCalled':False})
            raise WorkflowError('repair_stalled','Matching repair input did not change; stopped without repeating the same model request.')
        data={};analysis={};grounding=[];targets=[];selected=[];errors=[];reference_repairs=[]
        try:
            data=self._call('match',current,CriterionRowsResult,callback,attempt,defer_success=True)
            from .source_references import resolve_row_references
            data,reference_repairs=resolve_row_references(data,document,standards)
            analysis,grounding,targets,selected,errors=project_rows(data,standards,state['profile'])
            from .outcome_reading import READING_VERSION
            settings=self.run_settings(context['runId'])
            if settings and READING_VERSION in settings['roles']['match']['systemPrompt'] and self.provider.mode=='live' and not data.get('reading'):
                errors.append('This active task requires candidate reading conclusions, concise summary and interviewChoices; reading cannot be null.')
            errors+=validate_refs(data,document,detailed=True)
            errors+=validate_source_ownership({'observations':[],'concerns':[]},row_claim_grounding(data),document,state['profile'])
            if not errors:
                analysis=compose_point_explanations(analysis,grounding)
                errors+=validate_analysis(analysis,document,standards)
                errors+=validate_point_grounding(analysis,grounding,require_facts=True)
                errors+=validate_verification_targets(targets,analysis,standards,state['profile'])+validate_refs(targets,document)
                # Every row fact was checked above. Derived multi-employer observations must not be reinterpreted
                # as one employer merely because only one of their printed abbreviations was recognized.
                errors+=validate_source_ownership({'observations':[],'concerns':analysis['concerns']},grounding,document,state['profile'])
                errors+=validate_presentation(analysis)
                if not data.get('reading'):analysis['summary']=identity_free_summary(analysis,standards)
        except ProviderError as exc:
            if exc.code!='model_invalid_output':raise
            errors=[exc.message]
        self._event(callback,context,'match','failed' if errors else 'succeeded',attempt,'Criterion evidence rows validated' if not errors else 'Criterion row validation failed')
        self._diagnostic(callback,context,'match',attempt,{'kind':'match_validation','evidenceProtocol':3,
            'structuredEvidence':data,'analysis':analysis,'pointGrounding':grounding,'verificationTargets':targets,
            'selectedUnknowns':selected,'referenceRepairs':reference_repairs,'validationIssues':list(dict.fromkeys(errors))})
        return {'analysis':analysis,'pointGrounding':grounding,'verificationTargets':targets,'structuredEvidence':data or state.get('structuredEvidence',{}),
                'errors':list(dict.fromkeys(errors)),'attempt':attempt,'approved':False,'matchInputHash':input_hash,'matchInputAttempt':attempt}

    def _review_rows(self,document,standards,hat,context,feedback,state,max_repairs,callback):
        attempt=state['attempt'];errors=state['errors']
        if errors:
            self._event(callback,context,'review','skipped',attempt,'Deterministic row validation blocked review; no provider call')
            self._diagnostic(callback,context,'review',attempt,{'kind':'review_decision','evidenceProtocol':3,
                'analysis':state['analysis'],'pointGrounding':state['pointGrounding'],'verificationTargets':state['verificationTargets'],
                'validationIssues':errors,'reviewIssues':[],'reviewApproved':False,'publishable':False,'providerCalled':False})
            if attempt>max_repairs:raise WorkflowError('review_failed','Deterministic validation did not pass after bounded repair; semantic review was not called.')
            return {'approved':False,'errors':errors}
        current=row_payload(document,standards,hat,context,feedback,state['profile'])
        current['criterionEvidence']=deepcopy(state['structuredEvidence'])
        _,_,_,selected,_=project_rows(state['structuredEvidence'],standards,state['profile'])
        current['selectedUnknowns']=selected
        review=self._call('review',current,CriterionRowsReview,callback,attempt,defer_success=True)
        contract_errors=validate_row_review(review,state['structuredEvidence'],standards,require_checks=True)+validate_refs(review,document)
        issues=row_repair_findings(review);approved=review['approved'] and not issues and not contract_errors
        self._event(callback,context,'review','succeeded' if approved else 'failed',attempt,'Criterion evidence review passed' if approved else 'Criterion evidence review rejected publication')
        self._diagnostic(callback,context,'review',attempt,{'kind':'review_decision','evidenceProtocol':3,
            'analysis':state['analysis'],'pointGrounding':state['pointGrounding'],'verificationTargets':state['verificationTargets'],
            'validationIssues':[],'reviewIssues':issues,'findings':review['findings'],'reviewContractIssues':contract_errors,
            'reviewApproved':review['approved'],'publishable':approved})
        if contract_errors:raise WorkflowError('review_contract_invalid','Review findings are not grounded in current rows: '+'; '.join(contract_errors))
        if not approved and attempt>max_repairs:raise WorkflowError('review_failed','Evidence review did not pass after bounded repair. Inspect sources or correct the input and retry.')
        return {'approved':approved,'errors':issues,'semanticErrors':[] if approved else issues}

    def interview_only(self,document,standards,hat,profile,analysis,run_id,candidate_id,trace_callback=None,feedback=None,targets=None):
        """Shared final role; callers must establish approved analysis provenance."""
        context={'runId':run_id,'candidateId':candidate_id,'standardsVersion':standards['version']['id'],'hatVersion':hat['version']['id']}
        payload={'standards':deepcopy(standards),'hat':deepcopy(hat),'context':context,'feedback':deepcopy(feedback or []),
                 'confirmedRequirementScope':[{'criterionId':c['id'],'requirement':c['description'],'jdQuote':c['jdQuote'],'additionalThresholdsAllowed':False} for c in standards['criteria']]}
        plans=interview_plan(analysis,standards,profile,targets)
        if not plans:raise WorkflowError('missing_verification_plan','No reviewed verification plan; run normal analysis instead of manufacturing legacy targets.')
        snapshot=self.run_settings(run_id)
        repairs=snapshot['flow']['interviewRepairs'] if snapshot else 0
        previous=None;errors=[]
        base={**payload,**interview_context(document,profile,analysis,plans),'questionPlan':plans}
        for attempt in range(1,repairs+2):
            current=deepcopy(base)
            if previous is not None or errors:
                current.update(previousQuestions=previous,repairIssues=errors,
                               repairScope='Correct only interview wording/references for the same three reviewed goals. Do not rematch or add new requirements.')
            try:
                result=self._call('interview',current,InterviewResult,trace_callback,attempt=attempt,defer_success=True)
            except (ProviderError,WorkflowError) as exc:
                if exc.code not in ('model_invalid_output','invalid_model_output') or not getattr(exc,'schema_issues',None):raise
                errors=[exc.message];previous=None
                if attempt<=repairs:continue
                raise
            errors=validate_refs(result,document)
            ids={c['id'] for c in standards['criteria']}
            points={p['id'] for p in analysis['matches']+analysis['concerns']}
            if self.provider.mode=='live':
                errors+=validate_interview_focus(result['questions'],plans,profile)
                if len({q['question'].strip() for q in result['questions']})!=3 or any(len(q['question'])>240 or len(q['purpose'])>180 for q in result['questions']):
                    errors.append('Provide three distinct concise candidate-specific questions')
            qids=[q['id'] for q in result['questions']]
            if len(set(qids))!=3: errors.append('Question IDs must be unique')
            for q in result['questions']:
                if not q['criterionIds'] or not set(q['criterionIds'])<=ids or not set(q['pointIds'])<=points:
                    errors.append('Question references nonexistent criteria or points')
                if points and not q['pointIds']: errors.append('Questions must connect to analysis points')
            self._diagnostic(trace_callback,context,'interview',attempt,{'kind':'interview_validation','questionPlan':plans,'questions':result['questions'],'validationIssues':errors})
            if not errors:
                self._event(trace_callback,context,'interview','succeeded',attempt,summary='All interview source and focus checks passed')
                return result
            self._event(trace_callback,context,'interview','failed',attempt,summary='Interview validation failed; only interview may be repaired within configured limit')
            previous=result['questions']
        raise WorkflowError('invalid_interview_evidence','Interview questions failed validation: '+'; '.join(errors))

    def chat(self,message,document,standards,hat,context):
        result=self._call('chat',{'message':message,'document':{'id':document['id'],'blocks':document['blocks']},
            'standards':standards,'hat':hat,'context':context},ChatResult)
        if validate_refs(result,document): raise WorkflowError('invalid_chat_evidence','Chat answer references invalid source evidence.')
        return result

    def standards_chat(self,message,standards_or_draft,hat):
        context={'runId':str(uuid4()),'hatVersion':hat['version']['id'],
                 'standardsVersion':(standards_or_draft or {}).get('version',{}).get('id')}
        result=self._call('standards_chat',{'message':message,'standards':standards_or_draft,'hat':hat,'context':context},StandardsChatResult)
        if result['draft'] is not None:
            if not standards_or_draft or result['draft']['jdText'] != standards_or_draft['jdText']:
                raise WorkflowError('invalid_jd_evidence','Chat proposals must preserve the original JD source.')
            validate_standards(result['draft'])
            if self.provider.mode=='live': validate_live_titles(result['draft']['criteria'])
        return result

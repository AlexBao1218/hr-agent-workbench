"""Read-only views of accepted facts; never generate, score or persist assessment data."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
from .evidence import compose_point_explanations, validate_point_grounding
from .workflow import validate_refs

ANALYSIS_FIELDS=('observations','matches','concerns','summary','keyUnknown')


def meaning(standards):
    # Only priority/order/version may differ; all other confirmed meaning remains exact.
    return {'jdText':standards['jdText'], 'criteria':sorted(
        [{k:v for k,v in c.items() if k!='priority'} for c in standards['criteria']],key=lambda c:c['id'])}


def same_analysis(left,right):
    return all(k in left and k in right and (sorted(left[k],key=lambda p:p['id'])==sorted(right[k],key=lambda p:p['id']) if k in ('matches','concerns') else left[k]==right[k]) for k in ANALYSIS_FIELDS)


def card_display(matches,entries,unknown):
    labels=[];supports=[]
    names={'rag':'RAG实践','python':'Python应用','data_analysis':'数据分析'}
    for match in matches:
        entry=entries[match['pointId']]
        label='工作流项目实践' if entry.get('focus')=='workflow' else (
            names.get(next(iter(entry.get('capabilities',[])),None)) if entry.get('focus')=='capability' else None)
        if label and label not in labels:
            labels.append(label);supports.append(deepcopy(match))
        if len(labels)==2:break
    if set(labels)=={'工作流项目实践','RAG实践'}:
        wording={'工作流项目实践':'工作流','RAG实践':'RAG'}
        summary='有'+'与'.join(wording[label] for label in labels)+'项目实践'
    elif labels:summary='有'+'和'.join(labels)
    else:
        supports=deepcopy(matches[:2])
        summary=supports[0]['shortFacts'][0].rstrip('。；;') if supports else '现有材料尚需进一步核实'
    if unknown:
        text=unknown['text'].rstrip('。')
        if text.endswith('待确认'):text=text[:-3]+'仍需确认'
        summary+='，但'+text
    return {'summary':summary+'。','supports':supports,'keyUnknown':unknown}


def project(assessment,standards,grounding,document):
    """Pure projection, failing closed on incomplete or inconsistent factual structure."""
    try:
        points=assessment['matches'];entries={g['pointId']:g for g in grounding}
        if len(entries)!=len(grounding) or len(entries)!=len(points) or set(entries)!={p['id'] for p in points}:return None
        if validate_refs(assessment,document) or validate_refs(grounding,document) or validate_point_grounding(assessment,grounding,require_facts=True):return None
        rendered=compose_point_explanations(assessment,grounding)
        if rendered['matches']!=points:return None
        criteria={c['id']:c for c in standards['criteria']}
        observations={o['criterionId']:o['coverage'] for o in assessment['observations']}
        if len(observations)!=len(assessment['observations']):return None
        def priority(point):
            return min((criteria[c]['group']!='must',criteria[c]['priority']) for c in point['criterionIds'])
        ordered=sorted(points,key=priority)  # stable ties retain original assessment order
        matches=[]
        for point in ordered:
            coverage=[observations[c] for c in point['criterionIds']]
            if not coverage or any(c not in ('supported','partial') for c in coverage):return None
            facts=entries[point['id']]['claims']
            strings=[f['claim'] for f in facts]
            if any(not isinstance(t,str) or not t.strip() for t in strings):return None
            selected=strings[:1]
            if len(strings)>1 and len(strings[0]+' '+strings[1])<=80:selected.append(strings[1])
            matches.append({'pointId':point['id'],'title':point['title'],'shortReason':' '.join(selected),'shortFacts':list(selected),
                            'coverage':'supported' if all(c=='supported' for c in coverage) else 'partial',
                            'additionalFactCount':len(strings)-len(selected)})
        concerns=sorted(assessment['concerns'],key=priority)
        unknown={'pointId':concerns[0]['id'],'text':concerns[0]['title']} if concerns else None
        card=card_display(matches,entries,unknown)
        from .outcome_reading import READING_VERSION
        if assessment.get('_readingVersion')==READING_VERSION:
            card['summary']=assessment['summary']
        return {'assessmentId':assessment['id'],'matches':matches,'card':card}
    except (KeyError,TypeError,ValueError,AttributeError):
        return None


class DisplayProjection:
    """No persistent cache or writes. Read only explicitly indexed, bounded diagnostics."""
    def __init__(self,root,diagnostic_reader=None):
        self.root=Path(root).resolve()
        self.diagnostic_reader=diagnostic_reader

    def _artifacts(self,run):
        refs=run.get('_diagnosticFiles',[])
        if not isinstance(refs,list) or not refs or len(refs)>64:return None
        directory=self.root/'diagnostics'
        if directory.is_symlink():return None
        result=[]
        for ref in refs:
            if not isinstance(ref,str):return None
            if self.diagnostic_reader:
                if not ref.startswith('diagnostics/'+run['id']+'-') or Path(ref).name != ref.split('/')[-1] or len(Path(ref).parts)!=2 or '..' in Path(ref).parts:return None
                raw=self.diagnostic_reader(ref)
                if len(raw)>4*1024*1024:return None
                artifact=json.loads(raw)
                if not isinstance(artifact,dict) or artifact.get('runId')!=run['id']:return None
                result.append(artifact)
                continue
            path=self.root/ref
            if (path.is_symlink() or path.parent!=directory or path.suffix!='.json'
                or not path.name.startswith(run['id']+'-') or path.resolve().parent!=directory):return None
            if path.stat().st_size>4*1024*1024:return None
            artifact=json.loads(path.read_text())
            if not isinstance(artifact,dict) or artifact.get('runId')!=run['id']:return None
            result.append(artifact)
        return result

    def _resume_sources(self,state,run,artifacts):
        if run.get('_resumeKind')!='interview_only':return True
        source=state['runs'][run['_sourceRunId']]
        binding=run['_resumeBinding']
        if hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()!=binding['sourceRunDigest']:return False
        originals=self._artifacts(source)
        if originals is None:return False
        indexed=dict(zip(source['_diagnosticFiles'],originals))
        reused=[a for a in artifacts if a.get('reused')]
        if {a['kind'] for a in reused}!={'validated_profile','match_validation','review_decision'}:return False
        metadata={'runId','role','attempt','candidateId','standardsVersion','hatVersion','createdAt','usageReference','providerCalled'}
        for artifact in reused:
            ref=artifact['sourceArtifact']
            if ref not in indexed or artifact['sourceRunId']!=source['id'] or artifact.get('providerCalled') is not False:return False
            actual=hashlib.sha256(self.diagnostic_reader(ref) if self.diagnostic_reader else (self.root/ref).read_bytes()).hexdigest()
            if actual!=artifact['sourceArtifactSHA256'] or actual!=binding['artifactHashes'][ref]:return False
            original=indexed[ref]
            if any(artifact.get(k)!=v for k,v in original.items() if k not in metadata):return False
            if any(artifact.get(k)!=original.get(k) for k in ('candidateId','standardsVersion','hatVersion','role','attempt')):return False
        return True

    def get(self,state,candidate):
        try:
            a=state['assessments'].get(candidate.get('assessmentId'))
            standards=state['standards'];hat=state['hat']
            if (not a or not a['reviewed'] or a['status']!='current' or a['candidateId']!=candidate['id']
                or a['standardsVersion']!=standards['version']['id'] or a['hatVersion']!=hat['version']['id']):return None
            doc=state['documents'][candidate['documentId']]
            source=candidate.get('profileSource',{})
            if (doc['status']!='ready' or source.get('documentId')!=doc['id']
                or not doc.get('hash') or source.get('documentHash')!=doc['hash']):return None
            for run in reversed(list(state['runs'].values())):
                if run.get('status')!='succeeded' or run.get('mode')!=a['mode'] or candidate['id'] not in run.get('_candidateIds',[]):continue
                inputs=run.get('_inputs',{})
                if (inputs.get('generations',{}).get(candidate['id'])!=candidate['generation']
                    or candidate['id'] not in inputs.get('candidateIds',[])):continue
                old=inputs.get('standards',{})
                if (meaning(old)!=meaning(standards) or inputs.get('hat')!=hat
                    or run['standardsVersion']!=old['version']['id'] or run['hatVersion']!=hat['version']['id']):continue
                originals=[state['assessments'].get(i,{}) for i in run.get('_outputAssessmentIds',[])]
                if not any(o.get('candidateId')==candidate['id'] and o.get('runId')==run['id'] and o.get('reviewed')
                           and same_analysis(o,a) and o.get('questions')==a['questions'] for o in originals):continue
                artifacts=self._artifacts(run)
                if artifacts is None or not self._resume_sources(state,run,artifacts):continue
                bound=[d for d in artifacts if d.get('candidateId')==candidate['id']
                       and d.get('standardsVersion')==run['standardsVersion'] and d.get('hatVersion')==run['hatVersion']]
                if not any(d.get('kind')=='validated_profile' and d.get('documentId')==doc['id'] for d in bound):continue
                if not any(d.get('kind')=='interview_validation' and d.get('validationIssues')==[] and d.get('questions')==a['questions'] for d in bound):continue
                for review in reversed(bound):
                    if (review.get('kind')=='review_decision' and review.get('publishable') is True
                        and review.get('reviewApproved') is True and review.get('validationIssues')==[]
                        and review.get('reviewIssues')==[] and not review.get('exclusionValidationIssues')
                        and same_analysis(review.get('analysis',{}),a)):
                        return project(a,standards,review['pointGrounding'],doc)
            return None
        except (OSError,UnicodeError,ValueError,TypeError,KeyError,AttributeError):
            return None

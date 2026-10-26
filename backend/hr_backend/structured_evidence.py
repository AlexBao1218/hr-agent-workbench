"""Private evidence protocol: one fact/component ledger, public wire projections.

No semantic keyword classification or candidate-specific correction lives here.
The model still selects/assesses source-grounded components; code enforces their
identities, references and projection boundaries. Legacy saved data is untouched.
"""
from copy import deepcopy
import hashlib
import json

from .evidence import normalize_evidence, reading_order


def _unique(items,key='id'):
    return all(item[key].strip() for item in items) and len({item[key] for item in items})==len(items)


def _refs(refs):
    result=[];seen=set()
    for ref in refs:
        key=(ref['blockId'],normalize_evidence(ref['quote']))
        if key not in seen:result.append(deepcopy(ref));seen.add(key)
    return result


def active_structure(data):
    """Project the referenced subgraph; raw model inventory remains in role_output.

    Components alone select canonical facts. Deprecated point factIds never
    create support; raw historical model output remains available for audit.
    """
    result=deepcopy(data)
    focus_ids={c['focusId'] for c in data['concerns']}|{t['focusId'] for t in data['verificationTargets']}
    fact_ids={i for c in data['components'] for i in c['factIds']}
    for point in result['matches']:point.pop('factIds',None)
    result['facts']=[f for f in result['facts'] if f['id'] in fact_ids]
    result['focuses']=[f for f in result['focuses'] if f['id'] in focus_ids]
    return result


def component_text(component):
    label=component['component']
    return {'supported':f'相关证据支持「{label}」。',
            'partial':f'相关证据对「{label}」提供部分支持。',
            'unknown':f'材料未提及「{label}」，待确认。',
            'contradiction':f'所引原文与「{label}」存在明确冲突，需核实。'}[component['coverage']]


def focus_goal(focus,component):
    text=component['component']
    return {'existence':f'是否有过「{text}」的实际经历？',
            'method':f'具体如何完成「{text}」？',
            'contribution':f'在「{text}」中，你亲自完成了什么？',
            'validation':f'如何验证「{text}」的结果？',
            'limitations':f'「{text}」有哪些实际限制？'}[focus['objective']]


def validate_structure(data,standards,profile):
    data=active_structure(data)
    errors=[]
    for key in ('facts','components','matches','focuses','concerns','verificationTargets'):
        if not _unique(data[key]):errors.append(f'{key} IDs must be unique')
    if errors:return errors
    facts={f['id']:f for f in data['facts']}
    components={c['id']:c for c in data['components']}
    criteria={c['id']:c for c in standards['criteria']}
    focuses={f['id']:f for f in data['focuses']}
    experiences={e['id']:e for e in profile['experiences']}
    fact_keys=[(normalize_evidence(f['claim']),tuple(sorted((r['blockId'],normalize_evidence(r['quote'])) for r in f['evidence']))) for f in facts.values()]
    if len(fact_keys)!=len(set(fact_keys)):
        errors.append('Reuse a canonical fact ID instead of duplicating the same claim and sources')
    if any(not normalize_evidence(f['claim']) for f in facts.values()):
        errors.append('Canonical facts must contain a nonempty claim')
    for c in components.values():
        criterion=criteria.get(c['criterionId'])
        if not criterion:
            errors.append('Unknown criterion for component '+c['id']);continue
        quote=normalize_evidence(c['requirementQuote'])
        if not normalize_evidence(c['component']) or not quote or not any(quote in normalize_evidence(criterion[k]) for k in ('description','jdQuote')):
            errors.append('Component must quote its confirmed requirement exactly: '+c['id'])
        if len(set(c['factIds']))!=len(c['factIds']) or not set(c['factIds'])<=facts.keys():
            errors.append('Component references missing/duplicate facts: '+c['id'])
        if c['coverage']!='unknown' and not c['factIds']:
            errors.append('Positive/partial/contradiction components require canonical facts: '+c['id'])
    seen=[o['criterionId'] for o in data['observations']]
    if set(seen)!=criteria.keys() or len(seen)!=len(set(seen)):
        errors.append('Observations must cover each confirmed criterion exactly once')
    used_components=set()
    for o in data['observations']:
        refs=o['componentIds'];used_components.update(refs)
        if len(refs)!=len(set(refs)) or not refs or any(i not in components or components[i]['criterionId']!=o['criterionId'] for i in refs):
            errors.append('Observation must reference its own unique components: '+o['criterionId'])
            continue
        states=[components[i]['coverage'] for i in refs]
        positive=any(s in ('supported','partial') for s in states)
        if o['coverage'] in ('supported','partial') and not positive:
            errors.append('Positive observation requires an evidenced component: '+o['criterionId'])
        if o['coverage']=='unknown' and positive:
            errors.append('Unknown observation cannot also assert a positive component: '+o['criterionId'])
        if (o['coverage']=='contradiction')!=('contradiction' in states):
            errors.append('Criterion contradiction must retain its explicit conflicting component: '+o['criterionId'])
    if used_components!=components.keys():errors.append('Every component must remain in its observation, including unselected unknowns')
    for point in data['matches']:
        # Same existing grounding rule, checked before projection so a broken
        # reference does not conceal an independent metadata error until M2.
        if (point['focus']=='capability' and not point['capabilities']) or (point['focus']!='capability' and point['capabilities']):
            errors.append('Point focus/capabilities mismatch: '+json.dumps({k:point[k] for k in ('id','focus','capabilities')},ensure_ascii=False)
                +'. capability requires one controlled capability; workflow/other/uncertain require an empty list. Correct the explicit metadata from the actual point scope; do not guess from title keywords.')
        refs=point['componentIds'];point_components=[components[i] for i in refs if i in components]
        positive={c['criterionId'] for c in point_components if c['coverage'] in ('supported','partial')}
        if len(refs)!=len(set(refs)) or len(point_components)!=len(refs) or not positive:
            errors.append('Match requires existing positive components: '+point['id'])
        if any(c['criterionId'] not in positive for c in point_components):
            errors.append('Match limitations must belong to its positive linked criteria: '+point['id'])
    focus_keys=[]
    for focus in focuses.values():
        c=components.get(focus['componentId'])
        if not c:errors.append('Focus references a missing component: '+focus['id']);continue
        if not normalize_evidence(focus['label']):errors.append('Focus label must be nonempty: '+focus['id'])
        if len(focus_goal(focus,c))>180:errors.append('Focus component exceeds the existing single-goal length bound: '+focus['id'])
        focus_keys.append((focus['componentId'],focus['objective'],focus['experienceId']))
        experience=focus['experienceId']
        if experience is None and (focus['objective']!='existence' or focus['evidence']):
            errors.append('Unanchored focus must be an existence check with empty evidence: '+focus['id'])
        if experience is not None and (experience not in experiences or not focus['evidence']):
            errors.append('Anchored focus requires an existing experience and evidence: '+focus['id'])
    if len(focus_keys)!=len(set(focus_keys)):errors.append('Verification focuses must represent distinct missing answers/angles')
    concern_focus=[c['focusId'] for c in data['concerns']]
    if len(concern_focus)!=len(set(concern_focus)) or not set(concern_focus)<=focuses.keys():
        errors.append('Each concern must reference one distinct existing focus')
    if set(c['id'] for c in data['concerns']) & set(p['id'] for p in data['matches']):
        errors.append('Match and concern IDs must not collide')
    selected=[t['focusId'] for t in data['verificationTargets']]
    if len(selected)!=3 or len(set(selected))!=3 or not set(selected)<=focuses.keys():
        errors.append('Select exactly three distinct existing focuses')
    # A target must link a projected concern, or a relevant supported match.
    for i in selected:
        if i not in focuses:continue
        c=components.get(focuses[i]['componentId'])
        if c and i not in concern_focus:
            matching=[p for p in data['matches'] if c['id'] in p['componentIds'] and c['coverage'] in ('supported','partial')]
            if not matching:errors.append('Unresolved target needs its own concern; supported target needs a matching point: '+i)
    return list(dict.fromkeys(errors))


def _point_layout(data):
    """Derive support once, narrate each fact once, retain every linked scope.

    A wholly redundant point folds into its first existing fact owner. Other
    points keep their own new facts and all supporting source references. No
    source-to-requirement association is inferred here.
    """
    components={c['id']:c for c in data['components']}
    points=[];owners={}
    for original in data['matches']:
        point=deepcopy(original)
        support=list(dict.fromkeys(i for cid in point['componentIds']
            if components[cid]['coverage'] in ('supported','partial')
            for i in components[cid]['factIds']))
        owned=[i for i in support if i not in owners]
        if not owned:
            owner=owners[support[0]]
            owner['componentIds']=list(dict.fromkeys(owner['componentIds']+point['componentIds']))
            owner['supportFactIds']=list(dict.fromkeys(owner['supportFactIds']+support))
            owner['supplementaryEvidence']=_refs(owner['supplementaryEvidence']+point['supplementaryEvidence'])
            continue
        point['factIds']=owned
        point['supportFactIds']=support
        points.append(point)
        for fact_id in owned:owners[fact_id]=point
    return points


def project_match(data,standards,profile):
    data=active_structure(data)
    errors=validate_structure(data,standards,profile)
    empty={'observations':[],'matches':[],'concerns':[],'summary':'','keyUnknown':None}
    if errors:return empty,[],[],errors
    facts={f['id']:f for f in data['facts']};components={c['id']:c for c in data['components']}
    focuses={f['id']:f for f in data['focuses']};criteria={c['id']:c for c in standards['criteria']}
    def fact_refs(ids):return _refs(r for i in ids for r in facts[i]['evidence'])
    def requirement(ids):return '；'.join(criteria[i]['description'] for i in ids)
    analysis=deepcopy(empty);grounding=[]
    for o in data['observations']:
        scopes=[components[i] for i in o['componentIds']]
        ids=list(dict.fromkeys(i for c in scopes for i in c['factIds']))
        asserted=list(dict.fromkeys(i for c in scopes if c['coverage']!='unknown' for i in c['factIds']))
        analysis['observations'].append({'criterionId':o['criterionId'],'coverage':o['coverage'],
            'explanation':''.join(facts[i]['claim'].rstrip('。')+'。' for i in asserted)+''.join(component_text(c) for c in scopes),
            'evidence':fact_refs(ids)})
    points=_point_layout(data)
    for p in points:
        scopes=[components[i] for i in p['componentIds']]
        linked=list(dict.fromkeys(c['criterionId'] for c in scopes if c['coverage'] in ('supported','partial')))
        assessment=''.join(component_text(c) for c in scopes)
        claims=[{'claim':facts[i]['claim'],'evidence':deepcopy(facts[i]['evidence'])} for i in p['factIds']]
        analysis['matches'].append({'id':p['id'],'title':p['title'],'criterionIds':linked,'jdRequirement':requirement(linked),
            'explanation':'','evidence':_refs(fact_refs(p['supportFactIds'])+p['supplementaryEvidence']),'kind':'match'})
        grounding.append({'pointId':p['id'],'focus':p['focus'],'capabilities':p['capabilities'],
            'scopeReason':p['scopeReason'],'claims':claims,'assessment':assessment})
    concern_by_focus={c['focusId']:c['id'] for c in data['concerns']}
    for concern in data['concerns']:
        f=focuses[concern['focusId']];c=components[f['componentId']]
        analysis['concerns'].append({'id':concern['id'],'title':f['label']+'待确认',
            'criterionIds':[c['criterionId']],'jdRequirement':requirement([c['criterionId']]),
            'explanation':component_text(c),'evidence':fact_refs(c['factIds']),
            'kind':'gap' if c['coverage']=='contradiction' else 'verify'})
    targets=[]
    for t in data['verificationTargets']:
        f=focuses[t['focusId']];c=components[f['componentId']]
        point_id=concern_by_focus.get(f['id'])
        if point_id is None:point_id=next(p['id'] for p in points if c['id'] in p['componentIds'])
        targets.append({'id':t['id'],'pointId':point_id,'criterionId':c['criterionId'],
            'singleGoal':focus_goal(f,c),'decisionReason':t['decisionReason'],
            'experienceId':f['experienceId'],'evidence':deepcopy(f['evidence'])})
    analysis=reading_order(analysis,standards)
    analysis['keyUnknown']=analysis['concerns'][0]['title'] if analysis['concerns'] else None
    return analysis,grounding,targets,[]


def review_projection(data,analysis):
    """No separately rewritten body is supplied as another source of facts."""
    data=active_structure(data)
    facts={f['id']:f for f in data['facts']}
    points=_point_layout(data)
    supplementary=[]
    for p in analysis['matches']:
        point=next(row for row in points if row['id']==p['id'])
        stated={(r['blockId'],normalize_evidence(r['quote'])) for i in point['factIds'] for r in facts[i]['evidence']}
        for r in p['evidence']:
            if (r['blockId'],normalize_evidence(r['quote'])) in stated:continue
            digest=hashlib.sha256((p['id']+'\0'+r['blockId']+'\0'+r['quote']).encode()).hexdigest()[:16]
            supplementary.append({'id':'context-'+digest,'pointId':p['id'],**deepcopy(r)})
    result={'evidenceProtocol':2,'facts':deepcopy(data['facts']),'components':deepcopy(data['components']),
        'observations':deepcopy(data['observations']),
        'points':[{k:deepcopy(v) for k,v in p.items() if k!='supplementaryEvidence'} for p in points],
        'focuses':deepcopy(data['focuses']),'concerns':deepcopy(data['concerns']),
        'verificationTargets':deepcopy(data['verificationTargets']),
        'supplementarySources':[{'id':r['id'],'pointId':r['pointId'],'blockId':r['blockId']} for r in supplementary]}
    return result,supplementary


def validate_review(review,data,analysis,standards):
    """Validate findings against actual current objects; invalid reviews fail closed."""
    data=active_structure(data)
    criteria={c['id']:c for c in standards['criteria']}
    components={c['id']:c for c in data['components']}
    _,supplementary=review_projection(data,analysis)
    catalog={('fact',f['id']):([f['claim']],{c['criterionId'] for c in components.values() if f['id'] in c['factIds']}) for f in data['facts']}
    catalog.update({('component',c['id']):([c['component'],component_text(c)],{c['criterionId']}) for c in components.values()})
    catalog.update({('focus',f['id']):([f['label'],components[f['componentId']]['component'],focus_goal(f,components[f['componentId']])],
                    {components[f['componentId']]['criterionId']}) for f in data['focuses']})
    catalog.update({('supplementary_source',r['id']):([r['quote']],set()) for r in supplementary})
    allowed={'fact':{'unsupported_fact','presentation'},'component':{'criterion_scope','presentation'},
             'focus':{'verification_scope','presentation'},'supplementary_source':{'irrelevant_context'}}
    errors=[]
    for f in review['findings']:
        key=(f['subjectType'],f['subjectId']);subject=catalog.get(key)
        if not subject:errors.append('Review finding references a missing current subject: '+f['subjectId']);continue
        if f['issueType'] not in allowed[f['subjectType']]:
            errors.append('Review finding crosses the fact/context boundary: '+f['subjectId'])
        statement=normalize_evidence(f['statement']).rstrip('。；;.!！?？')
        if not statement or not any(statement==normalize_evidence(text).rstrip('。；;.!！?？') for text in subject[0]):
            errors.append('Review finding must quote its actual current subject: '+f['subjectId'])
        if f['issueType']=='unsupported_fact' and not f['evidence']:
            errors.append('Factual review objection requires an original-source basis: '+f['subjectId'])
        if f['criterionId'] is not None and f['criterionId'] not in subject[1]:
            errors.append('Review finding uses an unrelated criterion: '+f['subjectId'])
        if f['issueType'] in ('criterion_scope','verification_scope'):
            c=criteria.get(f['criterionId']);quote=normalize_evidence(f['requirementQuote'] or '')
            if not c or not quote or not any(quote in normalize_evidence(c[k]) for k in ('description','jdQuote')):
                errors.append('Coverage/goal finding needs its actual confirmed requirement quote: '+f['subjectId'])
    blockers=[f for f in review['findings'] if f['severity']=='blocker']
    if not review['approved'] and not blockers:
        errors.append('Rejected structured review requires a concrete current blocker')
    return list(dict.fromkeys(errors))


def repair_findings(review):
    return [f"[{f['subjectType']}:{f['subjectId']}] {f['message']} Current statement: {f['statement']}"
            for f in review['findings'] if f['severity']=='blocker']

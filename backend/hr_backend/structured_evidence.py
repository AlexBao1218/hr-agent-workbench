"""Private evidence protocol: one fact/component ledger, public wire projections.

No semantic keyword classification or candidate-specific correction lives here.
The model still selects/assesses source-grounded components; code enforces their
identities, references and projection boundaries. Legacy saved data is untouched.
"""
from copy import deepcopy
import hashlib
import json
import re

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


# v3 stays separate from the archived v2 decoder/projection above.
def row_payload(document,standards,hat,context,feedback,profile):
    """Full source text once; geometry stays in the server document/viewer."""
    from .evidence import source_ownership_context
    navigation=source_ownership_context(document,profile)
    groups={}
    for source in navigation['sources']:
        key=(source['experienceId'],source['organization'],source['basis'],
             tuple(r['blockId'] for r in source['headerEvidence']))
        groups.setdefault(key,[]).append(source['blockId'])
    ownership=[{'experienceId':eid,'organization':org,'basis':basis,
                'headingBlockIds':list(headers),'blockIds':ids}
               for (eid,org,basis,headers),ids in groups.items()]
    def source_ids(value):
        text=normalize_evidence(value or '')
        return [b['id'] for b in document['blocks'] if text and text in normalize_evidence(b['text'])]
    # Preserve useful CV extraction across stages without duplicating source quotes.
    # These values are navigation only; row claims must still cite the original blocks.
    profile_navigation={'education':[{**{k:e[k] for k in ('school','degree','major','level','status')},
                        'sourceBlockIds':list(dict.fromkeys(r['blockId'] for r in e['evidence']))} for e in profile['education']],
                        'languages':[{'value':v,'sourceBlockIds':source_ids(v)} for v in profile['languages']],
                        'availability':{'value':profile['availability'],'sourceBlockIds':source_ids(profile['availability'])}}
    return {'document':{'id':document['id'],'blocks':[{'id':b['id'],'text':b['text']} for b in document['blocks']]},
            'standards':{'version':deepcopy(standards['version']),'criteria':deepcopy(standards['criteria'])},
            'confirmedRequirementScope':[{'criterionId':c['id'],'requirement':c['description'],'authority':'Only this HR-confirmed description defines this criterion. jdQuote is source context, not additional duties or bonus conditions.'} for c in standards['criteria']],
            'hat':deepcopy(hat),'context':deepcopy(context),'feedback':deepcopy(feedback),
            # HR's confirmed description defines scope. A broader JD quote can
            # contain a separate bonus criterion and must not expand this row.
            'compoundRequirementClauses':[{'criterionId':c['id'],'basis':'confirmed_description','clauses':[part.strip() for part in re.split(r'[;；，]',c['description']) if part.strip()]} for c in standards['criteria'] if re.search(r'[;；，]',c['description'])],
            'profileNavigation':profile_navigation,
            'experiences':[{k:e[k] for k in ('id','organization','kind')} for e in profile['experiences']],
            'sourceOwnership':{'groups':ownership,'policy':navigation['policy']}}


def _row_order(data,standards):
    criteria={c['id']:c for c in standards['criteria']}
    return sorted(data['rows'],key=lambda r:(criteria[r['criterionId']]['group']!='must',criteria[r['criterionId']]['priority']))


def validate_rows(data,standards,profile):
    criteria={c['id']:c for c in standards['criteria']};rows=data['rows'];errors=[]
    seen=[r['criterionId'] for r in rows]
    if len(seen)!=len(set(seen)) or set(seen)!=criteria.keys():
        return ['Rows must cover each confirmed criterion exactly once']
    from .evidence import source_owners
    experiences={e['id'] for e in profile['experiences']};owners=source_owners(profile)
    for row in rows:
        cid=row['criterionId'];criterion=criteria[cid]
        states=[e['state'] for e in row['evidence']]
        if any(not normalize_evidence(e['claim']) for e in row['evidence']):
            errors.append('Candidate evidence needs a nonempty fact: '+cid)
        if row['coverage'] in ('supported','partial') and not any(s in ('supported','partial') for s in states):
            errors.append('Positive row requires source-backed support: '+cid+
                '. Current coverage='+row['coverage']+', evidence states='+str(states)+
                '. Context contributes ZERO support. If all facts are only related background, change this row coverage to unknown; '
                'otherwise correct an evidence state only when its exact requirement constituent is actually supported and explain that limited scope. '
                'Never upgrade context merely to pass this check. Preserve the true source facts.')
        if row['coverage']=='unknown' and 'contradiction' in states:
            errors.append('Unknown row cannot also assert an explicit conflicting source: '+cid)
        if row['coverage']=='contradiction' and 'contradiction' not in states:
            errors.append('Contradiction row requires an explicit conflicting source: '+cid)
        # Alternatives may be overall supported with unknown/contradicted alternatives.
        # Overall coverage is never replaced by max/min of child states.
        for section in ('evidence','unknowns'):
            for index,item in enumerate(row[section]):
                quote=normalize_evidence(item['requirementQuote'])
                if not quote or not any(quote in normalize_evidence(criterion[k]) for k in ('description','jdQuote')):
                    import json
                    errors.append('Row item must quote its own confirmed requirement exactly: '+cid+'/'+section+'/'+str(index)+': '+json.dumps({
                        'invalidRequirementQuote':item['requirementQuote'],'originalDescription':criterion['description'],
                        'originalJdQuote':criterion['jdQuote'],'correction':'Use one contiguous exact substring of this criterion. Do not reconstruct a shared predicate across an AND/OR clause.'},ensure_ascii=False))
        for gap in row['unknowns']:
            if not normalize_evidence(gap['singleGoal']):errors.append('Unknown requires one nonempty goal: '+cid)
            if gap['experienceId'] is None:
                if gap['evidence']:errors.append('Unanchored unknown must have empty evidence: '+cid)
            elif (gap['experienceId'] not in experiences or not gap['evidence'] or
                  any(len(owners.get(r['blockId'],[]))!=1 or owners[r['blockId']][0]['id']!=gap['experienceId'] for r in gap['evidence'])):
                errors.append('Anchored unknown requires its actual experience and source: '+cid)
    return list(dict.fromkeys(errors))


def _row_fact_key(item):
    return (normalize_evidence(item['claim']),tuple(sorted((r['blockId'],normalize_evidence(r['quote'])) for r in item['evidence'])))


def _row_assessment(row,criterion,reading=False):
    if reading:
        if row['coverage']=='supported':return '相关证据支持该项确认标准。'
        limits=list(dict.fromkeys(g['conclusion'] for g in row['unknowns'] if g.get('conclusion')))
        return '相关证据提供部分支持。'+('仍待确认：'+'；'.join(limits)+'。' if limits else '该项要求尚未完整体现。')
    scope=next((f.get('scope','').strip() for f in row['evidence'] if f['state'] in ('supported','partial') and f.get('scope','').strip()),'')
    return (scope.rstrip('。')+'。' if scope else '')+('相关证据提供部分支持，其他未知项仍待确认。' if row['coverage']=='partial'
            else '相关证据支持该项确认标准。')


def project_rows(data,standards,profile):
    """Public DTOs from row evidence; no candidate capability/title inference."""
    errors=validate_rows(data,standards,profile)
    empty={'observations':[],'matches':[],'concerns':[],'summary':'','keyUnknown':None}
    if errors:return empty,[],[],[],errors
    rows=_row_order(data,standards);criteria={c['id']:c for c in standards['criteria']}
    result=deepcopy(empty);grounding=[];owners={};selected=[];eligible=[];seen_goals=set()
    for row in rows:
        cid=row['criterionId'];criterion=criteria[cid]
        facts=row['evidence']
        lines=[f['claim'].rstrip('。')+'。'+(('该项仍未知；' if row['coverage']=='unknown' else '')+'仅作相关背景，不支持该标准片段。' if f['state']=='context' else
               '该项仍未知；保留相关事实不表示满足标准片段。' if row['coverage']=='unknown' else
               {'supported':'相关证据支持该标准片段。','partial':'相关证据仅部分支持该标准片段。',
                'contradiction':'相关原文与该标准片段明确冲突。'}[f['state']])+'「'+f['requirementQuote']+'」'+(('支持范围：'+f['scope'].rstrip('。')+'。') if f.get('scope') else '') for f in facts]
        lines += ['待确认：'+g['singleGoal'] for g in row['unknowns']]
        if not lines:lines=['材料未提供该项标准的可用证据，待确认。']
        result['observations'].append({'criterionId':cid,'coverage':row['coverage'],
            'explanation':'\n'.join(lines),'evidence':_refs(r for f in facts+row['unknowns'] for r in f['evidence'])})
        positive=[];seen_facts=set()
        for fact in facts:
            key=_row_fact_key(fact)
            if fact['state'] in ('supported','partial') and key not in seen_facts:
                positive.append(fact);seen_facts.add(key)
        if row['coverage'] in ('supported','partial') and positive:
            fresh=[f for f in positive if _row_fact_key(f) not in owners]
            if not fresh:
                point=owners[_row_fact_key(positive[0])]
                if cid not in point['criterionIds']:point['criterionIds'].append(cid)
                if row['coverage']=='partial':
                    next(g for g in grounding if g['pointId']==point['id'])['assessment']=_row_assessment(row,criterion,bool(data.get('reading')))
                point['jdRequirement']='；'.join(criteria[i]['description'] for i in point['criterionIds'])
                point['evidence']=_refs(point['evidence']+[r for f in positive for r in f['evidence']])
            elif len(result['matches'])<6:
                assessment=_row_assessment(row,criterion,bool(data.get('reading')));claims=[]
                # Reuse the existing display bound; never cut a fact/qualification.
                # All evidence/claims remain complete in the row and observation.
                for fact in fresh:
                    proposed=[*claims,{'claim':fact['claim'],'evidence':deepcopy(fact['evidence'])}]
                    if len('；'.join(f['claim'].rstrip('。；;') for f in proposed)+'。'+assessment)<=360:claims=proposed
                if not claims:
                    errors.append('No complete source-backed claim fits the existing 360-character display bound for '+cid+'; shorten the source-backed wording without dropping qualifications. Full row evidence is retained.')
                else:
                    point={'id':'match-'+cid,'title':row.get('conclusion') or criterion['title'],'criterionIds':[cid],
                           'jdRequirement':criterion['description'],'explanation':'',
                           'evidence':_refs(r for f in positive for r in f['evidence']),'kind':'match'}
                    result['matches'].append(point)
                    grounding.append({'pointId':point['id'],'focus':'other','capabilities':[],
                        'scopeReason':'按确认标准逐项展示；不从标题或关键词推断独立能力归属。',
                        'claims':claims,'assessment':assessment})
                    for fact in claims:owners[_row_fact_key(fact)]=point
        # Fully supported OR/preference alternatives are retained, not extra interview obligations.
        if row['coverage']!='supported':
            for index,gap in enumerate(row['unknowns']):
                key=normalize_evidence(gap['singleGoal']).casefold()
                if key in seen_goals:continue
                seen_goals.add(key)
                eligible.append({'criterionId':cid,'index':index})
    if data.get('reading'):
        by_id={r['criterionId']:r for r in rows}
        for point in result['matches']:
            assessments=list(dict.fromkeys(_row_assessment(by_id[cid],criteria[cid],True) for cid in point['criterionIds']))
            next(g for g in grounding if g['pointId']==point['id'])['assessment']=' '.join(assessments)
        from .outcome_reading import project_reading
        return project_reading(data,result,grounding,standards,profile)
    # Legacy saved rows retain their original deterministic selection.
    selected_criteria=set()
    for choice in eligible:
        if choice['criterionId'] not in selected_criteria and len(selected)<3:
            selected.append(choice);selected_criteria.add(choice['criterionId'])
    for choice in eligible:
        if len(selected)<3 and choice not in selected:selected.append(choice)
    targets=[]
    by_id={r['criterionId']:r for r in rows}
    for slot,choice in enumerate(selected):
        cid=choice['criterionId'];row=by_id[cid];gap=row['unknowns'][choice['index']];criterion=criteria[cid]
        conflicts=[r for f in row['evidence'] if f['state']=='contradiction' for r in f['evidence']]
        point={'id':'unknown-'+cid+'-'+str(choice['index']),'title':criterion['title'][:29]+'待确认',
            'criterionIds':[cid],'jdRequirement':criterion['description'],
            'explanation':'待确认：'+gap['singleGoal'],'evidence':_refs(gap['evidence']+conflicts),
            'kind':'gap' if row['coverage']=='contradiction' else 'verify'}
        result['concerns'].append(point)
        targets.append({'id':'target-'+str(slot+1),'pointId':point['id'],'criterionId':cid,
            'singleGoal':gap['singleGoal'],'decisionReason':'按已确认标准的组别及优先级选择这一单目标；其他未知仍完整保留。',
            'experienceId':gap['experienceId'],'evidence':deepcopy(gap['evidence'])})
    if len(targets)!=3:errors.append('Fewer than three distinct eligible unresolved goals; do not invent gaps to fill slots')
    result=reading_order(result,standards)
    result['keyUnknown']=result['concerns'][0]['explanation'] if result['concerns'] else None
    return result,grounding,targets,selected,errors


def row_claim_grounding(data):
    return [{'pointId':r['criterionId'],'claims':r['evidence']} for r in data['rows']]


def _current_unknown_list(statement,row,cid):
    """A missing subclause concerns the complete current list, not a nonexistent item.

    Accept only exact, ordered current titles/goals with a literal list wrapper;
    arbitrary prose, stale entries and incomplete lists remain invalid.
    """
    import re
    text=statement.strip()
    for prefix in (cid+' unknowns:',cid+' unknowns：','unknowns:','unknowns：'):
        if text.startswith(prefix):text=text[len(prefix):].strip();break
    pattern=r'''(['"])(.*?)\1'''
    pieces=[match[1] for match in re.findall(pattern,text)]
    if re.sub(pattern,'',text).strip(' ,;，；、[]\n\r\t'):return False
    actual=[normalize_evidence(value) for value in pieces]
    for field in ('singleGoal','conclusion'):
        expected=[item.get(field) for item in row['unknowns']]
        if all(isinstance(value,str) and value for value in expected) and actual==[normalize_evidence(value) for value in expected]:return True
    return not row['unknowns'] and not pieces and text=='[]'


def validate_row_review(review,data,standards,require_checks=False):
    rows={r['criterionId']:r for r in data['rows']};criteria={c['id']:c for c in standards['criteria']};errors=[]
    for finding in review['findings']:
        cid=finding['criterionId'];row=rows.get(cid);section=finding['section'];index=finding['index']
        if row is None:errors.append('Review references a missing current criterion');continue
        item=None
        if section=='coverage':
            text=row['coverage'] if index is None else None
        elif section=='unknown' and index is None:
            text=finding['statement'] if _current_unknown_list(finding['statement'],row,cid) else None
        else:
            items=row['unknowns' if section=='unknown' else 'evidence']
            item=items[index] if type(index) is int and 0<=index<len(items) else None
            field='scope' if section=='scope' else 'claim' if section=='evidence' else 'singleGoal'
            text=item.get(field) if item else None
        if text is None or normalize_evidence(text)!=normalize_evidence(finding['statement']):
            errors.append('Review must quote its complete current row subject: '+cid)
        quote=normalize_evidence(finding['requirementQuote'])
        if not quote or not any(quote in normalize_evidence(criteria[cid][k]) for k in ('description','jdQuote')):
            errors.append('Review must quote its own confirmed requirement: '+cid)
        if item and quote not in normalize_evidence(item['requirementQuote']):
            errors.append('Review requirement must address this current item, not another retained subpart: '+cid)
        if section in ('evidence','scope') and not finding['evidence']:
            errors.append('Factual review objection requires original-source evidence: '+cid)
    blockers=[f for f in review['findings'] if f['severity']=='blocker']
    reading=review.get('reading')
    reading_blocked=False
    if data.get('reading'):
        if not reading:errors.append('Review must explicitly check candidate conclusions, summary and interview selection')
        else:
            reading_blocked=any(reading[k]=='block' for k in ('conclusions','summary','interviewSelection'))
            if reading_blocked!=bool(reading['issues']):errors.append('Reading review blocks need concrete current issues; passing checks cannot carry blockers')
            if reading_blocked and review['approved']:errors.append('Blocked reading review cannot be approved')
    checks=review.get('checks',[])
    if require_checks or checks:
        ids=[check['criterionId'] for check in checks]
        if len(ids)!=len(set(ids)) or set(ids)!=set(rows):
            errors.append('Review checks must cover every current criterion exactly once')
        blocked={f['criterionId'] for f in blockers}
        for check in checks:
            row=rows.get(check['criterionId'])
            if row is None:continue
            has_block=any(check[key]=='block' for key in ('facts','scope','coverage','unknowns'))
            if has_block!=(row['criterionId'] in blocked):
                errors.append('Review check and concrete blocker findings disagree: '+row['criterionId'])
            if has_block and review['approved']:errors.append('Blocked row check cannot be approved')
    if not review['approved'] and not blockers and not reading_blocked:errors.append('Rejected row review requires a current concrete blocker')
    return list(dict.fromkeys(errors))


def row_repair_findings(review):
    return [f"[{f['criterionId']}/{f['section']}/{f['index']}] {f['message']} Current statement: {f['statement']}"
            for f in review['findings'] if f['severity']=='blocker'] + [
            '[reading] '+issue for issue in (review.get('reading') or {}).get('issues',[])]

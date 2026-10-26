"""Deterministic candidate-source retrieval, not scoring or semantic proof."""
from copy import deepcopy
import re
import hashlib
import json
import unicodedata

CAPABILITIES={
    'rag':r'\bRAG\b|retrieval[ -]augmented(?:[ -]generation)?|检索增强(?:生成)?',
    'python':r'(?:in|using|via|with)\s+Python\b|\bPython\s+(?:for\s+)?(?:scripts?|tools?|code|program|pipeline|service|retriev|data|automation|classification|analysis)|Python.{0,30}(?:分析|脚本|自动化)|(?:使用|用)Python',
    'data_analysis':r'data analy\w*|dashboards?|\bGPS\b|\bMILP\b|optimi[sz]\w*|calculation model|energy.saving|classif\w*.*(?:documents?|data)|数据分析|仪表盘|测算|优化|数据分类',
}
TITLE_CAPABILITIES={
    'rag':r'\bRAG\b|检索|知识库',
    'python':r'\bPython\b',
    'data_analysis':r'data analy\w*|数据分析|数据建模|数据驱动|量化分析',
}
ACTIVITY=r'\b(?:built|developed|designed|analy[sz]ed|implemented|created|automated|added|tested|evaluated|compared|used|proposed|formulated|researched|classified|tuned|computed)\b|开发|构建|分析|研究|设计|测试|计算|建立'
BACKGROUND=r'^\s*(?:skills?|major courses?|coursework|familiar with|skilled in|技能|课程)\s*[:：]?'


def source_owners(profile):
    owners={}
    for experience in profile.get('experiences',[]):
        owner={k:experience[k] for k in ('id','organization','kind')}
        for ref in experience['evidence']:
            bucket=owners.setdefault(ref['blockId'],[])
            if owner not in bucket:bucket.append(owner)
    return owners


def _owner_key(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum())


def source_ownership_context(document,profile):
    """Source-backed section navigation. Page breaks do not start new employers.

    Only establish a span from an organization actually printed in a referenced
    heading. Profile summaries and neighboring company industry are never proof.
    Unrecognized headings remain for semantic review rather than guessed owners.
    """
    blocks=document.get('blocks',[]);positions={b['id']:i for i,b in enumerate(blocks)}
    headings={};explicit=source_owners(profile)
    for exp in profile.get('experiences',[]):
        refs=[r for r in exp['evidence'] if r['blockId'] in positions]
        if not refs:continue
        start=min(positions[r['blockId']] for r in refs)
        header=[]
        for block in blocks[start:start+5]:
            text=block['text'].strip()
            if re.match(ACTIVITY,text,re.I):break
            # Dates can be interleaved with a wrapped company heading by PDF extraction.
            if re.match(r'^\d{1,4}[/.-]\d{1,4}\s*[-–]',text):continue
            header.append({'blockId':block['id'],'quote':block['text']})
            if _owner_key(exp['organization']) in _owner_key(' '.join(r['quote'] for r in header)):
                headings[start]={'organization':exp['organization'],'experienceId':exp['id'],
                                 'basis':'source_heading','headerEvidence':deepcopy(header)}
                break
    sources=[];current=None
    for i,block in enumerate(blocks):
        text=block['text'].strip()
        if i in headings:current=headings[i]
        elif re.fullmatch(r'(?:EDUCATION|(?:WORK|PROFESSIONAL|INTERNSHIP) EXPERIENCE|(?:ACADEMIC |COURSE |RESEARCH )?PROJECTS?(?: EXPERIENCE)?|SKILLS(?: AND INTERESTS)?|(?:EXTRA)?CURRICULAR ACTIVIT(?:Y|IES)|AWARDS(?: AND HONORS)?|REFERENCES)',text,re.I):current=None
        owners=explicit.get(block['id'],[])
        # Conflicting direct profile attribution must not silently override source layout.
        if current and (not owners or all(o['id']==current['experienceId'] for o in owners)):
            sources.append({'blockId':block['id'],**deepcopy(current)})
        elif len(owners)==1 and not current:
            owner=owners[0]
            sources.append({'blockId':block['id'],'organization':owner['organization'],
                            'experienceId':owner['id'],'basis':'profile_reference','headerEvidence':[]})
    return {'sources':sources,'policy':'Navigation only. Verify employer against printed heading and activity; a page break does not change ownership. Never transfer domains between projects.'}


def validate_source_ownership(analysis,grounding,document,profile):
    """Reject clear one-employer/foreign-activity contradictions, not missing aliases.

    Multi-employer paragraphs remain subject to claim-level semantic review.
    This guard never rewrites a claim or creates a source reference.
    """
    context=source_ownership_context(document,profile)
    owners={s['blockId']:s for s in context['sources'] if s['basis']=='source_heading'}
    texts={b['id']:b['text'] for b in document.get('blocks',[])}
    aliases={}
    for exp in profile.get('experiences',[]):
        org=exp['organization'];tokens=re.findall(r'[A-Za-z0-9]+',org)
        alias=' '.join(tokens[:3]) if len(tokens)>=3 else org
        aliases[exp['id']]=(_owner_key(org),_owner_key(alias))
    rows=[]
    for key in ('observations','concerns'):
        for item in analysis.get(key,[]):rows.append((item.get('id',item.get('criterionId')),item.get('explanation',''),item.get('evidence',[])))
    for item in grounding:
        for claim in item.get('claims',[]):rows.append((item['pointId'],claim['claim'],claim['evidence']))
    errors=[]
    for location,text,refs in rows:
        named={eid for eid,keys in aliases.items() if any(k and k in _owner_key(text) for k in keys)}
        if len(named)!=1:continue
        expected=next(iter(named))
        for ref in refs:
            owner=owners.get(ref['blockId'])
            if owner and owner['experienceId']!=expected and re.match(r'\s*(?:Prepared|Assisted|Supported|Managed|Participated|Conducted|Led|Updated|Tracked|Coordinated|Produced|Drafted|Collaborated|'+ACTIVITY+r')',texts.get(ref['blockId'],''),re.I):
                errors.append(f"Source ownership mismatch in {location}: activity {ref['blockId']} belongs under printed heading {owner['organization']}; correct this claim's employer or remove that attribution, not the source heading.")
    return list(dict.fromkeys(errors))


def source_scope(text,owners,capabilities):
    research=bool(re.search(r'\bresearched\b|算法研究|研究',text,re.I))
    proposal=bool(re.search(r'\bproposed\b|提议|建议',text,re.I))
    modeling=bool(re.search(r'calculation model|测算|建模',text,re.I))
    label=('算法/方法研究' if research else '定量测算建模' if modeling else '开发或数据处理活动自述')
    names={'rag':'RAG构建/检索活动','python':'明确Python使用','data_analysis':'数据处理或定量问题求解相关活动'}
    scope='；'.join(names[k] for k in capabilities)
    limits=[]
    if 'python' not in capabilities:limits.append('未证明使用Python')
    if research:limits.append('研究不等于实现、工作交付或生产部署')
    if modeling:limits.append('模型构建不等于已验证业务收益')
    if proposal:limits.append('提议值不等于已实现收益')
    if owners and all(o['kind']=='project' for o in owners):limits.append('项目经历，不计作实习')
    return {'activityType':label,'supportedSubcapabilities':scope,'limitations':limits}


def scope_explanation(source):
    owners='、'.join(o['organization'] for o in source['owners']) or '经历归属待确认'
    scope=source['scope']
    return f"来源：{owners}。范围：{scope['activityType']}；{scope['supportedSubcapabilities']}。"+'；'.join(scope['limitations'])


def canonical_requirements(analysis,standards):
    result=deepcopy(analysis);criteria={c['id']:c for c in standards['criteria']}
    for point in result.get('matches',[])+result.get('concerns',[]):
        if all(cid in criteria for cid in point['criterionIds']):
            point['jdRequirement']='；'.join(criteria[cid]['description'] for cid in dict.fromkeys(point['criterionIds']))
    return result

def capability_index(document,profile):
    owners=source_owners(profile)
    result={key:[] for key in CAPABILITIES}
    result['contextOnly']=[]
    education_blocks={r['blockId'] for e in profile.get('education',[]) for r in e['evidence']}
    for block in document['blocks']:
        text=block['text']
        if re.search(BACKGROUND,text,re.I):continue
        if block['id'] in education_blocks and block['id'] not in owners:continue
        capabilities=[key for key,pattern in CAPABILITIES.items() if re.search(pattern,text,re.I)]
        source={'blockId':block['id'],'quote':text,'owners':owners.get(block['id'],[])}
        if not capabilities:
            if re.search(r'retriev\w*|knowledge bases?|检索|知识库',text,re.I):
                result['contextOnly'].append({**source,'candidateCapabilities':[], 'eligibility':'context_only','reasons':['retrieval_is_not_rag']})
            continue
        reasons=[]
        if not re.search(ACTIVITY,text,re.I):reasons.append('no_asserted_activity')
        if re.search(r"(?:never|\bnot\b|n't|no experience|unable to|cannot|未曾|从未|没有|未|无法).{0,50}(?:develop|build|built|implement|use|RAG|Python|retriev|开发|构建|分析|使用|研究)",text,re.I):
            reasons.append('negative_or_unasserted_activity')
        if re.search(r'red[ ,/-]*amber[ ,/-]*green|traffic.light|RAG\s*(?:status|\(\s*red)',text,re.I):
            reasons.append('ambiguous_non_retrieval_acronym')
        if re.search(r'\b(?:would|could|might|want to|plan to|hope to|aim to|if I)\b|计划开发|希望开发|如果',text,re.I):
            reasons.append('hypothetical_activity')
        if len(source['owners'])!=1:reasons.append('unverified_or_ambiguous_experience_owner')
        if reasons:
            result['contextOnly'].append({**source,'candidateCapabilities':capabilities,'eligibility':'context_only','reasons':reasons})
            continue
        source['scope']=source_scope(text,source['owners'],capabilities)
        for key in capabilities:result[key].append(deepcopy(source))
    return result


def profile_navigation(profile):
    """Source navigation only: generated summaries cannot establish project domains."""
    result=deepcopy(profile)
    for experience in result.get('experiences',[]):
        experience.pop('summary',None)
    return result


def review_context(analysis,grounding,index):
    """Keep source text in document/analysis, not repeated in every sidecar."""
    roles=point_evidence_roles(analysis,grounding)
    for role in roles:
        role['supplementaryEvidence']=[{'blockId':r['blockId']} for r in role['supplementaryEvidence']]
    scopes=[{k:deepcopy(v) for k,v in g.items() if k not in ('claims','assessment')} for g in grounding]
    compact_index={key:[{k:deepcopy(v) for k,v in source.items() if k!='quote'} for source in sources]
                   for key,sources in index.items()}
    return {'pointGrounding':scopes,'pointEvidenceRoles':roles,'capabilityEvidenceIndex':compact_index}


def point_capabilities(point, grounding=None):
    if grounding is not None:
        entry=next((g for g in grounding if g['pointId']==point['id']),None)
        return list(dict.fromkeys(entry['capabilities']))[:1] if entry and entry.get('focus','capability')=='capability' else []
    # Legacy offline helper compatibility only. Workflow always supplies explicit scopes.
    return [k for k,pattern in TITLE_CAPABILITIES.items() if re.search(pattern,point['title'],re.I)]


def compose_point_explanations(analysis, grounding):
    """One factual wording, rendered once; public contract remains unchanged."""
    result=deepcopy(analysis)
    entries={g['pointId']:g for g in grounding}
    for point in result.get('matches',[]):
        entry=entries.get(point['id'],{})
        clauses=[f['claim'].strip().rstrip('。；;') for f in entry.get('claims',[]) if f['claim'].strip()]
        assessment=entry.get('assessment','').strip()
        point['explanation']='；'.join(clauses)+('。' if clauses else '')+assessment
    return result


def point_evidence_roles(analysis,grounding):
    """Expansion is clickable context, not an assertion made in point prose."""
    entries={g['pointId']:g for g in grounding};result=[]
    for point in analysis.get('matches',[]):
        entry=entries.get(point['id'],{})
        stated_blocks={r['blockId'] for f in entry.get('claims',[]) for r in f['evidence']}
        result.append({'pointId':point['id'],'statedFacts':deepcopy(entry.get('claims',[])),
                       'assessment':entry.get('assessment',''),
                       'supplementaryEvidence':[deepcopy(r) for r in point['evidence'] if r['blockId'] not in stated_blocks]})
    return result


def normalize_evidence(text):
    return ' '.join(unicodedata.normalize('NFKC',text).split())


def validate_point_grounding(analysis, grounding, require_facts=False):
    points={p['id']:p for p in analysis.get('matches',[])}
    ids=[g['pointId'] for g in grounding];errors=[]
    if set(ids)!=set(points) or len(ids)!=len(set(ids)):
        errors.append('Provide exactly one explicit pointGrounding entry for every match point')
    for entry in grounding:
        point=points.get(entry['pointId'])
        if not point:continue
        if not entry['scopeReason'].strip():errors.append('Capability scope requires a reason, including uncertain/empty scope')
        if len(entry['capabilities'])>1:
            errors.append('Choose only the primary capability; implementation tools must not expand the source scope')
        if entry.get('focus')=='capability' and not entry['capabilities']:
            errors.append('Capability focus requires a nonempty controlled capability; use workflow/other/uncertain otherwise')
        if entry.get('focus','capability')!='capability' and entry['capabilities']:
            errors.append('workflow/other/uncertain focus requires empty capabilities; do not force general problem solving into data_analysis')
        if require_facts and not entry['claims']:
            errors.append('Each positive match requires at least one source-backed candidate fact')
        for fact in entry['claims']:
            if not fact['claim'].strip():errors.append('Grounding fact must be nonempty')
            if not fact['evidence']:errors.append('Every concrete point claim needs point-local source evidence')
            for ref in fact['evidence']:
                if not any(r['blockId']==ref['blockId'] and normalize_evidence(ref['quote']) in normalize_evidence(r['quote']) for r in point['evidence']):
                    errors.append('Concrete claim evidence must be quoted in this same point, not only elsewhere in the assessment')
    return errors


def exclusion_pairs(analysis, exclusions):
    points={p['id']:p for p in analysis.get('matches',[])}
    return {(e['pointId'],e['blockId']) for e in exclusions
            if e['pointId'] in points and e.get('scope') == point_scope(points[e['pointId']])}


def point_scope(point):
    return {'title':point['title'],'criterionIds':sorted(point['criterionIds'])}


def validate_source_exclusions(exclusions, analysis, index, grounding=None):
    points={p['id']:p for p in analysis.get('matches',[])}
    accepted=[];errors=[]
    for item in exclusions:
        point=points.get(item['pointId'])
        eligible={s['blockId'] for key in (point_capabilities(point,grounding) if point else []) for s in index[key]}
        if (not point or item['blockId'] not in eligible or not item['reason'].strip()
            or item['blockId'] not in {r['blockId'] for r in point['evidence']}):
            errors.append('Source exclusion requires a current match point, cited eligible block and concrete reason')
        else:accepted.append({**item,'scope':point_scope(point)})
    return accepted,errors


def exclusion_review_context(analysis, exclusions, index, grounding=None):
    """Provisional exemptions must be re-judged against this exact review snapshot."""
    active=exclusion_pairs(analysis,exclusions)
    points={p['id']:p for p in analysis.get('matches',[])}
    sources={s['blockId']:s for key in TITLE_CAPABILITIES for s in index[key]}
    pending={}
    for exclusion in exclusions:
        pair=(exclusion['pointId'],exclusion['blockId'])
        if pair not in active:continue
        point=points[pair[0]]
        snapshot={'point':point,'observations':[o for o in analysis.get('observations',[]) if o['criterionId'] in point['criterionIds']],
                  'excludedSource':sources[pair[1]],'pointGrounding':next((g for g in (grounding or []) if g['pointId']==point['id']),None)}
        token=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        pending[pair]={**deepcopy(exclusion),'scopeToken':token,'currentScope':deepcopy(snapshot)}
    return list(pending.values())


def confirm_source_exclusions(pending, decisions):
    expected={(e['pointId'],e['blockId']):e for e in pending}
    grouped={};errors=[];retained=[]
    for decision in decisions:
        pair=(decision['pointId'],decision['blockId'])
        grouped.setdefault(pair,[]).append(decision)
        if pair not in expected:errors.append('Exclusion decision references no pending current-scope exclusion')
    for pair,item in expected.items():
        entries=grouped.get(pair,[])
        valid=(len(entries)==1 and entries[0]['scopeToken']==item['scopeToken'] and bool(entries[0]['reason'].strip()))
        stored={k:v for k,v in item.items() if k not in ('scopeToken','currentScope')}
        if not valid:
            errors.append('Every pending source exclusion needs one explicit decision and reason for the current scope token')
            retained.append(stored)  # provisional only; missing confirmation blocks publication
        elif entries[0]['retain']:
            retained.append({**stored,'lastConfirmation':deepcopy(entries[0])})
        else:
            errors.append('Reviewer revoked a source exclusion; restore its source and review the repaired analysis')
    return retained,errors


def identity_free_summary(analysis, standards):
    """Compose the reviewed card from concrete point labels, never model identity narration."""
    matches=list(dict.fromkeys(p['title'].strip() for p in analysis.get('matches',[]) if p['title'].strip()))
    concerns=list(dict.fromkeys(p['title'].strip() for p in analysis.get('concerns',[]) if p['title'].strip()))
    parts=[]
    if matches:parts.append('相关证据：'+'、'.join(matches[:3]))
    if concerns:parts.append('需核实：'+concerns[0])
    if not parts:return '尚无可展示的具体匹配依据，需补充相关经历证据。'
    return '；'.join(parts)


def supplement_capability_sources(analysis,index,exclusions=(),grounding=None):
    """Add exact activity sources before review; never change coverage/grade/prose claims."""
    result=deepcopy(analysis);changes=[]
    excluded=exclusion_pairs(result,exclusions)
    for point in result.get('matches',[]):
        keys=point_capabilities(point,grounding)
        source_map={source['blockId']:source for key in keys for source in index[key]}
        point['evidence']=[r for r in point['evidence'] if (point['id'],r['blockId']) not in excluded]
        for ref in point['evidence']:
            source=source_map.get(ref['blockId'])
            if source:ref['explanation']=scope_explanation(source)
        refs={ref['blockId'] for ref in point['evidence']}
        for key in keys:
            for source in index[key]:
                if source['blockId'] in refs or (point['id'],source['blockId']) in excluded:continue
                names='、'.join(o['organization'] for o in source['owners']) or '经历归属待确认'
                point['evidence'].append({'blockId':source['blockId'],'quote':source['quote'],'explanation':scope_explanation(source)})
                refs.add(source['blockId']);changes.append({'pointId':point['id'],'capability':key,'blockId':source['blockId'],'owners':source['owners']})
    return result,changes


def validate_capability_coverage(analysis,index,exclusions=(),grounding=None):
    errors=[]
    excluded=exclusion_pairs(analysis,exclusions)
    for point in analysis.get('matches',[]):
        actual={ref['blockId'] for ref in point['evidence']}
        for key in point_capabilities(point,grounding):
            missing={source['blockId'] for source in index[key] if (point['id'],source['blockId']) not in excluded}-actual
            if missing:errors.append(f"Capability {key} in point {point['id']} omits {len(missing)} retrieved activity source(s)")
    return errors


ANGLE_LABELS={'contribution':'个人贡献','validation':'验证方法','limitations':'失败与边界'}
ANGLE_PATTERNS={
 'contribution':r'亲自|个人|负责|贡献|产物|实现部分|personally|contribut|responsib|your own|which part',
 'validation':r'验证|评估|核验|测试|指标|对照|validat|evaluat|test|metric|measur',
 'limitations':r'失败|边界|限制|不足|未达|未成功|failure|failed|limit|boundar|unsuccess|did not',
}


def reading_order(analysis,standards):
    """Stable confirmed-priority ordering; never alter coverage or interview order."""
    result=deepcopy(analysis)
    criteria={c['id']:c for c in standards['criteria']}
    def priority(point):
        return min(((criteria[c]['group']!='must',criteria[c]['priority']) for c in point['criterionIds'] if c in criteria),default=(True,float('inf')))
    for field in ('matches','concerns'):result[field]=sorted(result[field],key=priority)
    return result


def rebind_repaired_verification_targets(targets,analysis,previous_analysis,standards):
    """Repair only a dangling unanchored link after a match loses its criterion.

    No criterion, goal, evidence or analysis content is changed. A unique existing
    concern supplies the replacement link; normal validation AND semantic review
    still decide whether the resulting target is acceptable.
    """
    result=deepcopy(targets);changes=[]
    if not previous_analysis:return result,changes
    prior={p['id']:p for p in previous_analysis.get('matches',[])}
    current={p['id']:p for p in analysis.get('matches',[])}
    confirmed={c['id'] for c in standards['criteria']}
    all_points=analysis.get('matches',[])+analysis.get('concerns',[])
    if len({p['id'] for p in all_points})!=len(all_points):return result,changes
    for target in result:
        point_id=target['pointId'];criterion=target['criterionId']
        if (target['experienceId'] is not None or target['evidence'] or criterion not in confirmed
            or point_id not in prior or point_id not in current
            or criterion not in prior[point_id]['criterionIds']
            or criterion in current[point_id]['criterionIds']):continue
        candidates=[p for p in analysis.get('concerns',[]) if criterion in p['criterionIds']]
        if len(candidates)!=1:continue
        target['pointId']=candidates[0]['id']
        changes.append({'targetId':target['id'],'criterionId':criterion,
                        'previousPointId':point_id,'pointId':target['pointId'],
                        'reason':'Removed match criterion has one existing concern; unanchored goal and evidence unchanged.'})
    return result,changes


def validate_verification_targets(targets,analysis,standards,profile):
    """Check IDs and exact source ownership; semantic choice belongs to review."""
    if len(targets)!=3:return ['Match must propose three reviewed single-purpose verification targets']
    errors=[];criteria={c['id'] for c in standards['criteria']}
    points={p['id']:p for p in analysis['matches']+analysis['concerns']}
    owners=source_owners(profile);experiences={e['id'] for e in profile['experiences']}
    if len({t['id'] for t in targets})!=3 or len({t['singleGoal'].strip() for t in targets})!=3:
        errors.append('Verification targets need distinct IDs and goals')
    for t in targets:
        point=points.get(t['pointId'])
        if t['criterionId'] not in criteria or not point or t['criterionId'] not in point['criterionIds']:
            errors.append('Verification target must link an existing point and its primary confirmed criterion: '
                          +t['id']+' links '+t['pointId']+'/'+t['criterionId']+'; current point criteria='
                          +','.join(point['criterionIds'] if point else [])
                          +'. Update the target pointId to an existing relevant concern; do not add unsupported criteria to a match.')
        experience=t['experienceId']
        if experience is None:
            if t['evidence']:errors.append('Unanchored verification target must have empty evidence: '+t['id']+'. Remove borrowed CV premises from singleGoal and keep evidence=[]; ask conditionally whether relevant experience exists. Do not add an unrelated experience anchor just to retain those citations.')
        elif experience not in experiences or not t['evidence'] or any(len(owners.get(r['blockId'],[]))!=1 or owners[r['blockId']][0]['id']!=experience for r in t['evidence']):
            errors.append('Verification target evidence must belong to its explicitly selected experience')
    return errors


def interview_plan(analysis,standards,profile,targets=None):
    """Adapt the reviewed plan, never invent targets for legacy saved analyses."""
    if not targets:return []
    criteria={c['id']:c for c in standards['criteria']};points={p['id']:p for p in analysis['matches']+analysis['concerns']}
    experiences={e['id']:e for e in profile['experiences']}
    return [{'slot':i+1,'pointId':t['pointId'],'criterionId':t['criterionId'],
             'criterionDescription':criteria[t['criterionId']]['description'],'primaryGoal':t['singleGoal'],
             'decisionReason':t['decisionReason'],'verificationAngle':'primary','unresolvedPoint':points[t['pointId']]['title'],
             'questionMode':'source_anchored' if t['experienceId'] else 'unanchored',
             'sourceBoundary':'Use only assigned evidence; one missing answer, not the whole criterion.',
             'experience':{k:experiences[t['experienceId']][k] for k in ('id','organization','kind')} if t['experienceId'] else None,
             'allowedEvidence':deepcopy(t['evidence'])} for i,t in enumerate(targets)]


def _fixture_interview_plan(analysis,standards,profile):
    owners=source_owners(profile);criteria={c['id']:c for c in standards['criteria']}
    points=analysis.get('concerns') or analysis.get('matches') or []
    candidates=[]
    for point in points:
        linked=[criteria[c] for c in point['criterionIds'] if c in criteria]
        linked.sort(key=lambda c:(c['group']!='must',c['priority']))
        candidates.extend((point,c) for c in linked)
    if not candidates:return []
    selected=[];used=set()
    for point,criterion in candidates:
        dimension=' '.join(criterion['description'].casefold().split())
        if dimension in used:continue
        selected.append((point,criterion));used.add(dimension)
        if len(selected)==3:break
    cursor=0
    while len(selected)<3:
        selected.append(candidates[cursor%len(candidates)]);cursor+=1
    dimensions=[' '.join(c['description'].casefold().split()) for _,c in selected]
    plans=[];occurrences={}
    for i,(point,criterion) in enumerate(selected):
        dimension=dimensions[i]
        angle='primary'
        if dimensions.count(dimension)>1:
            n=occurrences.get(dimension,0);angle=list(ANGLE_LABELS)[n];occurrences[dimension]=n+1
        experience=next((owners[r['blockId']][0] for r in point['evidence'] if len(owners.get(r['blockId'],[]))==1),None)
        refs=[deepcopy(r) for r in point['evidence'] if
              (not experience and not owners.get(r['blockId'])) or
              (experience and len(owners.get(r['blockId'],[]))==1 and owners[r['blockId']][0]['id']==experience['id'])]
        goal=criterion['description']+(f"（核验角度：{ANGLE_LABELS[angle]}）" if angle!='primary' else '')
        plans.append({'slot':i+1,'pointId':point['id'],'criterionId':criterion['id'],'criterionDescription':criterion['description'],
                      'primaryGoal':goal,'verificationAngle':angle,'unresolvedPoint':point['title'],
                      'otherCriterionGoals':[{'criterionId':c['id'],'description':c['description']} for c in criteria.values() if c['id']!=criterion['id'] and c['description']!=criterion['description']],
                      'goalBoundary':'Ask only for evidence of primaryGoal. Other criteria may provide context, never a second requested answer or purpose.',
                      'questionMode':'source_anchored' if refs else 'unanchored',
                      'sourceBoundary':('Use only assigned source/experience.' if refs else 'No project evidence is assigned. Ask whether relevant experience exists, conditionally; name no employer/project and return evidence=[]. Do not borrow sources from other analysis points.'),
                      'experience':experience,'allowedEvidence':refs})
    return plans


def interview_context(document,profile,analysis,plans):
    """Only planned sources enter generation; full originals still govern validation."""
    blocks={r['blockId'] for p in plans for r in p['allowedEvidence']}
    experiences={p['experience']['id'] for p in plans if p['experience']}
    points={p['pointId'] for p in plans};criteria={p['criterionId'] for p in plans}
    return {'document':{'id':document['id'],'blocks':[deepcopy(b) for b in document['blocks'] if b['id'] in blocks]},
            'profile':{'name':profile['name'],'experiences':[e for e in profile_navigation(profile)['experiences'] if e['id'] in experiences]},
            'analysis':{'matches':[deepcopy(p) for p in analysis['matches'] if p['id'] in points],
                        'concerns':[deepcopy(p) for p in analysis['concerns'] if p['id'] in points],
                        'observations':[deepcopy(o) for o in analysis['observations'] if o['criterionId'] in criteria]}}


def organization_names(organization):
    return [organization]+[part for part in re.findall(r'\(([^()]+)\)',organization) if len(part)>=2]


def validate_interview_focus(questions,plans,profile):
    errors=[]
    if len(plans)!=3 or len(questions)!=3:return ['Interview requires three planned single-objective questions']
    owners=source_owners(profile)
    goals=[str(p.get('primaryGoal','')).strip().casefold() for p in plans]
    if all(goals) and len(set(goals))!=3:errors.append('Interview plans must define three distinct dimensions or explicit verification angles')
    for slot,(question,plan) in enumerate(zip(questions,plans),1):
        start=len(errors)
        if question['pointIds']!=[plan['pointId']] or question['criterionIds']!=[plan['criterionId']]:
            errors.append('Each interview question must follow its one planned point and primary criterion')
        # Punctuation does not count verification objectives. A same-goal example
        # follow-up can use another question mark; reviewed plans govern scope.
        if re.search(r'另外|此外|除此之外|另一个问题|\b(?:also|additionally|separately)\b',question['question'],re.I):
            errors.append('Do not append a separate verification objective to an interview question')
        logistics=re.search(r'到岗|出勤|每周|可用期|现场.{0,8}(?:几|多少|[0-9])天|availability|available to work|start date|(?:on-site|onsite).{0,20}(?:days|per week|weekly)',question['question'],re.I)
        unrelated=re.search(r'培训|隐私|合规|采用|反馈闭环|privacy|compliance|training|adoption',question['question'],re.I)
        if logistics and unrelated:errors.append('Availability must not be bundled with training, adoption or privacy verification')
        allowed={r['blockId'] for r in plan['allowedEvidence']}
        if any(r['blockId'] not in allowed for r in question['evidence']):
            errors.append('Interview cites evidence outside its assigned project/source scope')
        experience=plan['experience']
        angle=plan.get('verificationAngle','primary')
        if angle!='primary' and not re.search(ANGLE_PATTERNS[angle],question['question'],re.I):
            errors.append('Question must address its distinct planned verification angle, not paraphrase another goal')
        for other in profile.get('experiences',[]):
            if experience and (other['id']==experience['id'] or other['organization'].casefold()==experience['organization'].casefold()):continue
            for name in organization_names(other['organization']):
                # Shared aliases cannot distinguish two experiences; reject conservatively.
                pattern=re.escape(name) if re.search(r'[\u4e00-\u9fff]',name) else r'(?<!\w)'+re.escape(name)+r'(?!\w)'
                if re.search(pattern,question['question'],re.I):
                    errors.append('Question text names another known experience organization outside the assigned scope')
                    break
        if experience:
            if not question['evidence']:errors.append('Project-specific question must cite its assigned source')
            if not any(name.casefold() in question['question'].casefold() for name in organization_names(experience['organization'])):
                errors.append('Project-specific question must explicitly name its assigned organization or exact source alias')
        cited_owners={o['id'] for r in question['evidence'] for o in owners.get(r['blockId'],[])}
        if len(cited_owners)>1:errors.append('One interview question must not merge evidence from different experiences')
        errors[start:]=[f"Question {question.get('id',slot)} (slot {slot}, point {plan['pointId']}, criterion {plan['criterionId']}): {error}" for error in errors[start:]]
    return list(dict.fromkeys(errors))

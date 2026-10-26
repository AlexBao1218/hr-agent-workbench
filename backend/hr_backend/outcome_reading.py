"""Reviewed candidate reading recipe; never changes evidence coverage or ranking."""
from copy import deepcopy
from .evidence import source_owners, reading_order

READING_VERSION = 'candidate-outcome-v2'


def objective_errors(text,objective):
    """Detect an appended independent validation ask, not incidental technical words."""
    import re
    if objective in ('method','contribution'):
        validation=re.search(r'如何验证|怎样验证|如何评估|效果.{0,5}(?:如何|怎样)|(?:什么|哪些)评估指标|how (?:did|do|would) you (?:validat|evaluat)',text,re.I)
        if validation:
            prefix=text[:validation.start()]
            different_ask=re.search(r'如何(?:选择|编排|构建|设计)|怎样(?:选择|编排|构建|设计)|负责了?哪|实现了?哪|贡献是',prefix)
            if different_ask and re.search(r'[，,；;。？?]|以及|并且|又',prefix[different_ask.end():]):
                return ['One '+objective+' goal cannot append a separate effect-validation question; keep one answer dimension.']
    return []


def _key(value):
    return ''.join(value.split()).casefold().rstrip('。？?')


def _anchor(fact, owners):
    refs=fact.get('evidence',[])
    ids={o['id'] for ref in refs for o in owners.get(ref['blockId'],[])}
    if refs and len(ids)==1 and all(len(owners.get(r['blockId'],[]))==1 for r in refs):
        return next(iter(ids))
    return None


def _item(rows, reference, section):
    row=rows.get(reference['criterionId'])
    if row is None or not isinstance(reference['index'],int) or reference['index']<0:return None
    values=row['unknowns' if section=='unknown' else 'evidence']
    return values[reference['index']] if reference['index']<len(values) else None


def project_reading(data,analysis,grounding,standards,profile):
    """Validate recipe indices/ownership; semantic wording is checked by independent review."""
    reading=data['reading'];rows={r['criterionId']:r for r in data['rows']}
    criteria={c['id']:c for c in standards['criteria']};owners=source_owners(profile);errors=[]
    visible={point['id'] for point in analysis['matches']}
    for row in data['rows']:
        cid=row['criterionId'];criterion=criteria[cid]
        if 'match-'+cid in visible:
            title=row.get('conclusion')
            if not title or _key(title)==_key(criterion['title']):
                errors.append('rows/'+cid+'/conclusion must state this candidate\'s evidenced capability, not copy the JD heading.')
        for index,gap in enumerate(row['unknowns']):
            title=gap.get('conclusion')
            if not title:
                errors.append('rows/'+cid+'/unknowns/'+str(index)+'/conclusion must name the actual missing answer.')
    strength=reading['strength'].strip().rstrip('。；，,')
    gap=reading['gap'].strip().rstrip('。；，,') if reading.get('gap') else None
    summary=strength+('，但'+gap if gap else '')+'。'
    if len(summary)>46:errors.append('reading strength + ，但 + gap + 。 must be <=46 characters; shorten wording without losing the advantage or uncertainty.')
    for reference in reading['strengthSources']:
        item=_item(rows,reference,'evidence')
        if not item or item['state']=='contradiction':errors.append('reading.strengthSources must reference actual non-conflicting row evidence.')
    if bool(gap)!=(reading.get('gapSource') is not None):errors.append('reading.gapSource must be null iff gap is null.')
    if reading.get('gapSource') and not _item(rows,reading['gapSource'],'unknown'):
        errors.append('reading.gapSource must reference an actual row unknown; never manufacture a missing capability.')
    def fact_key(fact):return (_key(fact['claim']),tuple(sorted((r['blockId'],r['quote']) for r in fact['evidence'])))
    available={fact_key(fact) for row in data['rows'] for fact in row['evidence'] if fact['state']!='contradiction' and _anchor(fact,owners)}
    choices=[];selected=[];anchors=set();seen=set();chosen_facts=set()
    for slot,choice in enumerate(reading['interviewChoices']):
        section=choice['section'];item=_item(rows,choice,section)
        if not item:
            errors.append('reading.interviewChoices/'+str(slot)+' references a missing row item.');continue
        cid=choice['criterionId']
        objective=choice.get('objective')
        if not objective:errors.append('Each interview choice needs one explicit objective: existence, contribution, method, validation or limitations.')
        if section=='unknown':
            if choice.get('singleGoal') is not None or choice.get('conclusion') is not None:
                errors.append('Unknown interview choices reuse the row singleGoal/conclusion; set both choice fields null.')
            goal=item['singleGoal'];title=item.get('conclusion');eid=item['experienceId'];refs=item['evidence']
        else:
            goal=choice.get('singleGoal');title=choice.get('conclusion');eid=_anchor(item,owners);refs=item['evidence']
            if item['state']=='contradiction' or not eid:
                errors.append('Evidence interview choices need a source-backed activity with one exact owner. Context may anchor a question but never becomes positive coverage.')
            key=fact_key(item)
            if key in chosen_facts:errors.append('Choose different evidenced activities instead of selecting the same fact twice under different goals.')
            chosen_facts.add(key)
            if not goal or not title:errors.append('Evidence interview choices need one missing answer and its short verification conclusion.')
        if not goal or not title:continue
        errors+=objective_errors(goal,objective)
        if not eid and objective!='existence':errors.append('Unanchored choices must conditionally verify existence, not assume prior work.')
        if _key(goal) in seen:errors.append('Interview choices must verify three different answers, not rephrase the same goal.')
        seen.add(_key(goal))
        if eid:anchors.add((eid,fact_key(item) if section=='evidence' else (choice['criterionId'],choice['index'])))
        choices.append((choice,item,goal,title,eid,refs))
        selected.append({'criterionId':cid,'section':section,'index':choice['index'],'singleGoal':goal,'experienceId':eid,'decisionReason':choice['decisionReason']})
    required=min(2,len(available))
    if len(anchors)<required:
        errors.append('Select '+str(required)+' source-specific goals about different real activities instead of three generic JD existence checks. Context may supply a real activity for a question without supporting a qualification. Preserve actual owners and sources; never invent HR work.')
    if len(choices)!=3:errors.append('Provide exactly three valid candidate-specific interview choices.')
    if errors:return analysis,grounding,[],selected,list(dict.fromkeys(errors))
    concerns={};targets=[]
    def concern(cid,index):
        key=(cid,index)
        if key not in concerns:
            row=rows[cid];unknown=row['unknowns'][index]
            concerns[key]={'id':'unknown-'+cid+'-'+str(index),'title':unknown['conclusion'],
                'criterionIds':[cid],'jdRequirement':criteria[cid]['description'],
                'explanation':'待确认：'+unknown['singleGoal'],'evidence':deepcopy(unknown['evidence']),
                'kind':'verify'}
        return concerns[key]
    if reading.get('gapSource'):
        reference=reading['gapSource'];concern(reference['criterionId'],reference['index'])
    for slot,(choice,item,goal,title,eid,refs) in enumerate(choices):
        cid=choice['criterionId']
        if choice['section']=='unknown':point=concern(cid,choice['index'])
        else:
            point=next((p for p in analysis['matches'] if cid in p['criterionIds']),None)
            if point is None:
                key=('evidence',cid,choice['index'])
                point={'id':'verify-'+cid+'-'+str(choice['index']),'title':title,'criterionIds':[cid],
                    'jdRequirement':criteria[cid]['description'],'explanation':'待确认：'+goal,
                    'evidence':deepcopy(refs),'kind':'verify'}
                concerns[key]=point
        targets.append({'id':'target-'+str(slot+1),'pointId':point['id'],'criterionId':cid,
            'singleGoal':goal,'decisionReason':choice['decisionReason'],'experienceId':eid,'evidence':deepcopy(refs),'objective':choice['objective']})
    ordered=sorted(data['rows'],key=lambda r:(criteria[r['criterionId']]['group']!='must',criteria[r['criterionId']]['priority']))
    for row in ordered:
        if len(concerns)>=4:break
        if row['coverage']!='supported' and row['unknowns']:
            concern(row['criterionId'],0)
    analysis['concerns']=list(concerns.values())[:4]
    analysis['summary']=summary;analysis['_readingVersion']=READING_VERSION
    analysis=reading_order(analysis,standards)
    analysis['keyUnknown']=gap
    return analysis,grounding,targets,selected,[]

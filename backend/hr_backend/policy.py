"""Deterministic advisory policy. Unknown evidence is not a factual contradiction."""
import os
POLICY_VERSION = 'coverage-v2'
VALUES = {'supported': 1.0, 'partial': 0.5, 'unknown': 0.0, 'contradiction': -1.0}


def grade_reason(criteria, observations, reviewed=True):
    by_id = {o['criterionId']: o for o in observations}
    if set(by_id) != {c['id'] for c in criteria} or len(by_id) != len(observations):
        raise ValueError('Each confirmed criterion requires exactly one observation')
    if any(o['coverage'] not in VALUES for o in observations):raise ValueError('Invalid coverage')
    must=sorted((c for c in criteria if c['group']=='must'),key=lambda c:c['priority'])
    core=must[:2]  # HR-confirmed priority order; no name, school, or inferred skill classifier.
    supported=[c for c in must if by_id[c['id']]['coverage']=='supported']
    evidenced=[c for c in must if by_id[c['id']]['coverage'] in ('supported','partial')]
    conflicts=[c for c in must if by_id[c['id']]['coverage']=='contradiction']
    core_evidence=[c for c in core if by_id[c['id']]['coverage'] in ('supported','partial')]
    threshold=float(os.environ.get('HR_A_SUPPORTED_RATIO','0.75'))
    if not 0 <= threshold <= 1:raise ValueError('HR_A_SUPPORTED_RATIO must be between 0 and 1')
    def reason(grade,code,label,text,items):
        return grade,dict(code=code,label=label,explanation=text,criterionIds=[c['id'] for c in items])
    if not reviewed or not must:
        return reason('pending','pending_review','待复核','尚无可用的已复核必需条件判断，暂不分级。',[])
    if conflicts:
        return reason('C','explicit_mismatch','明确不符合','已有证据与必需条件不符：'+'、'.join(c['title'] for c in conflicts)+'。建议暂不推进；仍由 HR 决定。',conflicts)
    if by_id[must[0]['id']]['coverage']=='supported' and len(supported)/len(must)>=threshold:
        return reason('A','strong_match','匹配较充分',f'{len(must)} 项必需条件中 {len(supported)} 项完全支持，且最高优先级项完全支持，建议优先面试。',supported)
    if not core_evidence or len(evidenced)*2<len(must):
        causes=[]
        if not core_evidence:causes.append(f'前 {len(core)} 项核心要求均未体现支持证据')
        if len(evidenced)*2<len(must):causes.append(f'{len(must)} 项必需条件中仅 {len(evidenced)} 项有完全或部分支持证据，未达一半')
        missing=[c for c in must if by_id[c['id']]['coverage']=='unknown']
        return reason('C','insufficient_evidence','证据不足','；'.join(causes)+'。基于现有材料建议暂不推进，不代表已证明候选人不具备能力；补充证据后可重评。',missing)
    return reason('B','partial_match','有匹配基础',f'前 {len(core)} 项核心要求中 {len(core_evidence)} 项有支持证据，全部 {len(must)} 项必需条件中 {len(evidenced)} 项有完全或部分支持；仍有重要部分待核实，可备选。',evidenced)


def rank(criteria, observations, reviewed=True):
    grade,_=grade_reason(criteria,observations,reviewed)
    by_id={o['criterionId']:o for o in observations}
    def weighted(group):
        group=sorted(group,key=lambda c:c['priority']);n=len(group)
        return sum((n-i)*VALUES[by_id[c['id']]['coverage']] for i,c in enumerate(group))/max(1,n*(n+1)/2)
    return grade,round(weighted([c for c in criteria if c['group']=='must'])*100+weighted([c for c in criteria if c['group']=='bonus'])*10,6)


def reconciled_grade(criteria,observations,reviewed,recommendation=None):
    """Grade reviewed observations by policy independently of model advice."""
    grade,value=rank(criteria,observations,reviewed)
    _,reason=grade_reason(criteria,observations,reviewed)
    return grade,value,reason

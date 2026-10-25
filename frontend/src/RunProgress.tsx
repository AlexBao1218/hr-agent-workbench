import { useEffect, useId, useRef, useState } from "react";
import type { CandidateCard, Run, RunStep } from '../../contracts/api';

export function runErrorMessage(error: string) {
  if (/\bbudget_exhausted\b/.test(error)) return '已达到本地调用次数上限，本次分析未完成。';
  return /\binvalid_interview_evidence\b/.test(error) ? '面试问题的引用检查未通过' : error;
}

type Role = RunStep['role'];
type State = RunStep['status'] | 'stopped';
const labels: Record<Role, string> = { jd: 'JD理解', cv: '简历解析', match: '岗位匹配', review: '结果复核', interview: '面试问题', rank: '快速重排' };
const states: Record<State, string> = { queued: '等待', running: '进行中', succeeded: '已完成', failed: '失败', skipped: '复用/跳过', stopped: '已停止' };
const assessment: Role[] = ['jd', 'cv', 'match', 'review', 'interview'];

// Array order is the backend's event order. Interview attempts restart at 1,
// so downstream freshness must also compare event positions, not just attempts.
export function progressPaths(run: Run) {
  const groups = new Map<string, { candidateId: string | null; roles: Role[]; events: { step: RunStep; index: number }[] }>();
  run.steps.forEach((step, index) => {
    const kind = step.role === 'rank' ? 'rank' : step.candidateId || (step.role !== 'jd' && assessment.includes(step.role)) ? 'assessment' : 'jd';
    const key = `${kind}:${step.candidateId || ''}`;
    if (!groups.has(key)) groups.set(key, { candidateId: step.candidateId, roles: kind === 'assessment' ? assessment : [kind as Role], events: [] });
    groups.get(key)!.events.push({ step, index });
  });
  const stopped = !!run.error || run.status === 'failed' || run.status === 'cancelled';
  const active = !stopped && (run.status === 'queued' || run.status === 'running');
  const lastEvent = run.steps.map((step, index) => ({ step, index })).filter(e => e.step.status !== 'queued').at(-1);
  const fallbackFailure = stopped && run.status !== 'cancelled' && lastEvent?.step.status !== 'failed' ? lastEvent : undefined;
  return [...groups.entries()].map(([key, group]) => {
    let upstream = -1;
    let matchAttempt = 0;
    const nodes = group.roles.map(role => {
      const latest = group.events.filter(e => e.step.role === role).sort((a, b) => b.step.attempt - a.step.attempt || b.index - a.index)[0];
      const stale = !!latest && (latest.index < upstream || role === 'review' && latest.step.attempt < matchAttempt);
      if (latest && !stale) upstream = Math.max(upstream, latest.index);
      if (role === 'match' && latest) matchAttempt = latest.step.attempt;
      const step = stale ? undefined : latest?.step;
      let status: State = step?.status || 'queued';
      if (status === 'running' && !active) status = 'stopped';
      const terminalFailure = !!latest && latest.index === fallbackFailure?.index;
      if (terminalFailure) status = 'failed';
      return { role, status, terminalFailure, attempt: step?.attempt || null, stale, summary: step?.summary || '' };
    });
    return { key, candidateId: group.candidateId, nodes };
  });
}

export function RunProgress({ run, candidates }: { run: Run; candidates: CandidateCard[] }) {
  const paths = progressPaths(run);
  const hasFailed = paths.some(path => path.nodes.some(node => node.status === 'failed'));
  const incomplete = !paths.length || paths.some(path => path.nodes.some(node => node.status !== 'succeeded' && node.status !== 'skipped'));
  const overall = run.error || hasFailed ? '需要处理' : run.status === 'succeeded' && incomplete ? '已结束 · 步骤记录不完整' : { queued: '等待开始', running: '处理中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }[run.status];
  const nodeLabel = (role: Role, candidateId: string | null) => role === 'jd' && candidateId ? '岗位标准' : labels[role];
  const announcement = [overall, ...paths.flatMap(path => path.nodes.filter(n => n.status === 'running' || n.status === 'failed').map(n => `${nodeLabel(n.role, path.candidateId)}${states[n.status]}`))].join(' · ');
  return <details className="status-bar run-progress" aria-label="处理进度" open={run.status !== 'succeeded' || !!run.error || hasFailed || incomplete}>
    <summary className="run-progress-heading"><strong>处理进度 · {overall}</strong>{run.mode === 'fixture' && <span>本地测试</span>}</summary>
    <span className="run-announcement" role="status" aria-live="polite" aria-atomic="true">{announcement}</span>
    {!paths.length && <p className="run-waiting">{run.status === 'queued' || run.status === 'running' ? '等待开始' : '暂无步骤记录'}</p>}
    {paths.map(path => <div className="run-path" key={path.key}>
      {path.candidateId && <p className="run-candidate">{candidates.find(c => c.id === path.candidateId)?.name || '候选人处理'}</p>}
      <ol className="run-nodes" aria-label={path.candidateId ? '简历评估步骤' : '任务步骤'}>
        {path.nodes.map(node => <li key={node.role} className={`run-node is-${node.status}`} data-role={node.role} data-state={node.status} aria-current={node.status === 'running' ? 'step' : undefined}>
          <span className="run-dot" aria-hidden="true">{node.status === 'succeeded' ? '✓' : node.status === 'failed' ? '!' : node.status === 'skipped' ? '↪' : node.status === 'stopped' ? '–' : ''}</span>
          <span className="run-node-copy"><strong>{nodeLabel(node.role, path.candidateId)}</strong><small>{node.terminalFailure ? '结果未通过' : node.role === 'jd' && path.candidateId && node.status === 'succeeded' ? '已确认' : node.stale ? '等待重新处理' : states[node.status]}{node.attempt && node.attempt > 1 ? ` · 第${node.attempt}次` : ''}</small></span>
        </li>)}
      </ol>
    </div>)}
    {run.error && <p className="run-error">{runErrorMessage(run.error)}</p>}
    <details className="run-log"><summary>查看运行记录 · {run.steps.length} 条</summary>{run.error && <p className="run-error-raw">{run.error}</p>}{run.steps.map((step, index) => <p key={`${step.id}-${index}`}>{labels[step.role]} · {states[step.status]} · 第{step.attempt}次{step.candidateId ? ` · ${candidates.find(c => c.id === step.candidateId)?.name || step.candidateId}` : ''}<br />{step.summary}</p>)}</details>
  </details>;
}


export function hasCandidateProgress(candidate: CandidateCard, run: Run | null) {
  return !!run?.steps.some(step => step.candidateId === candidate.id && step.role !== 'rank') || ['uploaded', 'parsing', 'analyzing', 'failed', 'needs_attention'].includes(candidate.stage);
}

export function CandidateRunProgress({ candidate, run, placement = 'card' }: { candidate: CandidateCard; run: Run | null; placement?: 'card' | 'detail' }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const paths = run ? progressPaths(run).filter(path => path.candidateId === candidate.id && path.nodes.some(node => node.role !== 'rank')) : [];
  const steps = run?.steps.filter(step => step.candidateId === candidate.id && step.role !== 'rank') || [];
  const nodes = paths.flatMap(path => path.nodes);
  const candidateFailed = candidate.stage === 'failed' || candidate.stage === 'needs_attention';
  const failed = candidateFailed || nodes.some(node => node.status === 'failed');
  const error = candidate.error || (failed ? run?.error : null);
  const processing = ['uploaded', 'parsing', 'analyzing'].includes(candidate.stage);
  const stepsComplete = nodes.length > 0 && nodes.every(node => node.status === 'succeeded' || node.status === 'skipped');
  const complete = !failed && !error && stepsComplete && candidate.stage === 'ready' && candidate.assessmentStatus !== 'stale';
  const running = nodes.find(node => node.status === 'running');
  const stopped = nodes.some(node => node.status === 'stopped') || (steps.length > 0 && run?.status === 'cancelled');
  const label = failed || error ? '分析未完成 · 查看进度' : complete ? '分析已完成' : stopped ? '分析已停止 · 查看进度' : running ? `${running.role === 'jd' ? '岗位标准' : labels[running.role]}中` : stepsComplete ? '结果确认中 · 查看进度' : '等待开始 · 查看进度';
  useEffect(() => { setOpen(false); }, [run?.id, complete]);
  if (!steps.length && !processing && !candidateFailed) return null;
  const close = () => { setOpen(false); trigger.current?.focus(); };
  return <div className={`candidate-progress progress-${placement}`} data-candidate-id={candidate.id} onClick={event => event.stopPropagation()} onKeyDown={event => { event.stopPropagation(); if (event.key === 'Escape' && open) { event.preventDefault(); close(); } }}>
    <button ref={trigger} className={`candidate-progress-trigger ${failed || error ? 'failed' : ''}`} aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(value => !value)}>
      {running && !failed && !error && !stopped ? <span className="candidate-progress-spinner" aria-hidden="true" /> : <span aria-hidden="true">{complete ? '✓' : failed || error ? '!' : '·'}</span>}
      <span>{label}</span><span aria-hidden="true">{open ? '▴' : '▾'}</span>
    </button>
    <span className="run-announcement" role="status" aria-live="polite">{candidate.name} · {label}</span>
    {open && <div id={panelId} className="candidate-progress-panel" role="region" aria-label={`${candidate.name}的处理进度`}>
      <div className="candidate-progress-panel-heading"><strong>处理进度</strong><button aria-label="收起处理进度" onClick={close}>收起 ×</button></div>
      {!nodes.length && <p className="run-waiting">等待开始，尚无步骤记录。</p>}
      {!!nodes.length && <ol className="run-nodes" aria-label="简历评估步骤">{nodes.map(node => <li key={node.role} className={`run-node is-${node.status}`} data-role={node.role} data-state={node.status} aria-current={node.status === 'running' ? 'step' : undefined}>
        <span className="run-dot" aria-hidden="true">{node.status === 'succeeded' ? '✓' : node.status === 'failed' ? '!' : node.status === 'skipped' ? '↪' : node.status === 'stopped' ? '–' : ''}</span>
        <span className="run-node-copy"><strong>{node.role === 'jd' ? '岗位标准' : labels[node.role]}</strong><small>{node.terminalFailure ? '结果未通过' : node.role === 'jd' && node.status === 'succeeded' ? '已确认' : node.stale ? '等待重新处理' : states[node.status]}{node.attempt && node.attempt > 1 ? ` · 第${node.attempt}次` : ''}</small></span>
      </li>)}</ol>}
      {error && <p className="run-error">{runErrorMessage(error)}</p>}
      <details className="run-log"><summary>查看运行记录 · {steps.length} 条</summary>{error && <p className="run-error-raw">{error}</p>}{steps.map((step, index) => <p key={`${step.id}-${index}`}>{step.role === 'jd' ? '岗位标准' : labels[step.role]} · {states[step.status]} · 第{step.attempt}次<br />{step.summary}</p>)}</details>
    </div>}
  </div>;
}

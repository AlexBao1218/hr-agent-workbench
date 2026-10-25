import { useEffect, useId, useRef, useState } from 'react';
import type { HrDecision, HrReview } from '../../contracts/api';
import { api } from './api';
export const hrDecisionLabels: Record<HrDecision,string> = {priority_interview:'优先面试',consider:'保留考虑',not_now:'暂不推进'};
export function HrReviewEditor({candidateId,value,onSaved}:{candidateId:string;value?:HrReview;onSaved:(review:HrReview)=>Promise<void>}) {
  const [open,setOpen]=useState(false);const [decision,setDecision]=useState<HrDecision|null>(null);const [note,setNote]=useState('');const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [status,setStatus]=useState('');
  const root=useRef<HTMLDivElement>(null);const trigger=useRef<HTMLButtonElement>(null);const panel=useId();
  const close=()=>{if(busy)return;setOpen(false);trigger.current?.focus();};
  useEffect(()=>{if(!open)return;const outside=(e:PointerEvent)=>{if(!busy&&!root.current?.contains(e.target as Node))setOpen(false);};document.addEventListener('pointerdown',outside);return()=>document.removeEventListener('pointerdown',outside);},[open,busy]);
  return <div className="hr-review-action" ref={root} onKeyDown={e=>{if(e.key==='Escape'){e.stopPropagation();close();}}}>
    <button className="detail-hr-review" ref={trigger} aria-expanded={open} aria-controls={panel} aria-haspopup="dialog" disabled={busy} onClick={()=>{if(open){close();return;}setDecision(value?.decision||null);setNote(value?.note||'');setError('');setStatus('');setOpen(true);}}>HR 评价：{value?.decision?hrDecisionLabels[value.decision]:'未评价'} ▾</button>
    {open&&<form id={panel} className="hr-review-menu" role="dialog" aria-label="我的 HR 评价" onSubmit={async e=>{e.preventDefault();setBusy(true);setError('');let result:HrReview;try{result=await api<HrReview>(`/candidates/${encodeURIComponent(candidateId)}/hr-review`,'PUT',{decision,note});}catch(e){setError(e instanceof Error?e.message:String(e));setBusy(false);return;}try{await onSaved(result);setStatus('HR 评价已保存。');}catch{setStatus('HR 评价已保存，列表刷新失败，请刷新页面。');}setBusy(false);setOpen(false);trigger.current?.focus();}}>
      <strong>我的评价</strong>
      <fieldset disabled={busy}><legend className="sr-only">选择评价</legend>{(Object.entries(hrDecisionLabels) as [HrDecision,string][]).map(([key,label])=><label className="hr-review-option" key={key}><input type="radio" name={panel} checked={decision===key} onChange={()=>setDecision(key)}/>{label}</label>)}</fieldset>
      <label className="hr-note-label" htmlFor={`${panel}-note`}>备注（可选）</label><textarea id={`${panel}-note`} maxLength={2000} disabled={busy} value={note} onChange={e=>setNote(e.target.value)} placeholder="记录判断理由或待确认事项"/>
      {error&&<p className="hr-review-error" role="alert">{error}</p>}
      <footer><button type="button" disabled={busy} onClick={()=>{setDecision(null);setNote('');setError('');}}>清除评价</button><button type="button" disabled={busy} onClick={close}>取消</button><button className="primary" disabled={busy}>{busy?'保存中…':'保存'}</button></footer>
    </form>}
    {status&&<span className="hr-review-status" role="status">{status}</span>}
  </div>;
}

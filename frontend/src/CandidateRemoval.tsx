import { useEffect, useId, useRef, useState } from 'react';
import type { CandidateCard, CandidateDeletionResult } from '../../contracts/api';
import { api } from './api';

export function DeleteCandidate({ candidate, onDeleted }: { candidate: CandidateCard; onDeleted: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const submitting = useRef(false);
  const titleId = useId();
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    else if (!open && dialog.current?.open) dialog.current.close();
  }, [open]);
  const remove = async () => {
    if (submitting.current) return;
    submitting.current = true; setPending(true); setError('');
    try {
      await api<CandidateDeletionResult>(`/candidates/${encodeURIComponent(candidate.id)}?permanent=true`, 'DELETE');
      await onDeleted();
    } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { submitting.current = false; setPending(false); }
  };
  return <div className="card-delete-action" onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
    <button className="card-delete" title="删除简历" aria-label={`删除 ${candidate.name} 的简历`} disabled={pending} onClick={() => { setError(''); setOpen(true); }}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></svg>
    </button>
    <dialog ref={dialog} className="remove-candidate-dialog" aria-labelledby={titleId} onCancel={event => { if (pending) event.preventDefault(); else setOpen(false); }}>
      <h2 id={titleId}>永久删除这份简历？</h2>
      <p className="remove-candidate-name">{candidate.name}</p>
      <p>将删除原文件、分析结果和 HR 评价，删除后无法恢复。</p>
      {error && <p role="alert" className="remove-error">{error}</p>}
      <footer><button autoFocus disabled={pending} onClick={() => setOpen(false)}>取消</button><button className="danger-action" disabled={pending} onClick={() => void remove()}>{pending ? '正在删除…' : '确认删除'}</button></footer>
    </dialog>
  </div>;
}

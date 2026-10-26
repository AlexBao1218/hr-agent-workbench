import { useEffect, useRef, useState } from 'react';
import type { CandidateCard, RemovedCandidateList, CandidateRemovalResult } from '../../contracts/api';
import { api } from './api';

export function RemoveCandidate({ candidate, disabled, onRemoved }: { candidate: CandidateCard; disabled: boolean; onRemoved: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const submitting = useRef(false);
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    else if (!open && dialog.current?.open) dialog.current.close();
  }, [open]);
  const remove = async () => {
    if (submitting.current) return;
    submitting.current = true; setPending(true); setError('');
    try {
      await api<CandidateRemovalResult>(`/candidates/${encodeURIComponent(candidate.id)}`, 'DELETE');
      setOpen(false);
      await onRemoved();
    } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { submitting.current = false; setPending(false); }
  };
  return <>
    <button className="detail-remove" disabled={disabled || pending} onClick={() => { setError(''); setOpen(true); }}>移除简历</button>
    <dialog ref={dialog} className="remove-candidate-dialog" aria-labelledby="remove-candidate-title" onCancel={event => { if (pending) event.preventDefault(); else setOpen(false); }}>
      <h2 id="remove-candidate-title">移除这份简历？</h2>
      <p className="remove-candidate-name">{candidate.name}</p>
      <p>从候选列表移除，原文件和分析记录会保留，可在“已移除”中恢复。</p>
      {error && <p role="alert" className="remove-error">{error}</p>}
      <footer><button disabled={pending} onClick={() => setOpen(false)}>取消</button><button className="danger-action" disabled={pending} onClick={() => void remove()}>{pending ? '正在移除…' : '确认移除'}</button></footer>
    </dialog>
  </>;
}

export function RemovedCandidates({ onRestored }: { onRestored: () => Promise<void> }) {
  const [items, setItems] = useState<RemovedCandidateList['candidates']>([]);
  const [loaded, setLoaded] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState('');
  const load = async () => {
    try { setItems((await api<RemovedCandidateList>('/removed-candidates')).candidates); setLoaded(true); setError(''); }
    catch (error) { setError(error instanceof Error ? error.message : String(error)); }
  };
  const restore = async (id: string) => {
    if (pending) return;
    setPending(id); setError('');
    try {
      await api(`/candidates/${encodeURIComponent(id)}/restore`, 'POST');
      setItems(current => current.filter(candidate => candidate.id !== id));
      await onRestored();
    } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { setPending(null); }
  };
  return <details className="removed-candidates" onToggle={event => { if (event.currentTarget.open) void load(); }}>
    <summary>已移除{loaded && items.length > 0 ? ` · ${items.length}` : ''}</summary>
    {error && <p role="alert" className="remove-error">{error}<button onClick={() => void load()}>重试</button></p>}
    {!error && !loaded && <p>正在读取…</p>}
    {loaded && !items.length && <p>暂无已移除的简历。</p>}
    {items.map(candidate => <div className="removed-candidate-row" key={candidate.id}><span>{candidate.name}</span><button disabled={pending !== null} onClick={() => void restore(candidate.id)}>{pending === candidate.id ? '正在恢复…' : '恢复'}</button></div>)}
  </details>;
}

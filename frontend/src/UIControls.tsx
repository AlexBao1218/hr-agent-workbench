import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';

export interface FilterSelectProps {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  disabled?: boolean;
  onChange: (value: string) => void;
}

export function FilterSelect({ label, value, options, disabled = false, onChange }: FilterSelectProps) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);
  const [focused, setFocused] = useState(0);
  const selected = options.findIndex(option => option.value === value);
  const search = useRef({ text: '', time: 0 });
  const close = (restore = false) => {
    setOpen(false);
    if (restore) trigger.current?.focus();
  };
  const show = (index = selected >= 0 ? selected : 0) => {
    if (disabled || !options.length) return;
    setFocused(index);
    setOpen(true);
  };
  const choose = (index: number) => {
    const option = options[index];
    if (!option || disabled) return;
    onChange(option.value);
    close(true);
  };
  useEffect(() => {
    if (!open) return;
    list.current?.focus();
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  useEffect(() => {
    if (disabled || !options.length) setOpen(false);
    setFocused(index => Math.min(index, Math.max(0, options.length - 1)));
  }, [disabled, options.length]);
  useEffect(() => {
    if (open) document.getElementById(`${id}-option-${focused}`)?.scrollIntoView({ block: 'nearest' });
  }, [open, focused, id]);
  const keyboard = (event: KeyboardEvent<HTMLElement>) => {
    if (disabled) return;
    if (event.key === 'Tab') {
      if (open) close(true);
      return;
    }
    if (event.key === 'Escape') {
      if (open) { event.preventDefault(); event.stopPropagation(); close(true); }
      return;
    }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(event.key)) {
      event.preventDefault();
      if (!open) { show(event.key === 'End' ? options.length - 1 : event.key === 'Home' ? 0 : selected >= 0 ? selected : event.key === 'ArrowUp' ? options.length - 1 : 0); return; }
      if (event.key === 'Enter' || event.key === ' ') choose(focused);
      else if (event.key === 'Home') setFocused(0);
      else if (event.key === 'End') setFocused(options.length - 1);
      else setFocused(index => (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length);
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now();
      search.current = { text: (now - search.current.time < 700 ? search.current.text : '') + event.key.toLocaleLowerCase(), time: now };
      const index = options.findIndex(option => option.label.toLocaleLowerCase().startsWith(search.current.text));
      if (index >= 0) { event.preventDefault(); open ? setFocused(index) : show(index); }
    }
  };
  return <div className="filter-select" ref={root} onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
  }}>
    <span id={`${id}-label`} className="filter-select-label">{label}</span>
    <button type="button" className="filter-select-trigger" ref={trigger} disabled={disabled || !options.length}
      aria-labelledby={`${id}-label ${id}-value`} aria-haspopup="listbox" aria-expanded={open} aria-controls={`${id}-options`}
      onClick={() => open ? close(true) : show()} onKeyDown={keyboard}>
      <span id={`${id}-value`}>{options[selected]?.label ?? '请选择'}</span><span aria-hidden="true">⌄</span>
    </button>
    {open && <ul id={`${id}-options`} ref={list} className="filter-options" role="listbox" aria-label={label}
      tabIndex={-1} aria-activedescendant={`${id}-option-${focused}`} onKeyDown={keyboard}>
      {options.map((option, index) => <li key={option.value} id={`${id}-option-${index}`} role="option"
        aria-selected={option.value === value} className={`filter-option${option.value === value ? ' selected' : ''}${index === focused ? ' focused' : ''}`}
        onPointerMove={() => setFocused(index)} onMouseDown={event => event.preventDefault()} onClick={() => choose(index)}>
        {option.label}
      </li>)}
    </ul>}
  </div>;
}

export interface UploadDialogProps {
  open: boolean;
  busy: boolean;
  onClose: () => void;
  onConfirm: (files: File[]) => Promise<boolean>;
}
const fileKey = (file: File) => `${file.name}\u0000${file.size}\u0000${file.lastModified}`;

export function UploadDialog({ open, busy, onClose, onConfirm }: UploadDialogProps) {
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const inFlight = useRef(false);
  const dragDepth = useRef(0);
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const locked = busy || submitting;
  useEffect(() => {
    if (open) {
      setFiles([]); setError(''); setDragOver(false); dragDepth.current = 0;
      if (input.current) input.current.value = '';
      if (!dialog.current?.open) dialog.current?.showModal();
    } else if (dialog.current?.open) dialog.current.close();
  }, [open]);
  const addFiles = (incoming: File[]) => {
    if (locked || inFlight.current) return;
    const invalid = incoming.filter(file => !/\.(pdf|docx|png|jpe?g)$/i.test(file.name) || file.size === 0);
    if (invalid.length) {
      setError(`未添加 ${invalid.map(file => file.name).join('、')}：请选择非空的 PDF、DOCX、PNG 或 JPG 文件。`);
    } else setError('');
    const valid = incoming.filter(file => /\.(pdf|docx|png|jpe?g)$/i.test(file.name) && file.size > 0);
    setFiles(current => {
      const merged = new Map(current.map(file => [fileKey(file), file]));
      valid.forEach(file => merged.set(fileKey(file), file));
      return [...merged.values()];
    });
  };
  const confirm = async () => {
    if (locked || inFlight.current || !files.length) return;
    inFlight.current = true; setSubmitting(true); setError('');
    try {
      if (await onConfirm([...files])) onClose();
      else setError('上传未完成，请检查提示后重试。');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '上传失败，请重试。');
    } finally { inFlight.current = false; setSubmitting(false); }
  };
  return <dialog ref={dialog} className="upload-dialog" aria-labelledby={`${id}-heading`} aria-describedby={`${id}-note`}
    onCancel={event => { event.preventDefault(); if (!locked && !inFlight.current) onClose(); }}
    onClose={() => { if (open && !locked && !inFlight.current) onClose(); }}>
    <h2 id={`${id}-heading`} className="upload-dialog-heading">上传简历</h2>
    <p id={`${id}-note`}>支持 PDF、DOCX、PNG、JPG；选好文件后确认上传。</p>
    <div className={`upload-dropzone${dragOver ? ' drag-over' : ''}`} onDragEnter={event => {
      event.preventDefault(); if (!locked) { dragDepth.current += 1; setDragOver(true); }
    }} onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = locked ? 'none' : 'copy'; }}
      onDragLeave={event => { event.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragOver(false); }}
      onDrop={event => { event.preventDefault(); dragDepth.current = 0; setDragOver(false); addFiles(Array.from(event.dataTransfer.files)); }}>
      <p>将简历拖到这里，或</p>
      <button type="button" disabled={locked} onClick={() => input.current?.click()}>选择文件</button>
      <input ref={input} type="file" hidden multiple accept=".pdf,.docx,.png,.jpg,.jpeg" disabled={locked}
        aria-label="选择简历文件" onChange={event => { addFiles(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = ''; }} />
    </div>
    {error && <p role="alert">{error}</p>}
    <ul className="upload-file-list" aria-label="待上传简历">
      {files.map(file => <li className="upload-file-row" key={fileKey(file)}>
        <span>{file.name}</span><button type="button" disabled={locked} aria-label={`移除 ${file.name}`}
          onClick={() => setFiles(current => current.filter(item => fileKey(item) !== fileKey(file)))}>移除</button>
      </li>)}
    </ul>
    <div className="upload-dialog-actions">
      <button type="button" disabled={locked} onClick={onClose}>取消</button>
      <button type="button" disabled={locked || !files.length} onClick={() => void confirm()}>
        {locked ? '正在上传…' : `确认上传 ${files.length} 份`}
      </button>
    </div>
  </dialog>;
}

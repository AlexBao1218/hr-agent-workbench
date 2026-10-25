import { useEffect, useRef, useState } from 'react';

export interface EditableStandard { id: string; title: string; description: string; group?: 'must' | 'bonus'; jdQuote?: string }
export function StandardsList({items,kind,busy,onSave,onDelete,onAdd}:{items:EditableStandard[];kind:'jd'|'hat';busy:boolean;onSave:(item:EditableStandard)=>Promise<void>;onDelete?:(id:string)=>Promise<void>;onAdd?:boolean}) {
  const [filter,setFilter]=useState('all'); const [query,setQuery]=useState('');
  const [editing,setEditing]=useState<EditableStandard|null>(null); const [original,setOriginal]=useState('');
  const [discard,setDiscard]=useState(false);const [saving,setSaving]=useState(false);const [error,setError]=useState('');
  const dialog=useRef<HTMLDialogElement>(null);const trigger=useRef<HTMLButtonElement|null>(null);
  const dirty=!!editing&&JSON.stringify(editing)!==original;
  useEffect(()=>{if(editing&&!dialog.current?.open)dialog.current?.showModal();},[editing]);
  useEffect(()=>{if(!dirty)return;const guard=(e:BeforeUnloadEvent)=>{e.preventDefault();e.returnValue='';};window.addEventListener('beforeunload',guard);return()=>window.removeEventListener('beforeunload',guard);},[dirty]);
  const close=()=>{dialog.current?.close();setEditing(null);setDiscard(false);trigger.current?.focus();};
  const requestClose=()=>{if(saving)return;if(dirty){setDiscard(true);return;}close();};
  const update=(patch:Partial<EditableStandard>)=>{setEditing(x=>x?{...x,...patch}:x);setDiscard(false);};
  const visible=items.filter(x=>(filter==='all'||x.group===filter)&&`${x.title} ${x.description}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <section className="standards-manager" aria-label={kind==='jd'?'岗位标准清单':'共同规则清单'}>
    <div className="standards-list-toolbar"><div className="standards-filters" aria-label="标准筛选">{(kind==='jd'?[['all','全部'],['must','必须项'],['bonus','加分项']]:[['all','全部规则']]).map(([key,label])=><button key={key} aria-pressed={filter===key} onClick={()=>setFilter(key)}>{label}<small>{items.filter(x=>key==='all'||x.group===key).length}</small></button>)}</div><input className="standards-search" aria-label="搜索标准" placeholder={kind==='jd'?'搜索标准':'搜索规则'} value={query} onChange={e=>setQuery(e.target.value)}/></div>
    <div className="standards-list">{visible.map(item=><article className="standard-list-row" key={item.id} data-standard-id={item.id}><span className="standard-number">{items.indexOf(item)+1}</span><div><h2>{item.title||'未命名标准'}</h2><p>{item.description}</p></div><span className={`standard-group ${item.group||''}`}>{kind==='hat'?'共同规则':item.group==='must'?'必须项':'加分项'}</span><button className="standard-edit" disabled={busy} onClick={e=>{trigger.current=e.currentTarget;setEditing({...item});setOriginal(JSON.stringify(item));setError('');setDiscard(false);}}>编辑</button></article>)}{!visible.length&&<p className="empty">没有符合条件的条目</p>}</div>
    <div className="standards-list-bottom"><span>显示 {visible.length} / {items.length} 条</span>{onAdd&&<button disabled={busy} onClick={e=>{trigger.current=e.currentTarget;const item:EditableStandard={id:crypto.randomUUID(),title:"",description:"",group:"bonus",jdQuote:""};setEditing(item);setOriginal(JSON.stringify(item));setError("");setDiscard(false);}}>＋ 添加标准</button>}</div>
    <dialog ref={dialog} className="standard-drawer" aria-labelledby="standard-editor-heading" onCancel={e=>{e.preventDefault();requestClose();}}>
      {editing&&<form onSubmit={async e=>{e.preventDefault();setSaving(true);setError('');try{await onSave({...editing,title:editing.title.trim(),description:editing.description.trim()});if(editing.group!==JSON.parse(original).group)setFilter('all');close();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setSaving(false);}}}>
        <header><h2 id="standard-editor-heading">{kind==='jd'?'编辑岗位标准':'编辑共同规则'}</h2><button type="button" aria-label="关闭编辑" disabled={saving} onClick={requestClose}>×</button></header>
        <div className="standard-drawer-fields"><label>标准名称<input required value={editing.title} onChange={e=>update({title:e.target.value})}/></label>
          {kind==='jd'&&<fieldset><legend>标准类型</legend><div className="standard-radios">{(['must','bonus'] as const).map(group=><label key={group}><input type="radio" name="standard-group" checked={editing.group===group} onChange={()=>update({group})}/>{group==='must'?'必须项':'加分项'}</label>)}</div></fieldset>}
          <label>具体要求<textarea required value={editing.description} onChange={e=>update({description:e.target.value})}/></label>
          {kind==='jd'&&<details><summary>查看对应 JD 原文</summary><p>{editing.jdQuote||'尚无关联原句'}</p></details>}
          {kind==='hat'&&<p className="muted">五项核心约束始终生效；修改说明不会关闭约束。</p>}
          {error&&<p className="notice error" role="alert">{error}</p>}
          {discard&&<div className="standard-discard" role="alert"><p>有未保存的修改，要放弃吗？</p><button type="button" onClick={()=>setDiscard(false)}>继续编辑</button><button type="button" onClick={close}>放弃修改</button></div>}
        </div>
        <footer>{onDelete&&items.some(x=>x.id===editing.id)&&<button type="button" className="standard-delete" disabled={saving} onClick={async()=>{if(!window.confirm('删除此条标准并保存草稿？'))return;setSaving(true);try{await onDelete(editing.id);close();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setSaving(false);}}}>删除此条</button>}<button type="button" disabled={saving} onClick={requestClose}>取消</button><button className="primary" disabled={saving||busy||!editing.title.trim()||!editing.description.trim()}>{saving?'保存中…':'保存修改'}</button></footer>
      </form>}
    </dialog>
  </section>;
}

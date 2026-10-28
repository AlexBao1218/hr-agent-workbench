import React, {
  useEffect,
  useState,
  useCallback,
  useLayoutEffect,
  useRef,
} from "react";
import { createRoot } from "react-dom/client";
import type {
  Workspace,
  CandidateList,
  CandidateCard,
  CandidateDetail,
  Standards,
  HatRule,
  VersionHistory,
  Run,
  EvidenceRef,
  AnalysisPoint,
  UploadResult,
  CompanyGroupConfig,
  CompanyGroupMapping,
  CompanyGroupUpdate,
  MatchDisplay,
  SourceBlock,
  GroupMoveRequest,
  GroupMoveResult,
} from "../../contracts/api";
import { AgentWorkbench } from "./AgentWorkbench";
import { api, ApiRequestError } from "./api";
import "./styles.css";
import { FilterSelect, UploadDialog } from "./UIControls";
import { HrReviewEditor, hrDecisionLabels } from "./HrReviewEditor";
import { StandardsList } from "./StandardsList";
import { useDocumentGestures } from "./useDocumentGestures";
import { RunProgress, CandidateRunProgress, hasCandidateProgress, isInputLimitError, runErrorMessage } from "./RunProgress";
import { DeleteCandidate } from "./CandidateRemoval";
const grades = {
  A: "建议立即约面",
  B: "可备选",
  C: "不建议推进",
  pending: "待处理",
};
const educationStatus = {
  studying: "在读",
  graduated: "已毕业",
  unknown: "状态待确认",
};
const stageLabels = {
  uploaded: "已上传",
  parsing: "读取中",
  analyzing: "分析中",
  ready: "已完成",
  needs_attention: "需要处理",
  failed: "处理失败",
};
function useRoute() {
  const [route, setRoute] = useState(location.pathname);
  useEffect(() => {
    const f = () => { if (!window.dispatchEvent(new Event("hr-before-navigate", { cancelable: true }))) { history.pushState(null, "", route); return; } setRoute(location.pathname); };
    window.addEventListener("popstate", f);
    return () => window.removeEventListener("popstate", f);
  }, [route]);
  return [
    route,
    (path: string) => {
      if (path !== route && !window.dispatchEvent(new Event("hr-before-navigate", { cancelable: true }))) return;
      history.pushState(null, "", path);
      setRoute(path);
      window.scrollTo(0, 0);
    },
  ] as const;
}
function App() {
  const [route, navigate] = useRoute();
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [list, setList] = useState<CandidateList | null>(null);
  const [companyConfig, setCompanyConfig] = useState<CompanyGroupConfig | null>(
    null,
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploadOpen,setUploadOpen]=useState(false);
  const [run, setRun] = useState<Run | null>(null);
  const refresh = useCallback(async () => {
    const [w, l, groups] = await Promise.all([
      api<Workspace>("/workspace"),
      api<CandidateList>("/candidates"),
      api<CompanyGroupConfig>("/company-groups"),
    ]);
    setWorkspace(w);
    setList(l);
    setCompanyConfig(groups);
  }, []);
  const act = async (work: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void refresh().catch((e) => setError(e.message));
  }, [refresh]);
  const [observedRunId, setObservedRunId] = useState<string | null>(() => {
    try { return sessionStorage.getItem("hr-last-run"); } catch { return null; }
  });
  const missingRuns = useRef(new Set<string>());
  const rememberRun = useCallback((id: string) => {
    setObservedRunId(id);
    try { sessionStorage.setItem("hr-last-run", id); } catch { /* Storage may be unavailable. */ }
  }, []);
  useEffect(() => {
    const f = (event: Event) => {
      const id = (event as CustomEvent<string>).detail;
      if (typeof id !== "string" || !id) return;
      missingRuns.current.delete(id);
      rememberRun(id);
    };
    window.addEventListener("hr-run", f);
    return () => window.removeEventListener("hr-run", f);
  }, [rememberRun]);
  const serviceRunId = workspace?.activeRunId || list?.activeRunId;
  useEffect(() => {
    if (serviceRunId && !missingRuns.current.has(serviceRunId)) rememberRun(serviceRunId);
  }, [serviceRunId, rememberRun]);
  const selectedRunId = serviceRunId || observedRunId;
  const runId = workspace && list && selectedRunId && !missingRuns.current.has(selectedRunId) ? selectedRunId : null;
  useEffect(() => {
    if (!runId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const r = await api<Run>(`/runs/${encodeURIComponent(runId)}`);
        if (stopped) return;
        setRun(r);
        await refresh();
        if (r.status === "queued" || r.status === "running")
          timer = setTimeout(tick, 1100);

      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiRequestError && e.status === 404) {
          missingRuns.current.add(runId);
          setRun(current => current?.id === runId ? null : current);
          setObservedRunId(current => current === runId ? null : current);
          try { if (sessionStorage.getItem("hr-last-run") === runId) sessionStorage.removeItem("hr-last-run"); } catch { /* Optional persistence. */ }
        } else setError(String(e));
      }
    };
    void tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [runId, refresh]);
  const [filters, setFilters] = useState(() => {
    try {
      return { ...(
        JSON.parse(sessionStorage.getItem("hr-filters") || "null") || {
          search: "",
          level: "",
          count: "0",
          company: "",
        }
      ), company: "" };
    } catch {
      return { search: "", level: "", count: "0", company: "" };
    }
  });
  useEffect(
    () => sessionStorage.setItem("hr-filters", JSON.stringify(filters)),
    [filters],
  );
  const candidates = (list?.candidates || [])
    .filter((c) => {
      const p = c.profile;
      const internships =
        p?.experiences.filter((x) => x.kind === "internship") || [];
      return (
        (!filters.search ||
          `${c.name} ${p?.education.map((x) => x.school).join(" ")}`
            .toLowerCase()
            .replace(/\s+/g, "")
            .includes(filters.search.toLowerCase().replace(/\s+/g, ""))) &&
        (!filters.level ||
          p?.education.some((x) => x.level === filters.level)) &&
        internships.length >= Number(filters.count)
      );
    })
    .sort(
      (a, b) =>
        ["A", "B", "C", "pending"].indexOf(a.grade) -
          ["A", "B", "C", "pending"].indexOf(b.grade) ||
        (a.rank ?? Infinity) - (b.rank ?? Infinity) ||
        a.id.localeCompare(b.id),
    );
  const openCandidate = (id: string) => {
    sessionStorage.setItem(
      "hr-board-position",
      JSON.stringify({ scroll: window.scrollY, id }),
    );
    navigate(`/candidates/${encodeURIComponent(id)}`);
  };
  useEffect(() => {
    if (route === "/" && list) {
      let p;
      try {
        p = JSON.parse(sessionStorage.getItem("hr-board-position") || "null");
      } catch {}
      if (p)
        requestAnimationFrame(() => {
          document
            .getElementById(`card-${p.id}`)
            ?.focus({ preventScroll: true });
          window.scrollTo(0, p.scroll);
        });
    }
  }, [route, !!list]);
  const upload = async (files:File[]) => {
    if(!files.length)return false;
    setBusy(true);setError('');
    try {const form=new FormData();files.forEach(file=>form.append('files',file));const result=await api<UploadResult>('/candidates','POST',form);await refresh();if(result.errors.length)throw new Error(result.errors.map(error=>`${error.filename}: ${error.message}`).join('\n'));return true;}finally{setBusy(false);}
  };
  const boardGroup=(candidate:CandidateCard)=>candidate.grade==='pending'||candidate.stage!=='ready'?'pending':candidate.grade;
  return (
    <div className="product-shell">
      <header className="app-header"><div className="job-heading">
        <a
          className="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            navigate("/");
          }}
        >
          <img className="micro-connect-logo" src="/micro-connect-logo.png" alt="Micro Connect 滴灌通" />
        </a><h1>Role: {(workspace?.jobTitle || 'HR AI Agent Intern').replace(/^Micro\s+Connect(?:\s*[-–—:：|]\s*|\s+)/i, '')}</h1></div>
        <nav>
          {[
            ["/", "候选人"],
            ["/standards", "查看岗位 JD"],
            ["/settings", "共同 HAT"],
            ["/agents", "Agent工作台"],
          ].map(([p, t]) => (
            <button
              key={p}
              className={route === p ? "active" : ""}
              onClick={() => navigate(p)}
            >
              {t}
            </button>
          ))}

                    <button className="primary" disabled={busy} onClick={()=>setUploadOpen(true)}>上传简历</button>
                    {workspace?.mode === "fixture" && (
                      <button
                        disabled={busy}
                        onClick={() =>
                          void act(async () => {
                            const r = await api<UploadResult>(
                              "/demo/import",
                              "POST",
                              { includeReal: true },
                            );
                            await refresh();
                            if (r.errors.length)
                              setError(
                                r.errors
                                  .map((x) => `${x.filename}: ${x.message}`)
                                  .join("\n"),
                              );
                          })
                        }
                      >
                        导入示例（含真实 CV）
                      </button>
                    )}
        </nav>
      </header>
      <main className={`workspace ${route==='/'?'board-workspace':''}`}>
        {workspace?.mode === "fixture" && (
          <div className="notice">
            本地测试模式 · 当前为界面测试数据，匹配结论尚未由模型评估
          </div>
        )}
        {workspace?.mode === "live" && !workspace.provider.configured && (
          <div className="notice">
            模型尚未配置。请在后端环境文件配置 OPENAI_API_KEY 与
            OPENAI_MODEL，再启动服务。仍可编辑标准、上传和查看资料。
          </div>
        )}
        {error && (
          <div className="notice error" role="alert">
            {error}
            <button onClick={() => void act(refresh)}>重试连接</button>
          </div>
        )}
        {route === "/agents" ? <AgentWorkbench /> : !workspace ? (
          <div className="loading">正在连接本地工作区…</div>
        ) : (
          <>
            {route === "/" ? (
              <>
                {!workspace.standards && (
                  <div className="notice">
                    请先确认岗位标准，再分析候选人。
                    <button onClick={() => navigate("/standards")}>
                      确认标准 →
                    </button>
                  </div>
                )}
                <div className="board-layout">
                  <CriteriaPanel
                    standards={workspace.standards}
                    busy={busy}
                    edit={() => navigate("/standards")}
                    onGroupMove={async(request)=>{setBusy(true);setError('');try{const result=await api<GroupMoveResult>('/standards/group-move','POST',request);setWorkspace(current=>current?{...current,standards:result.standards}:current);try{await refresh();}catch(error){setError(`标准已修改，列表刷新失败：${error instanceof Error?error.message:String(error)}`);}}catch(error){if(error instanceof ApiRequestError&&error.status===409)await refresh().catch(()=>{});throw error;}finally{setBusy(false);}}}
                    onReorder={async (group, ids) => {
                      const current = workspace.standards;
                      if (!current) return;
                      setBusy(true);
                      setError("");
                      try {
                        setList(
                          await api<CandidateList>(
                            "/standards/priorities",
                            "PATCH",
                            {
                              standardsVersion: current.version.id,
                              ...Object.fromEntries(
                                (["must", "bonus"] as const).map((g) => [
                                  g,
                                  g === group
                                    ? ids
                                    : current.criteria
                                        .filter((c) => c.group === g)
                                        .sort((a, b) => a.priority - b.priority)
                                        .map((c) => c.id),
                                ]),
                              ),
                            },
                          ),
                        );
                        await refresh();
                      } catch (error) {
                        setError(
                          error instanceof Error
                            ? error.message
                            : String(error),
                        );
                        throw error;
                      } finally {
                        setBusy(false);
                      }
                    }}
                  />

                  <section className="candidates-panel" aria-label="候选人列表">
                    <div className="candidate-list-head"><div className="candidate-list-title"><h2>候选人 <span>{candidates.length} / {list?.candidates.length??0}</span></h2><label className="field candidate-search"><input aria-label="姓名 / 学校" placeholder="姓名 / 学校" value={filters.search} onChange={event=>setFilters({...filters,search:event.target.value})}/></label></div>
                    <div className="filters-panel" role="search" aria-label="筛选候选人">

                      <FilterSelect label="学历" value={filters.level} options={[{value:'',label:'全部'},{value:'bachelor',label:'本科'},{value:'master',label:'硕士'},{value:'doctorate',label:'博士'},{value:'other',label:'其他'}]} onChange={level=>setFilters({...filters,level})}/>
                      <FilterSelect label="至少实习次数" value={filters.count} options={[0,1,2,3].map(count=>({value:String(count),label:count===0?'不限':`${count} 次`}))} onChange={count=>setFilters({...filters,count})}/>
                      <button onClick={()=>setFilters({search:'',level:'',count:'0',company:''})}>清除筛选</button>
                    </div>
                    </div>


                    {(["A", "B", "C", "pending"] as const).filter(group=>group!=='pending'||candidates.some(candidate=>boardGroup(candidate)==='pending')).map((g) => (
                      <section className="grade-group" key={g} id={`grade-${g}`}>
                        <h2 className="grade-heading">
                          <span className="badge">
                            {g === "pending" ? "…" : g}
                          </span>
                          {grades[g]}{" "}
                          <small>
                            {candidates.filter((c) => boardGroup(c) === g).length}
                          </small>
                        </h2>
                        <div className="card-grid">
                          {candidates
                            .filter((c) => boardGroup(c) === g)
                            .map((c) => (
                              <Card
                                key={c.id}
                                c={c}
                                run={run}
                                onDeleted={async () => {
                                  setList(current => current ? {...current,candidates:current.candidates.filter(candidate=>candidate.id!==c.id)} : current);
                                  try { await refresh(); } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
                                }}
                                open={() => openCandidate(c.id)}
                              />
                            ))}
                        </div>
                        {!candidates.some((c) => boardGroup(c) === g) && (
                          <p className="empty">暂无候选人</p>
                        )}
                      </section>
                    ))}
                  </section>

                </div>
              </>
            ) : route === "/standards" ? (
              <>
              {run && run.steps.length > 0 && run.steps.every(step => step.candidateId === null && step.role === 'jd') && <RunProgress run={run} candidates={[]} />}
              <StandardsEditor
                workspace={workspace}
                refresh={refresh}
                act={act}
                busy={busy}
              />
              </>
            ) : route === "/settings" ? (
              <Settings
                companyConfig={companyConfig}
                candidates={list?.candidates || []}
                workspace={workspace}
                refresh={refresh}
                act={act}
                busy={busy}
              />
            ) : route.startsWith("/candidates/") ? (
              <Detail
                workspace={workspace}
                key={route}
                id={decodeURIComponent(route.slice(12))}
                queue={candidates}
                navigate={navigate}
                act={act}
                busy={busy}
                refresh={refresh}
                run={run}
                activeRunId={workspace.activeRunId || list?.activeRunId || null}

              />
            ) : (
              <div className="empty">
                页面不存在
                <button onClick={() => navigate("/")}>返回列表</button>
              </div>
            )}
          </>
        )}
      </main>
      <UploadDialog open={uploadOpen} busy={busy} onClose={()=>setUploadOpen(false)} onConfirm={upload}/>
    </div>
  );
}
function CriteriaPanel({standards,busy,edit,onReorder,onGroupMove}:{standards:Standards|null;busy:boolean;edit:()=>void;onReorder:(group:'must'|'bonus',ids:string[])=>Promise<void>;onGroupMove:(request:GroupMoveRequest)=>Promise<void>}) {
  const [drag,setDrag]=useState<{id:string;group:'must'|'bonus'}|null>(null);
  const [target,setTarget]=useState<{id:string;edge:'before'|'after';cross:boolean}|null>(null);
  const [status,setStatus]=useState('');
  const [pending,setPending]=useState<{request:GroupMoveRequest;title:string}|null>(null);
  const [moveError,setMoveError]=useState('');
  const [blocked,setBlocked]=useState(false);
  const dialog=useRef<HTMLDialogElement>(null),submitting=useRef(false);
  useEffect(()=>{if(pending&&!dialog.current?.open)dialog.current?.showModal();else if(!pending&&dialog.current?.open)dialog.current.close();},[pending]);
  const clear=()=>{setDrag(null);setTarget(null);};
  const itemsFor=(group:'must'|'bonus')=>standards?.criteria.filter(criterion=>criterion.group===group).sort((a,b)=>a.priority-b.priority)||[];
  const reorder=async(group:'must'|'bonus',from:number,to:number)=>{const items=itemsFor(group);if(busy||from<0||to<0||to>=items.length||from===to)return;const movedId=items[from].id;const focused=document.activeElement;const focusLabel=focused instanceof HTMLButtonElement&&focused.closest('[data-criterion-id]')?.getAttribute('data-criterion-id')===movedId?focused.getAttribute('aria-label'):null;const ids=items.map(c=>c.id);ids.splice(to,0,ids.splice(from,1)[0]);setStatus('正在保存顺序…');try{await onReorder(group,ids);setStatus('顺序已保存');if(focusLabel)requestAnimationFrame(()=>{const row=Array.from(document.querySelectorAll('[data-criterion-id]')).find(row=>row.getAttribute('data-criterion-id')===movedId);const controls=Array.from(row?.querySelectorAll('button')||[]);(controls.find(button=>button.getAttribute('aria-label')===focusLabel&&!button.disabled)||controls.find(button=>!button.disabled))?.focus();});}catch{setStatus('保存失败，请检查提示');}};
  const drop=(group:'must'|'bonus',position:number)=>{const source=drag;clear();if(!source||busy||!standards)return;if(source.group===group){const from=itemsFor(group).findIndex(c=>c.id===source.id);void reorder(group,from,position-(from<position?1:0));return;}const criterion=standards.criteria.find(c=>c.id===source.id);if(!criterion)return;setMoveError('');setBlocked(false);setPending({title:criterion.title,request:{standardsVersion:standards.version.id,criterionId:source.id,targetGroup:group,position,actor:'本地 HR'}});};
  const confirm=async()=>{if(!pending||busy||submitting.current||blocked)return;submitting.current=true;setMoveError('');try{await onGroupMove(pending.request);setPending(null);setStatus('标准类型已修改，候选人将重新分析');}catch(error){const conflict=error instanceof ApiRequestError&&error.status===409;setBlocked(conflict);setMoveError(conflict?`标准或草稿已更新，此次移动未保存。请取消并处理最新标准后重试。${error.message}`:error instanceof ApiRequestError&&error.status===404?'当前后端尚不支持修改标准类型，请更新服务后重试。':error instanceof Error?error.message:String(error));}finally{submitting.current=false;}};
  return <aside className="criteria-panel"><h2>评估标准</h2><p className="muted">组内仅调整优先级，复用分析快速重排</p><p className="sort-status" role="status">{drag?target?.cross?'松开后确认修改标准类型':'移至插入线后松开':status}</p>
    {(['must','bonus'] as const).map(group=><div key={group} className="criterion-group" data-group={group}><h3>{group==='must'?'必须满足':'加分项'}</h3><div className="criterion-list">
      {itemsFor(group).map((criterion,index,items)=><div key={criterion.id} data-criterion-id={criterion.id} className={`criterion-row ${drag?.id===criterion.id?'dragging':''} ${target?.id===criterion.id?`drop-${target.edge}`:''}`} draggable={!busy} onKeyDown={event=>{if(event.key==='Escape')clear();}} onDragStart={event=>{if(busy){event.preventDefault();return;}event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',criterion.id);setDrag({id:criterion.id,group});setStatus('');}} onDragEnd={clear}
        onDragOver={event=>{if(!drag||busy)return;event.preventDefault();event.dataTransfer.dropEffect='move';const rect=event.currentTarget.getBoundingClientRect();setTarget({id:criterion.id,edge:event.clientY<rect.top+rect.height/2?'before':'after',cross:drag.group!==group});}}
        onDragLeave={event=>{if(!event.currentTarget.contains(event.relatedTarget as Node|null))setTarget(null);}}
        onDrop={event=>{event.preventDefault();const rect=event.currentTarget.getBoundingClientRect();drop(group,index+(event.clientY>=rect.top+rect.height/2?1:0));}}>
        <span className="drag-handle" aria-hidden="true">⠿</span><span className="criterion-title"><small>{index+1}</small>{criterion.title}</span><div className="criterion-actions"><button aria-label={`${criterion.title}上移`} disabled={busy||index===0} onClick={()=>void reorder(group,index,index-1)}>↑</button><button aria-label={`${criterion.title}下移`} disabled={busy||index===items.length-1} onClick={()=>void reorder(group,index,index+1)}>↓</button></div>
      </div>)}
      {!itemsFor(group).length&&<div data-empty-group={group} className={`criterion-empty-target ${target?.id===`empty-${group}`?'drop-active':''}`} onDragOver={event=>{if(!drag||busy)return;event.preventDefault();event.dataTransfer.dropEffect='move';setTarget({id:`empty-${group}`,edge:'before',cross:drag.group!==group});}} onDragLeave={()=>setTarget(null)} onDrop={event=>{event.preventDefault();drop(group,0);}}>暂无标准 · 可拖入</div>}
    </div></div>)}<button className="criteria-edit" onClick={edit}>编辑标准含义 →</button>
    <dialog className="group-move-dialog" ref={dialog} aria-labelledby="group-move-title" onCancel={event=>{event.preventDefault();if(!busy&&!submitting.current)setPending(null);}}>
      <h2 id="group-move-title">改为{pending?.request.targetGroup==='bonus'?'加分项':'必须项'}？</h2><p>{pending?.title}</p><p>这会改变岗位条件，并重新分析候选人。</p>{moveError&&<p className="notice error" role="alert">{moveError}</p>}<div className="toolbar"><button disabled={busy} onClick={()=>setPending(null)}>取消</button><button className="primary" disabled={busy||blocked} onClick={()=>void confirm()}>{busy?'正在提交…':'确认并重新分析'}</button></div>
    </dialog>
  </aside>;
}
// Card-only labels: preserve malformed parentheses and names made solely of a supplement.
function cardLabel(value: string) {
  const stack: string[] = [];
  let output = "";
  let hasPair = false;
  for (const character of value) {
    if (character === "(" || character === "（") stack.push(character);
    else if (character === ")" || character === "）") {
      if (stack.pop() !== (character === ")" ? "(" : "（")) return value;
      hasPair = true;
    } else if (!stack.length) output += character;
  }
  if (stack.length || !hasPair) return value;
  return output.replace(/\s+/g, " ").trim() || value;
}
function Card({ c, open, run, onDeleted }: { c: CandidateCard; open: () => void; run: Run | null; onDeleted: () => Promise<void> }) {
  const gradeReason = c.assessmentStatus === "current" ? c.gradeReason : undefined;
  const internships =
    c.profile?.experiences
      .filter((e) => e.kind === "internship")
      .sort((a, b) =>
        (b.end || b.start || "").localeCompare(a.end || a.start || ""),
      ) || [];
  return (
    <article id={`card-${c.id}`} className="candidate-card" tabIndex={0} aria-label={`查看 ${c.name} 详情`} onClick={() => { if (!c.deletionPending) open(); }} onKeyDown={event => { if (!c.deletionPending && event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); open(); } }}>
      <div className="card-name">
        <strong>{c.name}</strong>
        <span className="card-hr-mark" data-decision={c.hrReview?.decision || 'unreviewed'} aria-label={`HR 评价：${c.hrReview?.decision ? hrDecisionLabels[c.hrReview.decision] : '未评价'}`}><small>HR</small>{c.hrReview?.decision ? hrDecisionLabels[c.hrReview.decision] : '未评价'}</span>
      </div>
      <div className="card-education">
        {c.fictional && <span className="card-fictional">虚构示例</span>}
        {c.profile?.education.slice(0, 2).map((e, i) => (
          <div key={i}>
            <strong title={e.school}>{e.school}</strong>
            <p className="education-meta">
              <span title={e.degree}>{e.degree}</span>
              <b>{educationStatus[e.status]}</b>
              <span className="education-major" title={cardLabel(e.major)}>{cardLabel(e.major)}</span>
            </p>
          </div>
        ))}
        {!c.profile && <p>资料{stageLabels[c.stage]}</p>}
        {c.profile && !c.profile.education.length && <p>教育信息待核对</p>}
      </div>
      <div className="card-match">
        <div className="muted card-match-heading">
          <span>匹配概况 {!hasCandidateProgress(c, run) && c.stage!=='ready'&&` · ${stageLabels[c.stage]}`}
          {!hasCandidateProgress(c, run) && c.assessmentStatus === "stale"
            ? " · 旧版本待更新"
            : !hasCandidateProgress(c, run) && c.assessmentStatus === "review_required"
              ? " · 待复核"
              : ""}</span>
          <div className="card-match-status">
            {!c.deletionPending && <CandidateRunProgress candidate={c} run={run} />}
            {gradeReason && <span className="card-grade-reason" data-reason={gradeReason.code} title={gradeReason.explanation} aria-label={`${c.grade === "pending" ? "评级待复核" : `${c.grade} 级`}：${gradeReason.label}。${gradeReason.explanation}`}>{c.grade === "pending" ? gradeReason.label : `${c.grade} · ${gradeReason.label}`}</span>}
          </div>
        </div>
        <div className="card-match-copy"><p className="card-summary">{c.deletionPending ? "删除未完成，请点击卡片上的删除按钮重试。" : c.stage === 'failed' || c.stage === 'needs_attention' ? (c.assessmentStatus === 'stale' ? '新标准下的分析尚未完成，上一版结果待更新。' : '分析尚未完成，请查看运行进度。') : c.error || c.display?.summary || (c.matchSummary.startsWith('相关证据：') ? c.matchSummary.slice('相关证据：'.length) : c.matchSummary) || stageLabels[c.stage]}</p></div>
      </div>
      <div className="card-internships">
        <div className="card-internships-heading"><span className="muted">实际实习 · {internships.length} 段</span><DeleteCandidate candidate={c} onDeleted={onDeleted} /></div>
        {internships.slice(0, 3).map((e, i) => (
          <div className="internship-row" key={e.id}>
            <span className="internship-company" title={cardLabel(e.organization)}>
              {i + 1}. {cardLabel(e.organization)}
            </span>
          </div>
        ))}
        {!internships.length && <p>暂无已识别的实习记录</p>}
      </div>

    </article>
  );
}
type Shared = {
  workspace: Workspace;
  refresh: () => Promise<void>;
  act: (f: () => Promise<void>) => Promise<void>;
  busy: boolean;
};
function StandardsEditor({ workspace, refresh, act, busy }: Shared) {
  const [draft, setDraft] = useState<Standards>(
    workspace.draftStandards ||
      workspace.standards || {
        version: { id: "", number: 0, confirmedAt: null, confirmedBy: null },
        jdText: "",
        criteria: [],
      },
  );
  const [message, setMessage] = useState("");
  const [reply, setReply] = useState("");
  useEffect(() => {
    setDraft(workspace.draftStandards || workspace.standards || draft);
  }, [workspace.draftStandards?.version.id, workspace.standards?.version.id]);
  const save = () =>
    api<Standards>("/standards/draft", "PUT", {
      jdText: draft.jdText,
      criteria: draft.criteria.map((c, i) => ({ ...c, priority: i })),
    });
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>岗位评估标准</h1>
          <p>
            当前生效 v{workspace.standards?.version.number ?? "无"} ·
            {workspace.draftStandards ? "有草稿待确认" : "保存草稿不会激活新标准"}
          </p>
        </div>

      </div>
      <details className="jd-source-editor"><summary>查看 / 导入原始 JD</summary>
      <label className="button">
        导入 JD 文本
        <input
          type="file"
          accept=".txt,.md"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file)
              void act(async () => {
                setDraft({ ...draft, jdText: await file.text() });
              });
          }}
        />
      </label>
      <label className="field">
        原始 JD
        <textarea
          aria-label="原始 JD"
          rows={12}
          value={draft.jdText}
          onChange={(e) => setDraft({ ...draft, jdText: e.target.value })}
        />
      </label>
      <button
        disabled={busy || !draft.jdText.trim()}
        onClick={() =>
          void act(async () => {
            await api("/standards/parse", "POST", { jdText: draft.jdText });
            await refresh();
          })
        }
      >
        从 JD 生成草稿
      </button>
      </details>
      <StandardsList kind="jd" onAdd items={draft.criteria} busy={busy} onSave={async item=>{
        const criteria=draft.criteria.map(c=>c.id===item.id?{...c,title:item.title,description:item.description,group:item.group!}:c);
        if(!criteria.some(c=>c.id===item.id))criteria.push({id:item.id,title:item.title,description:item.description,group:item.group!,priority:criteria.length,jdQuote:""});
        const saved=await api<Standards>("/standards/draft","PUT",{jdText:draft.jdText,criteria});setDraft(saved);setReply("草稿已保存，确认后生效。");await refresh();
      }} onDelete={async id=>{const saved=await api<Standards>("/standards/draft","PUT",{jdText:draft.jdText,criteria:draft.criteria.filter(c=>c.id!==id).map((c,i)=>({...c,priority:i}))});setDraft(saved);await refresh();}} />
        <div className="toolbar">
          <button
            disabled={busy}
            onClick={() =>
              void act(async () => {
                setDraft(await save());
                setReply("草稿已保存，尚未激活。");
                await refresh();
              })
            }
          >
            保存草稿
          </button>
          <button
            className="primary"
            disabled={busy || !draft.criteria.length || (!workspace.draftStandards && draft.jdText === workspace.standards?.jdText && JSON.stringify(draft.criteria) === JSON.stringify(workspace.standards?.criteria))}
            onClick={() =>
              void act(async () => {
                await save();
                await api("/standards/confirm", "POST", { actor: "本地 HR" });
                await refresh();
                setReply("新标准已确认；候选人的受影响判断将重新分析。");
              })
            }
          >
            确认并应用标准
          </button>
        </div>
      {reply && <p className="standards-save-status" role="status">{reply}</p>}
      <details className="jd-discussion"><summary>讨论草稿</summary>
      <section className="chat-panel">
        <h2>讨论草稿</h2>
        <p>提出修改建议后仍需确认，才会应用到所有候选人。</p>
        <textarea
          aria-label="标准修改建议"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
        />
        <button
          disabled={busy || !message.trim()}
          onClick={() =>
            void act(async () => {
              await save();
              const r = await api<{ reply: string; draft: Standards | null }>(
                "/standards/chat",
                "POST",
                { message },
              );
              setReply(r.reply);
              if (r.draft) setDraft(r.draft);
              setMessage("");
              await refresh();
            })
          }
        >
          提出修改建议
        </button>
      </section>
      </details>
    </>
  );
}
function Settings({
  workspace,
  refresh,
  act,
  busy,
  companyConfig,
  candidates,
}: Shared & {
  companyConfig: CompanyGroupConfig | null;
  candidates: CandidateCard[];
}) {
  const [rules, setRules] = useState<HatRule[]>(
    workspace.draftHat?.rules || workspace.hat.rules,
  );
  useEffect(()=>{setRules(workspace.draftHat?.rules || workspace.hat.rules);},[workspace.draftHat,workspace.hat]);
  const [history, setHistory] = useState<VersionHistory | null>(null);
  const [saved, setSaved] = useState("");
  useEffect(() => {
    void api<VersionHistory>("/versions")
      .then(setHistory)
      .catch((e) => setSaved(e.message));
  }, [workspace.hat.version.id]);
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>共同 HAT</h1>
          <p>所有判断共用已确认的规则 · 当前 v{workspace.hat.version.number}{workspace.draftHat ? " · 有草稿待确认" : ""}</p>
        </div>
      </div>
      <p className="notice">
        五项核心约束始终生效。修改说明需再次确认，受影响的结论会重新复核。
      </p>
      <StandardsList kind="hat" items={rules.map(r=>({id:r.id,title:r.title,description:r.text}))} busy={busy} onSave={async item=>{
        const updated=rules.map(r=>r.id===item.id?{...r,title:item.title,text:item.description}:r);
        const result=await api<{rules:HatRule[]}>("/hat/draft","PUT",{rules:updated});setRules(result.rules);setSaved("草稿已保存，尚未生效。");await refresh();
      }} />
      <div className="toolbar">
        <button
          disabled={busy}
          onClick={() =>
            void act(async () => {
              await api("/hat/draft", "PUT", { rules });
              setSaved("草稿已保存，尚未生效。");
              await refresh();
            })
          }
        >
          保存草稿
        </button>
        <button
          className="primary"
          disabled={busy || !workspace.draftHat}
          onClick={() =>
            void act(async () => {
              await api("/hat/draft", "PUT", { rules });
              await api("/hat/confirm", "POST", { actor: "本地 HR" });
              await refresh();
              setSaved("规则已确认。");
            })
          }
        >
          确认共同 HAT
        </button>
        <span role="status">{saved}</span>
      </div>
      {companyConfig && (
        <CompanyGroupsEditor
          initial={companyConfig}
          candidates={candidates}
          refresh={refresh}
        />
      )}
      <h2>版本历史</h2>
      {history?.hats.map((h) => (
        <div className="history-row" key={h.version.id}>
          HAT v{h.version.number} · {h.version.confirmedBy} ·{" "}
          {h.version.confirmedAt &&
            new Date(h.version.confirmedAt).toLocaleString()}
          <details>
            <summary>查看规则</summary>
            {h.rules.map((r) => (
              <p key={r.id}>
                {r.title}：{r.text}
              </p>
            ))}
          </details>
        </div>
      ))}
      {history?.standards.map((s) => (
        <div className="history-row" key={s.version.id}>
          标准 v{s.version.number} · {s.version.confirmedBy} ·{" "}
          {s.version.confirmedAt &&
            new Date(s.version.confirmedAt).toLocaleString()}
          <details>
            <summary>查看标准</summary>
            {s.criteria.map((c) => (
              <p key={c.id}>
                {c.title}：{c.description}
                <br />
                JD：{c.jdQuote}
              </p>
            ))}
          </details>
        </div>
      ))}
    </>
  );
}
function CompanyGroupsEditor({
  initial,
  candidates,
  refresh,
}: {
  initial: CompanyGroupConfig;
  candidates: CandidateCard[];
  refresh: () => Promise<void>;
}) {
  const [base, setBase] = useState(initial);
  const [rows, setRows] = useState<CompanyGroupMapping[]>(initial.mappings);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const [failed, setFailed] = useState(false);
  const dirty = JSON.stringify(rows) !== JSON.stringify(base.mappings);
  const organizations = Array.from(
    new Set(
      candidates.flatMap(
        (c) => c.profile?.experiences.map((e) => e.organization) || [],
      ),
    ),
  ).sort();
  const save = async () => {
    setSaving(true);
    setMessage("");
    setFailed(false);
    setConflict(false);
    try {
      const request: CompanyGroupUpdate = {
        expectedVersion: base.version.id,
        actor: "本地 HR",
        mappings: rows.map((row) => ({
          organization: row.organization.trim(),
          group: row.group.trim(),
        })),
      };
      const result = await api<CompanyGroupConfig>(
        "/company-groups",
        "PUT",
        request,
      );
      setBase(result);
      setRows(result.mappings);
      await refresh();
      setMessage("公司分组已保存，候选人筛选已更新。");
    } catch (error) {
      setFailed(true);
      const stale = error instanceof ApiRequestError && error.status === 409;
      setConflict(stale);
      setMessage(
        stale
          ? "配置已被其他编辑更新，当前修改仍保留。请复制需保留的修改，再加载最新版本。"
          : error instanceof Error
            ? error.message
            : String(error),
      );
    } finally {
      setSaving(false);
    }
  };
  return (
    <details className="company-settings chat-panel">
      <summary>
        公司分组 · v{base.version.number}
        {dirty ? " · 未保存" : ""}
      </summary>
      <p className="muted">
        由 HR
        明确指定公司名称与分组，仅用于筛选，不影响评级、排序或岗位标准。公司名须与简历原文一致；别名可另加一行。
      </p>
      <p className="muted">
        版本 v{base.version.number} · {base.version.confirmedBy} ·{" "}
        {base.version.confirmedAt &&
          new Date(base.version.confirmedAt).toLocaleString()}
      </p>
      <datalist id="existing-organizations">
        {organizations.map((name) => (
          <option value={name} key={name} />
        ))}
      </datalist>
      {!rows.length && <p className="notice">尚未配置公司分组</p>}
      {rows.map((row, index) => (
        <div className="company-mapping-row" key={index}>
          <label className="field">
            公司名称
            <input
              aria-label={`公司名称 ${index + 1}`}
              list="existing-organizations"
              disabled={saving}
              value={row.organization}
              onChange={(e) =>
                setRows(
                  rows.map((r, i) =>
                    i === index ? { ...r, organization: e.target.value } : r,
                  ),
                )
              }
            />
          </label>
          <label className="field">
            分组名称
            <input
              aria-label={`分组名称 ${index + 1}`}
              disabled={saving}
              value={row.group}
              onChange={(e) =>
                setRows(
                  rows.map((r, i) =>
                    i === index ? { ...r, group: e.target.value } : r,
                  ),
                )
              }
            />
          </label>
          <button
            disabled={saving}
            aria-label={`删除公司分组 ${index + 1}`}
            onClick={() => setRows(rows.filter((_, i) => i !== index))}
          >
            删除
          </button>
        </div>
      ))}
      <div className="toolbar">
        <button
          disabled={saving}
          onClick={() => setRows([...rows, { organization: "", group: "" }])}
        >
          添加公司分组
        </button>
        <button
          className="primary"
          disabled={
            saving ||
            !dirty ||
            rows.some((row) => !row.organization.trim() || !row.group.trim())
          }
          onClick={() => void save()}
        >
          保存公司分组
        </button>
      </div>
      {message && (
        <p
          className={failed ? "notice error" : "notice"}
          role={failed ? "alert" : "status"}
        >
          {message}
        </p>
      )}
      {conflict && (
        <button
          disabled={saving}
          onClick={() => {
            setSaving(true);
            void api<CompanyGroupConfig>("/company-groups")
              .then((result) => {
                setBase(result);
                setRows(result.mappings);
                setConflict(false);
                setFailed(false);
                setMessage("已加载最新版本，请重新编辑。");
              })
              .catch((error) => setMessage(error.message))
              .finally(() => setSaving(false));
          }}
        >
          放弃本地编辑并加载最新版本
        </button>
      )}
    </details>
  );
}
function orderedSources(refs:EvidenceRef[],blocks:SourceBlock[]) {
  const unique=Array.from(new Set(refs.map(ref=>ref.blockId))).map(blockId=>({blockId,block:blocks.find(block=>block.id===blockId),order:blocks.findIndex(block=>block.id===blockId),refs:refs.filter(ref=>ref.blockId===blockId)}));
  const noCoordinates=new Set(unique.filter(source=>source.block&&!source.block.box).map(source=>source.block!.page));
  return unique.sort((a,b)=>(a.block?.page??Infinity)-(b.block?.page??Infinity)||(a.block&&b.block&&!noCoordinates.has(a.block.page)&&a.block.box&&b.block.box?(a.block.box.y-b.block.box.y||a.block.box.x-b.block.box.x):0)||a.order-b.order);
}
function CompactReason({ display }: { display: MatchDisplay }) {
  const paragraph = useRef<HTMLParagraphElement>(null);
  const [text, setText] = useState(display.shortReason);
  const factsKey = JSON.stringify(display.shortFacts || []);
  useLayoutEffect(() => {
    const node = paragraph.current;
    if (!node) return;
    let lastWidth = -1;
    const measure = () => {
      const width = node.getBoundingClientRect().width;
      if (!width || Math.abs(width - lastWidth) < 0.1) return;
      lastWidth = width;
      const facts = (display.shortFacts || []).filter(Boolean).slice(0, 2);
      if (!facts.length) {
        setText(display.shortReason);
        return;
      }
      const probe = node.cloneNode(false) as HTMLParagraphElement;
      Object.assign(probe.style, {
        position: "absolute",
        visibility: "hidden",
        pointerEvents: "none",
        width: `${width}px`,
        height: "auto",
        maxHeight: "none",
        margin: "0",
        left: "0",
        top: "0",
      });
      probe.removeAttribute("id");
      probe.setAttribute("aria-hidden", "true");
      node.parentElement!.appendChild(probe);
      const lineHeight = parseFloat(getComputedStyle(node).lineHeight);
      const fits = (value: string, lines: number) => {
        probe.textContent = value;
        return probe.getBoundingClientRect().height <= lineHeight * lines + 1;
      };
      const full = facts.join(" "),
        first = facts[0];
      const selected = fits(full, 3) ? full : first;
      probe.remove();
      setText(selected);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [display.shortReason, factsKey]);
  return (
    <p className="point-explanation" data-compact-reason="true" ref={paragraph}>
      {text}
    </p>
  );
}
function Detail({
  workspace,
  id,
  queue,
  navigate,
  act,
  busy,
  refresh,
  activeRunId,
  run,
}: {
  workspace: Workspace;
  id: string;
  queue: CandidateCard[];
  navigate: (p: string) => void;
  act: Shared["act"];
  busy: boolean;
  refresh: () => Promise<void>;
  activeRunId: string | null;
  run: Run | null;
}) {
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<{
    id: string;
    title: string;
    refs: EvidenceRef[];
    tab: number;
  } | null>(null);
  const [index, setIndex] = useState(0);
  const [sourceZoom, setSourceZoom] = useState(100);
  const [fitPage, setFitPage] = useState(true);
  const [activeTab, setActiveTab] = useState(0);
  const layoutRef = useRef<HTMLDivElement>(null);
  const documentViewport = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState({ width: 600, height: 650 });
  const changeSourceZoom = useDocumentGestures(documentViewport, !!detail, sourceZoom, fitPage, setSourceZoom, setFitPage);
  useLayoutEffect(() => {
    if (!detail || !layoutRef.current || !documentViewport.current) return;
    const layout = layoutRef.current;
    const viewer = documentViewport.current;
    const size = () => {
      layout.style.setProperty('--detail-height', `${Math.max(380, innerHeight - layout.getBoundingClientRect().top - 20)}px`);
    };
    size();
    const observer = new ResizeObserver(() => setViewport({ width: viewer.clientWidth, height: viewer.clientHeight }));
    observer.observe(viewer);
    window.addEventListener('resize', size);
    return () => { observer.disconnect(); window.removeEventListener('resize', size); };
  }, [!!detail]);
  const showSource = (blockId: string | undefined) => {
    const target = document.getElementById(`source-${blockId}`);
    const viewer = documentViewport.current;
    if (!target || !viewer) return;
    viewer.scrollLeft += target.getBoundingClientRect().left - viewer.getBoundingClientRect().left - viewer.clientWidth / 2 + target.clientWidth / 2;
    viewer.scrollTo({ top: viewer.scrollTop + target.getBoundingClientRect().top - viewer.getBoundingClientRect().top - viewer.clientHeight / 2 + target.clientHeight / 2, behavior: 'smooth' });
    if (matchMedia('(max-width: 850px)').matches) viewer.closest('.source-pane')?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  };
  const [copied, setCopied] = useState(false);
  const load = useCallback(
    async () =>
      setDetail(
        await api<CandidateDetail>(`/candidates/${encodeURIComponent(id)}`),
      ),
    [id],
  );
  useEffect(() => {
    void load().catch((e) => setError(e.message));
    if (!activeRunId) return;
    const t = setInterval(
      () => void load().catch((e) => setError(e.message)),
      1500,
    );
    return () => clearInterval(t);
  }, [load, activeRunId]);
  const sources=orderedSources(selected?.refs||[],detail?.document.blocks||[]);
  const focusEvidence = (
    refs: EvidenceRef[],
    pointId: string,
    title: string,
  ) => {
    setSelected({ id: pointId, title, refs, tab: activeTab });
    setFitPage(false);
    setIndex(0);
    requestAnimationFrame(() => showSource(orderedSources(refs,detail?.document.blocks||[])[0]?.blockId));
  };
  const jump = (n: number) => {
    setIndex(n);
    showSource(sources[n]?.blockId);
  };
  if (error)
    return (
      <div className="notice error" role="alert">
        {error}
        <button onClick={() => navigate("/")}>← 返回候选人列表</button>
      </div>
    );
  if (!detail) return <p className="loading">正在读取候选人和原文…</p>;
  const { candidate: c, document: doc, assessment: a } = detail;
  const pos = queue.findIndex((c) => c.id === id);
  const questions = a?.questions || [];
  const point = (p: AnalysisPoint, ordinal: number, concern = false) => {
    const compact =
      !concern && a && detail.display?.assessmentId === a.id
        ? detail.display.matches.find((item) => item.pointId === p.id)
        : undefined;
    const fixtureUnknown =
      workspace.mode === "fixture" && p.evidence.length === 0;
    return (
      <article
        className="analysis-item"
        id={`point-${p.id}`}
        key={p.id}
        tabIndex={-1}
      >
        <span className="analysis-ordinal">{ordinal}</span>
        <div className="analysis-point-body">
        <button
          className="source-action"
          disabled={!p.evidence.length}
          onClick={() => focusEvidence(p.evidence, p.id, p.title)}
        >
          {p.evidence.length
            ? `${concern ? "相关原文" : "查看原文"} ↗ ${new Set(p.evidence.map(ref=>ref.blockId)).size}`
            : "尚未关联原文"}
        </button>
        <h3>
          {compact?.title || p.title}

        </h3>
        {compact && !fixtureUnknown ? (
          <CompactReason display={compact} />
        ) : (
          <p className="point-explanation">
            {fixtureUnknown
              ? "界面测试模式尚未完成这一项的语义评估，尚未关联原文。"
              : p.explanation}
          </p>
        )}
        <details className="requirement-disclosure">
          <summary>
            {compact
              ? `查看完整依据${compact.additionalFactCount > 0 ? " · 含更多事实" : ""}`
              : "查看岗位要求"}
          </summary>
          {compact?.coverage==='partial'&&<p className="coverage-explanation">这段经历支持该项要求的一部分，完整范围与限制如下。</p>}
          {compact && <p className="full-explanation">{p.explanation}</p>}
          <p className="full-jd">岗位要求：{p.jdRequirement}</p>
        </details>
        </div>
      </article>
    );
  };
  return (
    <div className="detail-view">
      <nav className="detail-navigation" aria-label="候选人导航">
        <button className="detail-back" onClick={() => navigate("/")}>← 返回候选人列表</button>
        <div className="toolbar">
          <button
            disabled={pos <= 0}
            onClick={() =>
              navigate(`/candidates/${encodeURIComponent(queue[pos - 1].id)}`)
            }
          >
            ← 上一位
          </button>
          <span>
            {pos < 0 ? "不在当前筛选" : `${pos + 1} / ${queue.length}`}
          </span>
          <button
            disabled={pos < 0 || pos >= queue.length - 1}
            onClick={() =>
              navigate(`/candidates/${encodeURIComponent(queue[pos + 1].id)}`)
            }
          >
            下一位 →
          </button>
            {c.stage !== "ready" && <CandidateRunProgress candidate={c} run={run} placement="detail" />}
            <button className="detail-reanalyze"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  await api(
                    `/candidates/${encodeURIComponent(id)}/analyze`,
                    "POST",
                    {},
                  );
                  await refresh();
                  await load();
                })
              }
            >
              重新分析
            </button>
            <HrReviewEditor key={c.id} candidateId={c.id} value={c.hrReview} onSaved={async hrReview=>{setDetail(current=>current?.candidate.id===c.id?{...current,candidate:{...current.candidate,hrReview}}:current);await refresh();}} />
        </div>
      </nav>
      <div className="detail-heading">
        <div className="detail-resume">
          <div className="identity-topline">
            <h1>{c.name}</h1>
            <div className="identity-rating"><span>{c.recommendation ? ({interview:"建议面试",hold:"可待确认",do_not_progress:"暂不推进",needs_review:"需要复核"} as const)[c.recommendation] : "评级"}</span><strong>{c.grade === "pending" ? (c.stage === "ready" ? "评级待复核" : "待处理") : c.grade}</strong>{c.grade !== "pending" && c.assessmentStatus === "current" && c.gradeReason && <span className="identity-grade-reason" data-reason={c.gradeReason.code}>{c.gradeReason.label}</span>}</div>
          </div>
          {c.assessmentStatus === "current" && c.gradeReason && <p className="grade-explanation">{c.gradeReason.explanation}</p>}
          {c.profile?.education.map((education, i) => (
            <div className="identity-fields" key={i}>
              <div><span className="identity-label">学校</span><strong>{education.school}</strong></div>
              <div><span className="identity-label">学历</span><p>{[education.degree, educationStatus[education.status]].filter(Boolean).join(" · ")}</p></div>
              <div><span className="identity-label">专业</span><p>{education.major}</p></div>
            </div>
          ))}
          {(!c.profile || !c.profile.education.length) && <p className="identity-fields">教育信息待核对</p>}
        </div>

      </div>
      {(c.error || doc.status !== "ready") && (
        <div className="notice error">
          {c.error && isInputLimitError(c.error) ? runErrorMessage(c.error) : <>{c.error ||
            `原文状态：${doc.status === "needs_ocr" ? "需要 OCR" : "读取失败"}`}
          。无法读取的资料保持待处理，不等于不匹配。</>}
        </div>
      )}
      {a?.status !== "current" && (
        <div className="notice">
          {a?.status === "stale"
            ? "此结论基于旧版本，正在等待重新分析。"
            : a?.status === "review_required"
              ? "此结论需要人工复核。"
              : "尚无可用分析。请确认标准与模型配置后分析。"}
        </div>
      )}
      <div className="detail-layout" ref={layoutRef}>
        <section className="source-pane" aria-label="简历原文">
          <div className="source-toolbar">
            <h2>简历原文</h2>
            <a href={doc.originalUrl} target="_blank" rel="noreferrer">
              打开 / 下载原件 ↗
            </a>
            {selected && (
              <>
                <p className="source-current" aria-live="polite">
                  {selected.title} · 当前来源 {index + 1} /{" "}
                  {sources.length} · 第 {sources[index]?.block?.page ?? '—'}{" "}
                  页
                </p>
                <div className="toolbar">
                  <button
                    disabled={index === 0}
                    onClick={() => jump(index - 1)}
                  >
                    上一段
                  </button>
                  <button
                    disabled={index === sources.length - 1}
                    onClick={() => jump(index + 1)}
                  >
                    下一段
                  </button>
                  <button
                    onClick={() => {
                      setActiveTab(selected.tab);
                      requestAnimationFrame(() => {
                        const el = document.getElementById(`point-${selected.id}`);
                        const panel = el?.closest<HTMLElement>('[role="tabpanel"]');
                        if (el && panel) panel.scrollTop += el.getBoundingClientRect().top - panel.getBoundingClientRect().top - 12;
                        el?.focus({ preventScroll: true });
                        if (matchMedia('(max-width: 850px)').matches) el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
                      });
                    }}
                  >
                    回到分析 ↑
                  </button>
                </div>
              </>
            )}
          </div>
          {doc.pages.some((page) => page.imageUrl) && (
            <div className="source-zoom toolbar" aria-label="原文缩放">
              <div className="source-view-modes" role="group" aria-label="原文显示模式">
                <button aria-pressed={fitPage} onClick={() => { setFitPage(true); requestAnimationFrame(() => {
                  const viewer = documentViewport.current;
                  const page = viewer?.querySelector<HTMLElement>(`[data-document-page="${sources[index]?.block?.page || doc.pages[0]?.number}"]`);
                  if (viewer && page) viewer.scrollTop += page.getBoundingClientRect().top - viewer.getBoundingClientRect().top;
                }); }}>整页</button>
                <button aria-pressed={!fitPage} onClick={() => { setFitPage(false); setSourceZoom(100); }}>适合宽度</button>
              </div>
              {!fitPage && <div className="source-scale-controls" role="group" aria-label="阅读缩放">
                <button aria-label="缩小原文" disabled={sourceZoom <= 100} onClick={() => changeSourceZoom(Math.max(100, sourceZoom - 50))}>−</button>
                <output aria-live="polite">{sourceZoom}%</output>
                <button aria-label="放大原文" disabled={sourceZoom >= 300} onClick={() => changeSourceZoom(Math.min(300, sourceZoom + 50))}>＋</button>
              </div>}
            </div>
          )}
          <div className="document-viewport" ref={documentViewport} tabIndex={0} aria-label="原文阅读区域，可滚动、缩放和拖动">
          {doc.warnings.map((w, i) => (
            <p className="notice" key={i}>
              {w}
            </p>
          ))}
          {doc.previewKind === "text" && (
            <p className="notice">
              以下为文本预览，保留提取顺序；排版请查看原件。
            </p>
          )}
          {doc.pages.map((page) => (
            <div key={page.number} data-document-page={page.number}>
              <div
                className="source-scroll"
                aria-label={`第 ${page.number} 页原文`}
              >
                <div
                  className="source-canvas"
                  style={{
                    width: page.imageUrl ? (fitPage ? `${Math.max(80, Math.min(viewport.width - 16, (viewport.height - 28) * page.width / page.height + 80))}px` : `${Math.max(80, (viewport.width - 16) * sourceZoom / 100)}px`) : "100%",
                    paddingLeft: page.imageUrl ? 40 : 0,
                    paddingRight: page.imageUrl ? 40 : 0,
                  }}
                >
                  <div
                    className="source-page"
                    style={{
                      position: "relative",
                      aspectRatio: page.imageUrl
                        ? `${page.width} / ${page.height}`
                        : undefined,
                    }}
                  >
                    {page.imageUrl ? (
                      <>
                        <img
                          className="source-image"
                          draggable={false}
                          src={page.imageUrl}
                          alt={`${doc.filename} 第 ${page.number} 页`}
                        />
                        {doc.blocks
                          .filter(
                            (b) =>
                              b.page === page.number &&
                              b.box &&
                              selected?.refs.some((e) => e.blockId === b.id),
                          )
                          .map((b) => {
                            const box = b.box!;
                            const number=sources.findIndex(source=>source.blockId===b.id)+1;
                            return (
                              <React.Fragment key={b.id}>
                                <button
                                  className="evidence-number" data-source-number={number}
                                  style={{ top: `${box.y * 100}%` }}
                                  aria-label={`定位来源 ${number}，第 ${page.number} 页`}
                                  onClick={() => jump(number - 1)}
                                >
                                  {number}
                                </button>
                                <button
                                  id={`source-${b.id}`}
                                  key={b.id}
                                  className="evidence-overlay" data-source-number={number}
                                  style={{
                                    position: "absolute",
                                    left: `${box.x * 100}%`,
                                    top: `${box.y * 100}%`,
                                    width: `${box.width * 100}%`,
                                    height: `${box.height * 100}%`,
                                  }}
                                  title={b.text}
                                  aria-label={`原文 ${number}: ${b.text}`}
                                  onClick={() => setIndex(number - 1)}
                                ></button>
                              </React.Fragment>
                            );
                          })}
                      </>
                    ) : (
                      <>
                        <h3>第 {page.number} 页 · 文本</h3>
                        {doc.blocks
                          .filter((b) => b.page === page.number)
                          .map((b) => (
                            <p
                              id={`source-${b.id}`}
                              key={b.id}
                              className={`source-block ${selected?.refs.some((e) => e.blockId === b.id) ? "highlighted" : ""}`}
                            >
                              {selected?.refs.some(
                                (e) => e.blockId === b.id,
                              ) && (
                                <strong>
                                  〔
                                  来源 {sources.findIndex(source=>source.blockId===b.id)+1}
                                  〕{" "}
                                </strong>
                              )}
                              {b.text}
                            </p>
                          ))}
                      </>
                    )}
                  </div>
                </div>
              </div>
              {page.imageUrl&&sources.filter(source=>source.block?.page===page.number).map(source=><div className="source-block highlighted" key={source.blockId}>{!source.block?.box&&<span id={`source-${source.blockId}`}/>}<strong>来源 {sources.indexOf(source)+1} · 第 {page.number} 页</strong>{source.refs.map((ref,i)=><p key={i}>{ref.quote}</p>)}</div>)}
            </div>
          ))}
          {sources.filter(source=>!source.block).map(source=><div id={`source-${source.blockId}`} className="source-block highlighted" key={source.blockId}><strong>来源 {sources.indexOf(source)+1} · 原文段落待定位</strong>{source.refs.map((ref,i)=><p key={i}>{ref.quote}</p>)}</div>)}
          {!doc.pages.length &&
            doc.blocks.map((b) => (
              <p
                id={`source-${b.id}`}
                key={b.id}
                className={`source-block ${selected?.refs.some((e) => e.blockId === b.id) ? "highlighted" : ""}`}
              >
                {b.text}
              </p>
            ))}
          </div>
        </section>
        <div className="analysis-pane">
          <div className="analysis-tabs" role="tablist" aria-label="候选人分析">
            {['匹配的点', '问题与待确认', '面试可以确认的问题'].map((title, tab) => <button key={title} id={`analysis-tab-${tab}`} role="tab" aria-selected={activeTab === tab} aria-controls={`analysis-panel-${tab}`} tabIndex={activeTab === tab ? 0 : -1} onClick={() => setActiveTab(tab)} onKeyDown={event => {
              const next = event.key === 'ArrowRight' ? (tab + 1) % 3 : event.key === 'ArrowLeft' ? (tab + 2) % 3 : event.key === 'Home' ? 0 : event.key === 'End' ? 2 : null;
              if (next !== null) { event.preventDefault(); setActiveTab(next); document.getElementById(`analysis-tab-${next}`)?.focus(); }
            }}>{title}<span>{[a?.matches.length || 0, a?.concerns.length || 0, questions.length][tab]}</span></button>)}
          </div>
          <section className="analysis-section analysis-matches" role="tabpanel" id="analysis-panel-0" aria-labelledby="analysis-tab-0" tabIndex={0} hidden={activeTab !== 0}>
            <h2>匹配的点</h2>
            {(a?.matches || []).map((p, i) => point(p, i + 1))}
            {!a?.matches.length && (
              <p className="empty">暂无充分证据支持的匹配点。</p>
            )}
          </section>
          <section className="analysis-section analysis-concerns" role="tabpanel" id="analysis-panel-1" aria-labelledby="analysis-tab-1" tabIndex={0} hidden={activeTab !== 1}>
            <h2>问题与待确认</h2>
            {(a?.concerns || []).map((p, i) => point(p, i + 1, true))}
            {!a?.concerns.length && (
              <p className="empty">暂无已生成的待确认点。</p>
            )}
          </section>
          <section className="analysis-section analysis-questions" role="tabpanel" id="analysis-panel-2" aria-labelledby="analysis-tab-2" tabIndex={0} hidden={activeTab !== 2}>
            <div className="toolbar">
              <h2>面试可以确认的问题</h2>
              <button
                disabled={!a?.questions.length}
                onClick={() =>
                  void act(async () => {
                    await navigator.clipboard.writeText(
                      questions
                        .map((q, i) => `${i + 1}. ${q.question}\n${q.purpose}`)
                        .join("\n\n"),
                    );
                    setCopied(true);
                  })
                }
              >
                {copied ? "已复制" : "复制全部"}
              </button>
            </div>
            {questions.map((q, i) => (
              <article
                className="analysis-item"
                id={`point-${q.id}`}
                tabIndex={-1}
                key={q.id}
              >
                <span className="analysis-ordinal">{i + 1}</span>
                <div className="analysis-point-body">
                {!!q.evidence.length && (
                  <button
                    className="source-action"
                    onClick={() => focusEvidence(q.evidence, q.id, "面试问题")}
                  >
                    相关原文 ↗ {new Set(q.evidence.map(ref=>ref.blockId)).size}
                  </button>
                )}
                <h3 className="question-text">
                  {q.question}
                </h3>
                <details className="question-purpose">
                  <summary>查看提问目的</summary>
                  <p>{q.purpose}</p>
                </details>
                </div>
              </article>
            ))}
            {a && a.questions.length !== 3 && (
              <p className="notice">
                面试问题尚未完整生成（{a.questions.length}/3），请重新检查。
              </p>
            )}
          </section>

        </div>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

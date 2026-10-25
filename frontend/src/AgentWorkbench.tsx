import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type {
  AgentRole as Role,
  AgentRoleSettings as RoleConfig,
  AgentConfig as Config,
  AgentWorkbench as State,
  AgentRunSummary as RunSummary,
  AgentRunDebug as RunDetail,
} from "../../contracts/api";
import "./agent-workbench.css";
const roles: Role[] = ["jd", "cv", "match", "review", "interview"];
const names: Record<Role, string> = {
  jd: "理解岗位",
  cv: "整理简历",
  match: "逐项匹配",
  review: "复核证据",
  interview: "面试问题",
};
const notes: Record<Role, string> = {
  jd: "原始 JD → 待确认标准",
  cv: "简历原文 → 结构化经历",
  match: "确认标准 + 简历 → 匹配与疑问",
  review: "匹配结果 + 原文 → 检查与修复意见",
  interview: "通过复核的待确认点 → 三个问题",
};
const same = (a: unknown, b: unknown) =>
  JSON.stringify(a) === JSON.stringify(b);
const body = (c: Config) => ({ roles: c.roles, flow: c.flow });
const artifactNames: Record<string, string> = {
  role_input: "输入与实际提示",
  role_output: "结构化输出",
  role_error: "调用错误",
  review_decision: "复核问题与决定",
  input: "输入",
  output: "输出",
  error: "错误",
};
const json = (x: unknown) => JSON.stringify(x, null, 2);
function changes(a: Config, b: Config) {
  const out: { label: string; before: unknown; after: unknown }[] = [];
  for (const role of roles)
    for (const key of Object.keys(a.roles[role]) as (keyof RoleConfig)[])
      if (!same(a.roles[role][key], b.roles[role][key]))
        out.push({
          label: `${names[role]} · ${key}`,
          before: a.roles[role][key],
          after: b.roles[role][key],
        });
  for (const key of ["matchRepairs", "interviewRepairs"] as const)
    if (a.flow[key] !== b.flow[key])
      out.push({
        label: key === "matchRepairs" ? "匹配修复次数" : "面试问题修复次数",
        before: a.flow[key],
        after: b.flow[key],
      });
  return out;
}
export function AgentWorkbench() {
  const [state, setState] = useState<State | null>(null),
    [draft, setDraft] = useState<Config | null>(null),
    [tab, setTab] = useState("flow"),
    [role, setRole] = useState<Role>("match"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [confirm, setConfirm] = useState(false);
  const [runs, setRuns] = useState<RunSummary[]>([]),
    [runsLoaded, setRunsLoaded] = useState(false),
    [runId, setRunId] = useState(""),
    [detail, setDetail] = useState<RunDetail | null>(null),
    [attempt, setAttempt] = useState("all");
  const requestId = useRef(0);
  const dirty = !!state && !!draft && !same(body(state.draft), body(draft));
  const accept = (s: State) => {
    setState(s);
    setDraft(structuredClone(s.draft));
    setConfirm(false);
  };
  const load = async () => {
    setBusy(true);
    setError("");
    try {
      accept(await api<State>("/agent-workbench"));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  useEffect(() => {
    if (!dirty) return;
    const unload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    const leave = (e: Event) => {
      if (!window.confirm("有未保存的工作台修改。离开并放弃这些修改？"))
        e.preventDefault();
    };
    window.addEventListener("beforeunload", unload);
    window.addEventListener("hr-before-navigate", leave);
    return () => {
      window.removeEventListener("beforeunload", unload);
      window.removeEventListener("hr-before-navigate", leave);
    };
  }, [dirty]);
  useEffect(() => {
    if (tab !== "debug" || runsLoaded) return;
    let cancelled = false;
    api<RunSummary[]>("/agent-workbench/runs")
      .then((r) => {
        if (!cancelled) {
          setRuns(r);
          setRunsLoaded(true);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [tab, runsLoaded]);
  const chooseRun = async (id: string) => {
    setRunId(id);
    setDetail(null);
    setAttempt("all");
    setError("");
    const seq = ++requestId.current;
    if (!id) return;
    try {
      const d = await api<RunDetail>(
        `/agent-workbench/runs/${encodeURIComponent(id)}`,
      );
      if (seq === requestId.current) setDetail(d);
    } catch (e) {
      if (seq === requestId.current) setError(String(e));
    }
  };
  const mutate = async (
    path: string,
    method: string,
    payload: unknown,
    success: string,
  ) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      accept(await api<State>(`/agent-workbench/${path}`, method, payload));
      setMessage(success);
    } catch (e) {
      setError(String(e));
      setConfirm(false);
    } finally {
      setBusy(false);
    }
  };
  if (!state || !draft)
    return (
      <section className="agent-workbench">
        <h2>Agent工作台</h2>
        {error ? (
          <div role="alert">
            {error}
            <button onClick={() => void load()}>重新加载</button>
          </div>
        ) : (
          <p>正在读取流程与设定…</p>
        )}
      </section>
    );
  const diff = changes(state.active, draft),
    selected = draft.roles[role];
  const selectedRules = state.lockedRulesByRole?.[role] ?? state.lockedRules;
  const effectivePromptPreview =
    selectedRules + "Active role: " + role + "\n" + selected.systemPrompt;
  const update = (key: keyof RoleConfig, value: unknown) => {
    setConfirm(false);
    setMessage("");
    setDraft({
      ...draft,
      roles: { ...draft.roles, [role]: { ...selected, [key]: value } },
    });
  };
  const artifacts =
    detail?.artifacts.filter(
      (a) =>
        a.role === role && (attempt === "all" || String(a.attempt) === attempt),
    ) || [];
  const attempts = [
    ...new Set([
      ...(detail?.artifacts
        .filter((a) => a.role === role)
        .map((a) => a.attempt) || []),
      ...(detail?.run.steps
        .filter((s) => s.role === role)
        .map((s) => s.attempt) || []),
    ]),
  ].sort((a, b) => a - b);
  return (
    <section className="agent-workbench">
      <header className="aw-heading">
        <div>
          <span className="aw-eyebrow">流程 · 设定 · 可追溯运行</span>
          <h2>Agent工作台</h2>
          <p>编辑和保存不调用模型。激活后仅用于新运行，已有结果保持原记录。</p>
        </div>
        <span className="aw-version">
          当前生效 v{state.active.version.number}
          <small>
            {state.runtime.mode === "fixture" ? "测试模式" : "实际模型模式"} ·{" "}
            {state.runtime.model || "模型未配置"}
          </small>
        </span>
      </header>
      <div className="aw-tabs" role="tablist" aria-label="工作台页面">
        {[
          ["flow", "流程与设定"],
          ["debug", "运行调试"],
          ["quality", "质量与限制"],
        ].map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      {error && (
        <div className="notice error" role="alert">
          {error}
          <button
            disabled={busy}
            onClick={() => {
              if (!dirty || window.confirm("重新加载将放弃未保存修改，继续？"))
                void load();
            }}
          >
            重新加载设定
          </button>
        </div>
      )}
      {message && (
        <div className="notice" role="status">
          {message}
        </div>
      )}
      {tab === "flow" && (
        <>
          <div className="aw-flow" aria-label="五阶段固定流程">
            {roles.map((r, i) => (
              <div className="aw-node-wrap" key={r}>
                <button
                  className={`aw-node ${role === r ? "selected" : ""}`}
                  aria-pressed={role === r}
                  onClick={() => setRole(r)}
                >
                  <small>
                    0{i + 1} / {r.toUpperCase()}
                  </small>
                  <strong>{names[r]}</strong>
                  <span>{notes[r]}</span>
                </button>
                {i < 4 && (
                  <span className="aw-arrow" aria-hidden="true">
                    →
                  </span>
                )}
              </div>
            ))}
          </div>
          <div className="aw-flow-notes">
            <span>
              ↶ 复核未通过 → 匹配修复：最多 {draft.flow.matchRepairs} 次
            </span>
            <span>
              ↶ 问题校验未通过 → 面试问题修复：最多{" "}
              {draft.flow.interviewRepairs} 次
            </span>
            <span>通过校验 → 代码分级 → 保存建议</span>
          </div>
          <p className="aw-muted">
            固定五阶段流程；JD 需经 HR
            确认，匹配依赖确认标准与已整理简历。可调整有限重试，不支持任意新增节点或拖动连线。
          </p>
          <div className="aw-layout">
            <form
              className="aw-panel"
              onSubmit={(e) => {
                e.preventDefault();
                void mutate(
                  "draft",
                  "PUT",
                  {
                    expectedDraftRevision: state.draftRevision,
                    config: body(draft),
                  },
                  "草稿已保存，尚未生效。",
                );
              }}
            >
              <div className="aw-panel-heading">
                <h3>{names[role]} · 角色设定</h3>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setDraft({
                      ...draft,
                      roles: {
                        ...draft.roles,
                        [role]: structuredClone(state.defaults.roles[role]),
                      },
                    });
                    setConfirm(false);
                  }}
                >
                  恢复此角色默认值
                </button>
              </div>
              <label>
                系统提示与角色任务
                <textarea
                  disabled={busy}
                  aria-label="系统提示与角色任务"
                  value={selected.systemPrompt}
                  onChange={(e) => update("systemPrompt", e.target.value)}
                  required
                  rows={12}
                />
              </label>
              <div className="aw-fields">
                <label>
                  模型 ID
                  <input
                    disabled={busy}
                    value={selected.model || ""}
                    onChange={(e) =>
                      update("model", e.target.value.trim() || null)
                    }
                    placeholder={`默认：${state.runtime.model || "后端配置"}`}
                  />
                </label>
                <label>
                  推理强度
                  <select
                    disabled={busy}
                    value={selected.reasoningEffort}
                    onChange={(e) => update("reasoningEffort", e.target.value)}
                  >
                    {["none", "low", "medium", "high"].map((v) => (
                      <option key={v}>{v}</option>
                    ))}
                  </select>
                </label>
                <label>
                  输出 token 上限
                  <input
                    disabled={busy}
                    type="number"
                    min={1}
                    step={1}
                    required
                    value={selected.maxOutputTokens}
                    onChange={(e) =>
                      update("maxOutputTokens", Number(e.target.value))
                    }
                  />
                </label>
                <label>
                  Temperature（留空使用默认）
                  <input
                    disabled={busy}
                    type="number"
                    min={0}
                    max={2}
                    step={0.1}
                    value={selected.temperature ?? ""}
                    onChange={(e) =>
                      update(
                        "temperature",
                        e.target.value === "" ? null : Number(e.target.value),
                      )
                    }
                  />
                </label>
                <label>
                  超时（秒）
                  <input
                    disabled={busy}
                    type="number"
                    min={1}
                    step={1}
                    required
                    value={selected.timeoutSeconds}
                    onChange={(e) =>
                      update("timeoutSeconds", Number(e.target.value))
                    }
                  />
                </label>
              </div>
              <p className="aw-muted">
                模型参数兼容性取决于所选模型与服务商；保存不是连通性测试。密钥仅在后端环境文件配置。
              </p>
              <details>
                <summary>强制来源与标准护栏 · {names[role]} · 只读</summary>
                <p className="aw-muted">
                  共同护栏适用于所有角色；项目领域、既往经历与核验范围附加规则仅用于逐项匹配和复核证据。
                </p>
                <pre>{selectedRules}</pre>
              </details>
              <details data-testid="effective-prompt-preview">
                <summary>完整生效 system prompt 预览</summary>
                <p className="aw-muted">
                  {dirty
                    ? "包含未保存修改的草稿预览，尚未生效。"
                    : "当前草稿的组合预览；只有明确激活后才用于新运行。"}{" "}
                  上方角色任务可编辑，强制护栏只读。
                </p>
                <pre>{effectivePromptPreview}</pre>
              </details>
              <details>
                <summary>结构化输出要求 · {role}</summary>
                <pre>{json(state.schemas[role])}</pre>
              </details>
              <div className="aw-save">
                <span>
                  {dirty ? "有未保存修改" : "草稿已保存"} · 草稿修订{" "}
                  {state.draftRevision}
                </span>
                <button
                  className="primary"
                  disabled={busy || !dirty}
                  type="submit"
                >
                  保存草稿
                </button>
              </div>
            </form>
            <aside className="aw-panel">
              <h3>流程控制</h3>
              {(["matchRepairs", "interviewRepairs"] as const).map((k) => (
                <label key={k}>
                  {k === "matchRepairs" ? "匹配修复次数" : "面试问题修复次数"}
                  <select
                    disabled={busy}
                    value={draft.flow[k]}
                    onChange={(e) => {
                      setDraft({
                        ...draft,
                        flow: { ...draft.flow, [k]: Number(e.target.value) },
                      });
                      setConfirm(false);
                    }}
                  >
                    {[0, 1, 2].map((n) => (
                      <option key={n} value={n}>
                        {n} 次{n === 0 ? "（不修复）" : ""}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
              <p>修复用尽仍不通过时，保留失败信息，不发布未经校验的结论。</p>
              <h3>激活新版本</h3>
              <p>
                与当前版本相比，{diff.length} 项修改。请先保存，再核对差异。
              </p>
              <button
                className="primary"
                disabled={busy || dirty || diff.length === 0}
                onClick={() => setConfirm(true)}
              >
                查看差异并激活
              </button>
              <details>
                <summary>版本历史（恢复为草稿）</summary>
                {state.history.map((c) => (
                  <div className="aw-history" key={c.version.id}>
                    <strong>v{c.version.number}</strong>
                    <small>
                      {c.version.confirmedAt || "时间未保存"} ·{" "}
                      {c.version.confirmedBy || "操作者未保存"}
                    </small>
                    <button
                      disabled={busy}
                      onClick={() => {
                        if (
                          !dirty ||
                          window.confirm("恢复历史版本将覆盖未保存修改，继续？")
                        )
                          void mutate(
                            "restore",
                            "POST",
                            {
                              versionId: c.version.id,
                              expectedDraftRevision: state.draftRevision,
                            },
                            "历史设定已恢复为草稿；需要单独激活。",
                          );
                      }}
                    >
                      恢复 v{c.version.number} 为草稿
                    </button>
                  </div>
                ))}
              </details>
            </aside>
          </div>
          {confirm && (
            <section className="aw-panel aw-confirm" aria-label="激活确认">
              <h3>核对即将生效的修改</h3>
              <p>
                从 v{state.active.version.number}{" "}
                激活已保存草稿。不会重新分析现有候选人。
              </p>
              {diff.map((d) => (
                <details key={d.label} open>
                  <summary>{d.label}</summary>
                  <div className="aw-diff">
                    <div>
                      <small>当前生效</small>
                      <pre>
                        {typeof d.before === "string"
                          ? d.before
                          : json(d.before)}
                      </pre>
                    </div>
                    <div>
                      <small>已保存草稿</small>
                      <pre>
                        {typeof d.after === "string" ? d.after : json(d.after)}
                      </pre>
                    </div>
                  </div>
                </details>
              ))}
              <div className="aw-save">
                <button onClick={() => setConfirm(false)} disabled={busy}>
                  取消
                </button>
                <button
                  className="primary"
                  disabled={busy || dirty}
                  onClick={() =>
                    void mutate(
                      "activate",
                      "POST",
                      {
                        expectedDraftRevision: state.draftRevision,
                        expectedActiveVersion: state.active.version.id,
                        actor: "本地 HR",
                      },
                      "新版本已激活，仅影响之后开始的运行。",
                    )
                  }
                >
                  确认激活新版本
                </button>
              </div>
            </section>
          )}
        </>
      )}
      {tab === "debug" && (
        <div className="aw-panel">
          <h3>查看已保存运行</h3>
          <p>只读取历史输入、输出与错误；这里不会执行模型。最近 30 次运行。</p>
          <label>
            选择运行
            <select
              aria-label="选择运行"
              value={runId}
              onChange={(e) => void chooseRun(e.target.value)}
            >
              <option value="">
                {runsLoaded
                  ? runs.length
                    ? "请选择运行"
                    : "暂无已保存运行"
                  : "正在加载运行…"}
              </option>
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.createdAt} · {r.candidateNames.join("、") || r.id} ·{" "}
                  {r.status}
                  {!r.hasSnapshot ? " · 未保存设定快照" : ""}
                </option>
              ))}
            </select>
          </label>
          {runId && !detail && !error && <p>正在读取运行记录…</p>}
          {detail && (
            <>
              <p>
                运行 {detail.run.id} · {detail.run.status} · 标准{" "}
                {detail.run.standardsVersion || "未保存"} · HAT{" "}
                {detail.run.hatVersion} · 设定{" "}
                {detail.settings
                  ? `v${detail.settings.version.number}`
                  : "未保存"}
              </p>
              {detail.historicalWarning && (
                <div className="notice">{detail.historicalWarning}</div>
              )}
              {detail.run.error && (
                <div className="notice error">{detail.run.error}</div>
              )}
              <div className="aw-fields">
                <label>
                  阶段
                  <select
                    aria-label="阶段"
                    value={role}
                    onChange={(e) => {
                      setRole(e.target.value as Role);
                      setAttempt("all");
                    }}
                  >
                    {roles.map((r) => (
                      <option key={r} value={r}>
                        {names[r]}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  尝试次数
                  <select
                    aria-label="尝试次数"
                    value={attempt}
                    onChange={(e) => setAttempt(e.target.value)}
                  >
                    <option value="all">全部尝试</option>
                    {attempts.map((a) => (
                      <option key={a} value={a}>
                        第 {a} 次
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <details>
                <summary>该运行的角色提示与参数</summary>
                <pre>
                  {detail.settings
                    ? json({
                        settings: detail.settings.roles[role],
                        promptHash:
                          detail.settings.promptHashes?.[role] ?? "未保存",
                        effectiveSystemPrompt:
                          detail.settings.effectivePrompts?.[role] ?? "未保存",
                        runtime: detail.settings.runtime,
                      })
                    : "该历史运行未保存设定快照，无法还原当时提示与参数。"}
                </pre>
              </details>
              {detail.run.steps
                .filter(
                  (s) =>
                    s.role === role &&
                    (attempt === "all" || String(s.attempt) === attempt),
                )
                .map((s) => (
                  <p key={s.id}>
                    第 {s.attempt} 次 · {s.status} · {s.summary}
                  </p>
                ))}
              {artifacts.length ? (
                artifacts.map((a, i) => (
                  <details key={`${a.reference}-${i}`} open>
                    <summary>
                      {artifactNames[a.kind] || a.kind} · 第 {a.attempt} 次
                    </summary>
                    <small>{a.reference}</small>
                    <pre>{json(a.data)}</pre>
                  </details>
                ))
              ) : (
                <p>
                  此阶段的输入、输出或错误明细未保存；不能从当前配置推断历史内容。
                </p>
              )}
              {detail.truncated && (
                <div className="notice">记录过大，当前仅展示部分内容。</div>
              )}
            </>
          )}
        </div>
      )}
      {tab === "quality" && (
        <div className="aw-quality">
          <article className="aw-panel">
            <h3>哪些检查由代码保证</h3>
            <p>
              来源 ID 与引用原文、共同 HAT
              与标准版本、结构化输出、字段归属及有限重试由代码校验。分级使用已复核的逐项证据，不按学校声望加分。
            </p>
            <p>
              修改角色提示不能解除这些护栏。缺失证据保持待确认，建议分级与 HR
              决定分别保存。
            </p>
          </article>
          <article className="aw-panel">
            <h3>仍然存在的限制</h3>
            <p>
              五个角色不代表五个独立模型。同一模型担任复核时可能重复原判断的偏差；复核也不等于事实已被外部证实。
            </p>
            <p>
              结构与引用正确不保证概括全面。OCR、模糊履历和过长输入仍会影响结果；需要
              HR 回看原文。修复次数有限，失败应明确保留。
            </p>
          </article>
          <article className="aw-panel">
            <h3>如何判断修改是否更好</h3>
            <p>
              先核对版本差异，再在正常分析流程中运行。比较同一资料的证据覆盖、是否夸大经历、未决点是否清晰，以及三个问题能否分别确认关键疑问。
            </p>
            <p>
              本工作台不自动运行评测或声称质量提升。旧运行缺少快照时显示“未保存”。
            </p>
          </article>
          <article className="aw-panel">
            <h3>当前运行边界</h3>
            <p>
              服务商：{state.runtime.provider} · 默认模型：
              {state.runtime.model || "未配置"}
            </p>
            <p>
              默认输出上限 {state.runtime.maxOutputTokens} token · 输入上限{" "}
              {state.runtime.maxInputBytes ?? "未设置"} 字节 · 调用预算{" "}
              {state.runtime.maxCalls ?? "未设置"} 次 · 默认超时{" "}
              {state.runtime.timeoutSeconds} 秒
            </p>
            <p>本地单用户工具；最终筛选、联系与录用由 HR 决定。</p>
          </article>
        </div>
      )}
    </section>
  );
}

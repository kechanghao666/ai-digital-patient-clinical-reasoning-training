import { useEffect, useRef, useState } from "react";
import { ApiError, enc, get, mutate, type Identity } from "../api";
import { navigate, setUnsavedGuard } from "../App";
import type { DifferentialDraft, SessionDetail, TrainingEvent } from "../types";
import { CLINICAL_STATE, ORDER_STATE, patientLine, type CatalogExam } from "../types";
import { Loading, Msg, Panel, Pick, Tabs, useAction, useLoad } from "../ui";
import { Replay, Review } from "./Review";

type Tab = "intake" | "reasoning" | "treatment" | "review";
interface DdxDraft { support: string; oppose: string; events: string[]; }
type Run = ReturnType<typeof useAction>["run"];
const FALLBACK = "我不太确定您的问题，能否换一种方式询问？";
const NOTE_TEMPLATE = "主诉：\n现病史：\n既往史：\n个人史/家族史：\n辅助检查：\n初步诊断：\n鉴别诊断：\n处理计划：";

export function evidenceLabel(e: TrainingEvent): string {
  if (e.action === "ask") return `#${e.seq} 问：${e.intent}`;
  const exams = e.result.exam_results || [];
  if (exams.length) return `#${e.seq} 回报：${exams.map((x) => x.name).join("、")}`;
  return `#${e.seq} 病情变化`;
}

function drafts(detail: SessionDetail) {
  const saved = detail.state.submissions;
  const ddx: Record<string, DdxDraft> = {};
  for (const c of saved.differential || []) {
    ddx[c.candidate_id] = { support: c.supporting_evidence.join("、"), oppose: c.opposing_evidence.join("、"), events: [...c.evidence_event_ids] };
  }
  return { notes: detail.state.case_notes || "", diagnosis: saved.diagnosis || "", ddx };
}
const key = (d: Record<string, DdxDraft>) =>
  JSON.stringify(Object.keys(d).sort().map((k) => [k, d[k].support.trim(), d[k].oppose.trim(), [...d[k].events].sort()]));

export function Training({ sessionId, identity }: { sessionId: string; identity: Identity }) {
  const { data, error, reload, setData } = useLoad(() => get<SessionDetail>(`/api/sessions/${enc(sessionId)}`), [sessionId]);
  const [tab, setTab] = useState<Tab | null>(null);
  const [notes, setNotes] = useState("");
  const [diagnosis, setDiagnosis] = useState("");
  const [ddx, setDdx] = useState<Record<string, DdxDraft>>({});
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const { busy, run } = useAction();

  useEffect(() => {
    if (data && loadedFor !== data.state.session_id) {
      const d = drafts(data);
      setNotes(d.notes); setDiagnosis(d.diagnosis); setDdx(d.ddx);
      setLoadedFor(data.state.session_id);
      setTab((t) => t ?? (data.state.sealed ? "review" : "intake"));
    }
  }, [data, loadedFor]);

  const saved = data ? drafts(data) : null;
  const sealed = !!data?.state.sealed;
  const dirty = {
    notes: !!saved && !sealed && notes !== saved.notes,
    diagnosis: !!saved && !sealed && diagnosis.trim() !== saved.diagnosis.trim(),
    ddx: !!saved && !sealed && key(ddx) !== key(saved.ddx),
  };
  const pending = [dirty.notes && "病历", dirty.ddx && "鉴别诊断", dirty.diagnosis && "诊断"].filter(Boolean) as string[];
  useEffect(() => {
    setUnsavedGuard(() => pending.length === 0 || window.confirm(`${pending.join("、")}尚未保存，确定离开？`));
    const before = (e: BeforeUnloadEvent) => { if (pending.length) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", before);
    return () => { setUnsavedGuard(null); window.removeEventListener("beforeunload", before); };
  }, [pending.join()]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!data || !saved) return <Loading error={error} onRetry={reload} />;
  const { state, options } = data;
  const base = `/api/sessions/${enc(sessionId)}`;
  const act = (k: string, action: string, payload: Record<string, unknown>, ok?: string) =>
    run(k, async () => { await mutate(`${sessionId}:${action}`, "POST", `${base}/action`, { action, payload }); await reload(); }, ok);
  const evidence = state.events
    .filter((e) => e.action === "ask" || (e.result.exam_results || []).length || (e.result.clinical_updates || []).length)
    .map((e) => ({ id: e.event_id, label: evidenceLabel(e) }));

  return (
    <div className="stack">
      <Monitor data={data} sealed={sealed} />
      {state.clinical_updates.map((u, i) => <div key={i} className="alert-line" role="alert">病情变化：{u}</div>)}

      <section className="panel">
        <Tabs<Tab> value={tab || "intake"} onChange={setTab} items={[
          { id: "intake", label: "问诊与检查" },
          { id: "reasoning", label: "病历与诊断", mark: pending.length > 0 },
          { id: "treatment", label: "处置与提交" },
          { id: "review", label: "评分与复盘" },
        ]} />
      </section>

      {tab === "intake" && <Intake data={data} sealed={sealed} busy={busy} run={run} reload={reload} act={act} sessionId={sessionId} />}

      {tab === "reasoning" && (
        <div className="work half">
          <Panel title="病历记录" actions={<>
            {dirty.notes && <span className="faint">未保存</span>}
            {!sealed && !notes && <button className="btn" onClick={() => setNotes(NOTE_TEMPLATE)}>插入模板</button>}
            <button className="btn btn-primary" disabled={sealed || !dirty.notes || busy !== null} onClick={() => act("notes", "update_notes", { notes }, "病历已保存")}>保存</button></>}>
            <textarea className="textarea" style={{ minHeight: 420, fontSize: 13.5 }} value={notes} disabled={sealed} onChange={(e) => setNotes(e.target.value)} aria-label="病历记录" />
          </Panel>
          <div className="stack">
            <Panel title="鉴别诊断" actions={<>
              {dirty.ddx && <span className="faint">未提交</span>}
              <button className="btn btn-primary" disabled={sealed || !dirty.ddx || busy !== null}
                onClick={() => run("ddx", async () => {
                  const candidates: DifferentialDraft[] = Object.entries(ddx).map(([id, d]) => ({
                    candidate_id: id, supporting_evidence: d.support.trim() ? [d.support.trim()] : [],
                    opposing_evidence: d.oppose.trim() ? [d.oppose.trim()] : [], evidence_event_ids: d.events }));
                  await mutate(`${sessionId}:ddx`, "POST", `${base}/differential`, { candidates });
                  await reload();
                }, "鉴别诊断已提交")}>提交</button></>}>
              <div className="stack">
                {options.differentials.map((o) => {
                  const d = ddx[o.candidate_id];
                  const set = (patch: Partial<DdxDraft>) => setDdx({ ...ddx, [o.candidate_id]: { ...d, ...patch } });
                  return (
                    <div key={o.candidate_id} className={d ? "ddx" : ""}>
                      <label className="check"><input type="checkbox" disabled={sealed} checked={!!d}
                        onChange={(e) => setDdx((all) => { const next = { ...all }; if (e.target.checked) next[o.candidate_id] = { support: "", oppose: "", events: [] }; else delete next[o.candidate_id]; return next; })} />
                        <span style={{ fontWeight: d ? 600 : 400 }}>{o.label}</span></label>
                      {d && <>
                        <div className="form-grid">
                          <label className="field"><span>支持点</span><input className="input" disabled={sealed} value={d.support} onChange={(e) => set({ support: e.target.value })} /></label>
                          <label className="field"><span>不支持点</span><input className="input" disabled={sealed} value={d.oppose} onChange={(e) => set({ oppose: e.target.value })} /></label>
                        </div>
                        <Pick label="依据记录" items={evidence} value={d.events} disabled={sealed} onChange={(events) => set({ events })} />
                      </>}
                    </div>
                  );
                })}
                <p className="faint">勾选某项后，填写支持点、不支持点，并选出对应的问诊或检查记录。</p>
              </div>
            </Panel>
            <Panel title="诊断" actions={<>
              {dirty.diagnosis && <span className="faint">未提交</span>}
              <button className="btn btn-primary" disabled={sealed || !diagnosis.trim() || !dirty.diagnosis || busy !== null}
                onClick={() => run("dx", async () => { await mutate(`${sessionId}:dx`, "POST", `${base}/diagnosis`, { diagnosis: diagnosis.trim() }); await reload(); }, "诊断已提交")}>提交</button></>}>
              <input className="input" disabled={sealed} value={diagnosis} onChange={(e) => setDiagnosis(e.target.value)} placeholder="主要诊断" aria-label="主要诊断" />
            </Panel>
          </div>
        </div>
      )}

      {tab === "treatment" && <Treatment data={data} sealed={sealed} busy={busy} run={run} reload={reload} pending={pending}
        sessionId={sessionId} onSealed={(next) => { setData(next); setTab("review"); }} />}

      {tab === "review" && (sealed ? <Review data={data} identity={identity} />
        : <div className="stack"><Msg>提交训练后生成评分。下面是目前的操作记录。</Msg><Panel title="操作记录"><Replay data={data} /></Panel></div>)}
    </div>
  );
}

// One ECG-like complex repeated; the strip sweeps continuously and turns red, faster, when the patient deteriorates.
const BEAT = "h18 l4 -3 l4 3 h6 l3 4 l4 -30 l4 34 l3 -8 h10 l6 -6 l6 6 h22";
const TRACE = `M0 26 ${Array.from({ length: 12 }, () => BEAT).join(" ")}`;

function Monitor({ data, sealed }: { data: SessionDetail; sealed: boolean }) {
  const { state } = data;
  const clinical = CLINICAL_STATE[state.clinical_state] || { label: state.clinical_state, tone: "ok" };
  const trace = sealed ? "idle" : clinical.tone === "bad" ? "bad" : "";
  return (
    <section className="monitor" aria-label="患者信息">
      <div className="who">
        <div className="avatar" aria-hidden="true">{data.case.patient?.sex || "患"}</div>
        <div style={{ minWidth: 0 }}>
          <div className="pt">模拟患者 <span>{patientLine(data.case)}</span></div>
          <div className="facts">
            <span>主诉 <b>{data.case.chief_complaint}</b></span><span>{data.case.specialty}</span>
            <span>病例 {data.case.public_id} v{data.case.version}</span><span>训练号 {state.session_id.slice(0, 8)}</span>
          </div>
        </div>
      </div>
      <div className={`trace ${trace}`} aria-hidden="true">
        <svg viewBox="0 0 1080 46" preserveAspectRatio="none"><path d={TRACE} /></svg>
      </div>
      <div className="readouts">
        <div className="readout cyan"><span>模拟时间</span><b>{state.sim_time_minutes}<small>分</small></b></div>
        <div className="readout amber"><span>检查费用</span><b>{state.exam_cost_total ?? 0}<small>元</small></b></div>
        <div className="readout green"><span>已获病史</span><b>{state.disclosed_fact_count}<small>条</small></b></div>
        <div className={`readout ${sealed ? "" : clinical.tone}`}><span>{sealed ? "训练" : "病情"}</span><b style={{ fontSize: 18 }}>{sealed ? "已提交" : clinical.label}</b></div>
      </div>
    </section>
  );
}

const lead = (m: number) => m === 0 ? "即时" : m < 60 ? `${m}分钟` : m < 1440 ? `${+(m / 60).toFixed(1)}小时` : `${Math.round(m / 1440)}天`;

function ExamCatalog({ exams, active, disabled, onOrder }: {
  exams: CatalogExam[]; active: Set<string>; disabled: boolean; onOrder: (exam: CatalogExam) => void;
}) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("全部");
  const categories = ["全部", ...Array.from(new Set(exams.map((e) => e.category)))];
  const q = query.trim();
  const shown = exams.filter((e) => (category === "全部" || e.category === category) && (!q || e.name.includes(q)));
  return (
    <>
      <div className="catalog-tools">
        <input className="input" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={`检索 ${exams.length} 项检查，如 血常规`} aria-label="检索检查" />
      </div>
      <div className="chips" role="group" aria-label="检查分类">
        {categories.map((c) => <button key={c} aria-pressed={category === c} onClick={() => setCategory(c)}>{c}</button>)}
      </div>
      <div className="catalog-list">
        {shown.length === 0 ? <div className="empty">没有匹配的检查，换个名称试试</div> : shown.map((e) => (
          <div key={e.exam_id} className="cat-row">
            <div style={{ minWidth: 0 }}><span className="cat-name">{e.name}</span><span className="cat-sub">{e.category}</span></div>
            <span className="cost">¥{e.cost}</span>
            <span className="lead">{lead(e.minutes)}</span>
            {active.has(e.exam_id) ? <span className="done">已开</span>
              : <button className="btn btn-sm" disabled={disabled} onClick={() => onOrder(e)}>开单</button>}
          </div>
        ))}
      </div>
    </>
  );
}

function Intake({ data, sealed, busy, run, reload, act, sessionId }: {
  data: SessionDetail; sealed: boolean; busy: string | null; run: Run; reload: () => Promise<void>;
  act: (k: string, action: string, payload: Record<string, unknown>, ok?: string) => Promise<unknown>; sessionId: string;
}) {
  const { state, options, history_coverage: coverage } = data;
  const [question, setQuestion] = useState("");
  const [askError, setAskError] = useState<{ reason: string; unconfirmed: boolean } | null>(null);
  const [minutes, setMinutes] = useState(5);
  const active = new Set(state.orders.filter((o) => o.state === "pending" || o.state === "ready").map((o) => o.catalog_id || ""));
  const [latency, setLatency] = useState<number | null>(null);
  const log = useRef<HTMLDivElement>(null);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [state.events.length, busy]);
  const ordered = state.events.filter((e) => e.action === "order_exam");
  const ask = () => {
    const q = question.trim();
    if (!q) return;
    setAskError(null);
    run("ask", async () => {
      const t = performance.now();
      try {
        await mutate(`${sessionId}:ask`, "POST", `/api/sessions/${enc(sessionId)}/ask`, { question: q });
      } catch (e) {
        // Keep the draft and the rest of the training state. `mutate` keeps the same request id while the
        // question is unchanged, so retrying the same text can never register a second turn; only an edited
        // question gets a new id. A network-level failure carries no response, so we cannot tell whether the
        // turn reached the server and must not claim it was not recorded. Anything we did parse is the
        // server's own verdict.
        const api = e instanceof ApiError ? e : null;
        setAskError(api && api.status !== 0
          ? { reason: api.message, unconfirmed: false }
          : { reason: "", unconfirmed: true });
        return;
      }
      setLatency(performance.now() - t);
      setQuestion("");
      await reload();
    });
  };
  return (
    <div className="work">
      <section className="panel dialog" aria-label="问诊记录">
        <div className="panel-head"><h3>问诊</h3><span className="faint" title={latency !== null ? `应答 ${(latency / 1000).toFixed(1)} 秒` : undefined}>已问 {state.events.filter((e) => e.action === "ask").length} 项</span></div>
        <div className="coverage" title={coverage.notice}>
          <span>问诊结构 {coverage.covered}/{coverage.total}</span>
          {coverage.domains.map((d) => <span key={d.domain} className={d.asked ? "on" : "off"} title={d.asked ? "" : d.hint}>{d.label}</span>)}
        </div>
        <div className="dialog-log" ref={log} aria-live="polite">
          {state.events.length === 0 && <div className="empty">尚无记录。从患者的主诉问起。</div>}
          {state.events.map((e) => <Line key={e.event_id} event={e} examName={e.action === "order_exam" ? state.orders[ordered.indexOf(e)]?.exam_name : undefined} />)}
          {busy === "ask" && <div className="turn patient"><span className="who">患者</span><span className="text faint">…</span></div>}
        </div>
        {askError && <Msg tone="warn">{askError.unconfirmed
          ? "未收到本次提问的结果。输入已保留，请先原样重试以确认记录。"
          : `本次提问未取得有效回答（${askError.reason}）。输入已保留，请先原样重试以确认记录。`}</Msg>}
        <form className="composer" onSubmit={(e) => { e.preventDefault(); ask(); }}>
          <input className="input" placeholder={sealed ? "已提交，只读" : "输入问诊内容，回车发送"} value={question} disabled={sealed}
            onChange={(e) => { setQuestion(e.target.value); setAskError(null); }} aria-label="问诊内容" autoComplete="off" />
          <button className="btn btn-primary" style={{ height: 34 }} disabled={sealed || !question.trim() || busy !== null}>发送</button>
        </form>
      </section>
      <div className="stack">
        <Panel title="检查申请" flush actions={<span className="faint">按需开立，费用与等待时间计入评分</span>}>
          <ExamCatalog exams={options.exams} active={active} disabled={sealed || busy !== null}
            onOrder={(e) => act("order", "order_exam", { exam_id: e.exam_id }, `已开单：${e.name}`)} />
          <div className="wait-bar">
            <span className="small muted">等待</span>
            <select className="select" value={minutes} disabled={sealed} onChange={(e) => setMinutes(Number(e.target.value))} aria-label="等待分钟">
              {[5, 10, 15, 30, 60, 120].map((m) => <option key={m} value={m}>{m} 分钟</option>)}
            </select>
            <button className="btn" disabled={sealed || busy !== null} onClick={() => act("wait", "wait", { minutes }, `时间推进 ${minutes} 分钟`)}>推进时间</button>
          </div>
        </Panel>
        <Panel title={`检查结果（${state.orders.length}）`} flush>
          {state.orders.length === 0 ? <div className="empty">还没有开立检查。根据问诊结果，从上方目录选择</div> : state.orders.map((o, i) => (
            <div key={i} className={`order ${o.state}`}>
              <div className="spread"><b style={{ fontWeight: 600 }}>{o.exam_name}</b>
                <span className={`status ${o.state === "ready" ? "ok" : o.state === "pending" ? "warn" : "bad"}`}>{ORDER_STATE[o.state] || o.state}</span></div>
              <div className="meta">开单 {o.ordered_sim_time} 分　回报 {o.return_sim_time} 分　¥{o.cost ?? "—"}</div>
              {o.result && <div className="result">{o.result}</div>}
            </div>))}
        </Panel>
      </div>
    </div>
  );
}

function Line({ event: e, examName }: { event: TrainingEvent; examName?: string }) {
  if (e.action === "ask") {
    const answer = e.result.answer || "";
    return <>
      <div className="turn doctor"><span className="who">医生</span><span>{e.intent}<span className="meta">#{e.seq}　{e.sim_time}分</span></span></div>
      <div className={`turn patient${answer === FALLBACK ? " fallback" : ""}`}><span className="who">患者</span><span className="text">{answer}</span></div>
    </>;
  }
  const lines: [string, boolean?][] = [];
  if (e.action === "order_exam") lines.push([`${e.sim_time}分 开单：${examName || "检查"}`]);
  if (e.action === "wait") lines.push([`时间推进 ${e.result.advanced_minutes ?? 0} 分钟`]);
  for (const r of e.result.exam_results || []) lines.push([`回报：${r.name}`]);
  for (const u of e.result.clinical_updates || []) lines.push([`病情变化：${u}`, true]);
  const named: Record<string, string> = { execute_treatment: "执行处置", plan_treatment: "记录处置计划", submit_diagnosis: "提交诊断", submit_differential: "提交鉴别诊断", seal: "提交训练" };
  if (named[e.action]) lines.push([named[e.action]]);
  return <>{lines.map(([t, alert], i) => <div key={i} className={`sys-line${alert ? " alert" : ""}`}>{t}</div>)}</>;
}

function Treatment({ data, sealed, busy, run, reload, pending, sessionId, onSealed }: {
  data: SessionDetail; sealed: boolean; busy: string | null; run: Run; reload: () => Promise<void>; pending: string[];
  sessionId: string; onSealed: (next: SessionDetail) => void;
}) {
  const { state, options } = data;
  const [choice, setChoice] = useState(options.treatments[0]?.treatment_id || "");
  const [confirm, setConfirm] = useState(false);
  useEffect(() => setConfirm(false), [choice]);
  const base = `/api/sessions/${enc(sessionId)}`;
  const submitted = state.submissions.treatment;
  const executed = state.events.filter((e) => e.action === "execute_treatment").length;
  const submit = (execute: boolean) => run("treat", async () => {
    await mutate(`${sessionId}:treat`, "POST", `${base}/treatment`, { treatment_id: choice, confirm_execution: execute });
    setConfirm(false);
    await reload();
  }, execute ? "已执行" : "已记录计划");
  const checks: [string, boolean][] = [
    ["问诊", state.events.some((e) => e.action === "ask")],
    ["检查", state.orders.length > 0],
    ["病历已保存", !!state.case_notes && !pending.includes("病历")],
    ["鉴别诊断已提交", state.submissions.differential.length > 0 && !pending.includes("鉴别诊断")],
    ["诊断已提交", !!state.submissions.diagnosis && !pending.includes("诊断")],
    ["处置已执行", executed > 0],
  ];
  return (
    <div className="work half">
      <Panel title="处置医嘱">
        <div className="stack">
          <table className="table"><tbody>{options.treatments.map((t) => (
            <tr key={t.treatment_id}><td style={{ width: 28 }}><input type="radio" name="tx" aria-label={t.name} disabled={sealed} checked={choice === t.treatment_id} onChange={() => setChoice(t.treatment_id)} /></td>
              <td onClick={() => !sealed && setChoice(t.treatment_id)} style={{ cursor: sealed ? "default" : "pointer" }}>{t.name}</td></tr>))}</tbody></table>
          <label className="check"><input type="checkbox" disabled={sealed} checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />确认执行所选处置（执行后影响病情，不可撤销）</label>
          <div className="row">
            <button className="btn" disabled={sealed || !choice || busy !== null} onClick={() => submit(false)}>仅记录计划</button>
            <button className="btn btn-primary" disabled={sealed || !choice || !confirm || busy !== null} onClick={() => submit(true)}>执行</button>
          </div>
          {submitted && <p className="small muted">最近：{submitted.treatment_name || submitted.treatment_id}（{submitted.confirm_execution ? "已执行" : "仅计划"}）</p>}
        </div>
      </Panel>
      <Panel title="提交训练">
        <div className="stack">
          <table className="table"><tbody>{checks.map(([label, done]) => (
            <tr key={label}><td>{label}</td><td className="num"><span className={`status ${done ? "ok" : ""}`}>{done ? "完成" : "未完成"}</span></td></tr>))}</tbody></table>
          {pending.length > 0 && <Msg tone="warn">{pending.join("、")}尚未保存，请先保存。</Msg>}
          <div className="row">
            <button className="btn btn-primary" disabled={sealed || pending.length > 0 || busy !== null}
              onClick={() => window.confirm("提交后不能再修改，确认提交？") && run("seal", async () => {
                await mutate(`${sessionId}:seal`, "POST", `${base}/seal`, {});
                onSealed(await get<SessionDetail>(base));
              }, "已提交，评分已生成")}>提交并评分</button>
            <button className="btn" onClick={() => navigate("/trainings")}>返回</button>
          </div>
        </div>
      </Panel>
    </div>
  );
}

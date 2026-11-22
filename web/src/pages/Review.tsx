import { useMemo, useState } from "react";
import { enc, get, getOptional, mutate, type Identity } from "../api";
import type { ScoreSnapshot, SessionDetail, TrainingEvent } from "../types";
import { STAGES } from "../types";
import { Bar, fmt, Loading, Msg, Panel, toneFor, useAction, useLoad } from "../ui";

interface Feedback {
  source: string; review_status: string; notice: string; review_revision?: number;
  items: { stage: string; comment: string; improvement?: string; basis_ids: string[] }[];
  teaching_basis: { basis_id: string; title: string; sources: string[]; revision: number | null }[];
  basis_provenance?: string; basis_coverage?: { missing_clinical_stages?: string[] };
}
interface Adjudication { revision: number; reviewed_score: ScoreSnapshot; notice: string; }

export function Review({ data, identity }: { data: SessionDetail; identity: Identity }) {
  const sid = data.state.session_id;
  const base = `/api/sessions/${enc(sid)}`;
  const { data: loaded, error, reload } = useLoad(async () => {
    const [score, adjudication, feedback] = await Promise.all([
      get<ScoreSnapshot>(`${base}/score`), getOptional<Adjudication>(`${base}/score-review`),
      getOptional<Feedback>(`${base}/feedback`).catch(() => null),
    ]);
    return { score, adjudication, feedback };
  }, [sid]);
  const { busy, run } = useAction();
  const [generated, setGenerated] = useState<Feedback | null>(null);
  if (!loaded) return <Loading error={error} onRetry={reload} />;
  const { score, adjudication } = loaded;
  const feedback = generated || loaded.feedback;
  const stages = STAGES.map((stage) => {
    const items = score.items.filter((i) => i.stage === stage);
    const got = items.reduce((s, i) => s + i.score, 0), max = items.reduce((s, i) => s + i.max_score, 0);
    return { stage, got, max, ratio: max ? got / max : null };
  });
  const coverage = data.history_coverage;
  return (
    <div className="stack">
      <Panel title="临床思维评分" actions={score.status === "needs_review" ? <span className="status warn">部分项目待教师复核</span> : score.status === "failed" ? <span className="status bad">评分失败</span> : <span className="status ok">已评分</span>}>
        <div className="score-top">
          <div className="score-total"><b>{fmt(score.total_score)}</b> <span>/ {fmt(score.max_score)}</span>
            <div className="faint">规则评分{adjudication ? `　教师裁定 ${fmt(adjudication.reviewed_score.total_score)}` : ""}</div></div>
          <div>{stages.filter((s) => s.max > 0).map((s) => (
            <div key={s.stage} className="stage"><span>{s.stage}</span><Bar value={s.ratio || 0} tone={toneFor(s.ratio)} /><span className="num faint" style={{ textAlign: "right" }}>{fmt(s.got)} / {fmt(s.max)}</span></div>))}</div>
        </div>
      </Panel>
      <Panel title="扣分点与改进建议" flush>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>环节</th><th className="num">得分</th><th>说明</th><th>改进建议</th></tr></thead>
          <tbody>{score.items.filter((i) => i.score < i.max_score).map((i) => (
            <tr key={i.item_id}>
              <td style={{ whiteSpace: "nowrap" }}>{i.stage}</td>
              <td className="num"><span className={`status ${i.score >= i.max_score ? "ok" : i.score > 0 ? "warn" : "bad"}`}>{fmt(i.score)}/{fmt(i.max_score)}</span></td>
              <td>{i.reason}</td><td className="muted">{i.improvement || "—"}</td>
            </tr>))}</tbody>
        </table></div>
      </Panel>
      <div className="work half">
        <Panel title={`问诊结构 ${coverage.covered}/${coverage.total}`}>
          <div className="stack small">
            <div>{coverage.domains.map((d) => <span key={d.domain} style={{ marginRight: 12, color: d.asked ? "var(--ok)" : "var(--ink-3)" }}>{d.asked ? "✓" : "○"} {d.label}</span>)}</div>
            <div className="muted">现病史：{coverage.hpi_elements.map((e) => `${e.asked ? "✓" : "○"}${e.label}`).join("  ")}</div>
            {coverage.domains.filter((d) => !d.asked).map((d) => <div key={d.domain}><b style={{ fontWeight: 600 }}>{d.label}</b>：<span className="muted">{d.hint}</span></div>)}
          </div>
        </Panel>
        <Panel title="教师裁定">
          {adjudication ? (
            <div className="stack small">
              <div>第 {adjudication.revision} 版：{fmt(adjudication.reviewed_score.total_score)} / {fmt(adjudication.reviewed_score.max_score)}（原规则评分保留）</div>
              {adjudication.reviewed_score.items.map((i) => <div key={i.item_id}><b style={{ fontWeight: 600 }}>{i.stage}</b> {fmt(i.score)}/{fmt(i.max_score)} <span className="muted">{i.reason}</span></div>)}
            </div>
          ) : <p className="small muted">暂无。对评分有疑问可将训练编号 <span className="mono">{sid.slice(0, 8)}</span> 告知老师。</p>}
          <details className="fold" style={{ marginTop: 10 }}><summary>复核所需信息</summary>
            <div className="mono small">账号：{identity.user.username}<br />账号编号：{identity.user.id}<br />训练编号：{sid}</div></details>
        </Panel>
      </div>
      <Panel title="分环节反馈" actions={<>
        <button className="btn" disabled={busy !== null} onClick={() => { setGenerated(null); reload(); }}>刷新</button>
        <button className="btn btn-primary" disabled={busy !== null || (!!feedback && feedback.source !== "nexent")}
          onClick={() => run("fb", async () => setGenerated(await mutate<Feedback>(`${sid}:feedback`, "POST", `${base}/feedback`, {})), "反馈已生成")}>
          {busy === "fb" ? "生成中…" : "生成反馈"}</button></>}>
        {!feedback && <p className="small muted">尚未生成。</p>}
        {feedback?.source === "nexent" && <Msg tone="warn">模型反馈已生成，教师复核后可见。</Msg>}
        {feedback && feedback.source !== "nexent" && <FeedbackView feedback={feedback} />}
      </Panel>
      <Panel title="操作回放"><Replay data={data} /></Panel>
    </div>
  );
}

function FeedbackView({ feedback }: { feedback: Feedback }) {
  const basis = Object.fromEntries(feedback.teaching_basis.map((b) => [b.basis_id, b]));
  return (
    <div className="stack">
      {feedback.review_revision ? <Msg tone="ok">教师复核第 {feedback.review_revision} 版</Msg> : null}
      {feedback.basis_coverage?.missing_clinical_stages?.length ? <Msg tone="warn">未检索到依据的环节：{feedback.basis_coverage.missing_clinical_stages.join("、")}</Msg> : null}
      <table className="table"><tbody>{feedback.items.map((item) => (
        <tr key={item.stage}>
          <td style={{ width: 80, fontWeight: 600 }}>{item.stage}</td>
          <td><div>{item.comment.replace(/（已找到符合条件的事件证据）/g, "")}</div>
            {item.improvement && !item.improvement.startsWith("保持当前做法") && <div className="muted">建议：{item.improvement}</div>}
            {item.basis_ids.filter((id) => !(basis[id]?.sources || []).some((s) => s.includes("项目自拟"))).length > 0 && <div className="faint">依据：{item.basis_ids.filter((id) => !(basis[id]?.sources || []).some((s) => s.includes("项目自拟"))).map((id) => {
              const b = basis[id];
              return b ? `${b.title}${b.sources.length ? `（${b.sources.join("；")}）` : ""}` : id;
            }).join("；")}</div>}</td>
        </tr>))}</tbody></table>
      <p className="faint">{feedback.notice}</p>
    </div>
  );
}

const ACTION: Record<string, string> = {
  ask: "问诊", order_exam: "开单", wait: "时间推进", submit_differential: "鉴别诊断", submit_diagnosis: "诊断",
  plan_treatment: "处置计划", execute_treatment: "执行处置", update_notes: "保存病历", seal: "提交",
};

export function Replay({ data }: { data: SessionDetail }) {
  const [filter, setFilter] = useState("");
  const labels = useMemo(() => ({
    ddx: Object.fromEntries(data.options.differentials.map((d) => [d.candidate_id, d.label])),
    tx: Object.fromEntries(data.options.treatments.map((t) => [t.treatment_id, t.name])),
  }), [data]);
  if (!data.state.events.length) return <p className="small muted">尚无记录</p>;
  const kinds = [...new Set(data.state.events.map((e) => e.action))];
  const events = data.state.events.filter((e) => !filter || e.action === filter);
  const ordered = data.state.events.filter((e) => e.action === "order_exam");
  return (
    <div className="stack">
      <select className="select" style={{ width: 160 }} value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="筛选操作">
        <option value="">全部操作（{data.state.events.length}）</option>
        {kinds.map((k) => <option key={k} value={k}>{ACTION[k] || k}</option>)}
      </select>
      <div className="timeline">{events.map((e) => <Item key={e.event_id} event={e} labels={labels}
        examName={e.action === "order_exam" ? data.state.orders[ordered.indexOf(e)]?.exam_name : undefined} />)}</div>
    </div>
  );
}

function Item({ event: e, labels, examName }: { event: TrainingEvent; labels: { ddx: Record<string, string>; tx: Record<string, string> }; examName?: string }) {
  const r = e.result;
  return (
    <div className="item">
      <div className="when">#{e.seq}　{e.sim_time}分</div>
      <div className="stack" style={{ gap: 1 }}>
        {e.action === "ask" ? <><span><span className="faint">问 </span>{e.intent}</span><span className="muted"><span className="faint">答 </span>{r.answer}</span></>
          : <b style={{ fontWeight: 600 }}>{ACTION[e.action] || e.action}{examName ? `：${examName}` : ""}</b>}
        {r.advanced_minutes !== undefined && <span className="muted">推进 {r.advanced_minutes} 分钟</span>}
        {(r.exam_results || []).map((x, i) => <span key={i}>{x.name}：<span className="muted">{x.result}</span></span>)}
        {r.diagnosis && <span>{r.diagnosis}</span>}
        {(r.candidates || []).map((c) => <span key={c.candidate_id}>{labels.ddx[c.candidate_id] || "候选"}：<span className="muted">支持 {c.supporting_evidence.join("；") || "—"}；不支持 {c.opposing_evidence.join("；") || "—"}</span></span>)}
        {r.treatment && <span>{labels.tx[r.treatment.treatment_id] || "处置"}（{r.executed ? "已执行" : "计划"}）</span>}
        {(r.clinical_updates || []).map((u, i) => <span key={i} style={{ color: "var(--bad)" }}>病情变化：{u}</span>)}
      </div>
    </div>
  );
}

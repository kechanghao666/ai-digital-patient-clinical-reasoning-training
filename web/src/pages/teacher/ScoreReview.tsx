import { useEffect, useState, type FormEvent } from "react";
import { ApiError, enc, get, mutate } from "../../api";
import { navigate } from "../../App";
import type { ScoreSnapshot } from "../../types";
import { Pick, JsonView, Msg, Panel, useAction } from "../../ui";

export interface Digest { event_id: string; seq: number; action: string; sim_time: number; question?: string; answer?: string; exam?: string; [k: string]: unknown; }
interface Record_ {
  session_id: string; owner_id: string; case: { title: string; public_id: string; version: number };
  original_score: ScoreSnapshot; reviews: { revision: number; snapshot: ScoreSnapshot; reason: string; teacher_id: string; created_at: string }[];
  current_revision: number; events: Digest[]; rubric: { item_id: string; description: string; stage: string }[];
  submissions: Record<string, unknown>; case_notes: string; basis_coverage: { missing_clinical_stages: string[] };
}
interface Decision { item_id: string; score: number; reason: string; evidence_event_ids: string[]; }

export const digestLabel = (e: Digest) =>
  `#${e.seq}　${e.sim_time}分　${e.question ? `问诊：${e.question}` : e.exam ? `检查：${e.exam}` : e.action}`;

export function Lookup({ params, onLoad, title }: { params: URLSearchParams; onLoad: (owner: string, session: string) => void; title: string }) {
  const [owner, setOwner] = useState(params.get("owner") || "");
  const [session, setSession] = useState(params.get("session") || "");
  useEffect(() => { if (params.get("owner") && params.get("session")) onLoad(params.get("owner")!, params.get("session")!); }, []); // eslint-disable-line
  const submit = (e: FormEvent) => { e.preventDefault(); if (owner.trim() && session.trim()) onLoad(owner.trim(), session.trim()); };
  return (
    <Panel title={title}>
      <form className="row" onSubmit={submit} style={{ alignItems: "flex-end" }}>
        <label className="field grow"><span>学生账号编号</span><input className="input mono" value={owner} onChange={(e) => setOwner(e.target.value)} /></label>
        <label className="field grow"><span>训练编号</span><input className="input mono" value={session} onChange={(e) => setSession(e.target.value)} /></label>
        <button className="btn btn-primary" disabled={!owner.trim() || !session.trim()}>载入</button>
        <button type="button" className="btn" onClick={() => navigate("/teacher/dashboard")}>从学情统计选择</button>
      </form>
    </Panel>
  );
}

export function ScoreReview({ params }: { params: URLSearchParams }) {
  const [record, setRecord] = useState<Record_ | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const { busy, run } = useAction();

  const load = async (owner: string, session: string) => {
    setError(null); setSaved(null); setRecord(null);
    try {
      const r = await get<Record_>(`/api/teacher/scores/${enc(session)}?owner_id=${enc(owner)}`);
      const current = r.reviews.length ? r.reviews[r.reviews.length - 1].snapshot : r.original_score;
      setRecord(r);
      setDecisions(current.items.map((i) => ({ item_id: i.item_id, score: i.score, reason: i.reason, evidence_event_ids: [...i.evidence_event_ids] })));
      setReason(""); setConfirmed(false);
    } catch (e) { setError(e instanceof ApiError ? e.message : "载入失败"); }
  };
  const descriptions = Object.fromEntries((record?.rubric || []).map((r) => [r.item_id, r.description]));
  const events = (record?.events || []).map((e) => ({ id: e.event_id, label: digestLabel(e) }));
  const total = decisions.reduce((s, d) => s + (Number(d.score) || 0), 0);
  const update = (i: number, patch: Partial<Decision>) => setDecisions(decisions.map((d, j) => (j === i ? { ...d, ...patch } : d)));

  const save = () => record && run("save", async () => {
    const result = await mutate<{ revision: number; notice: string }>(`score-review:${record.session_id}`, "POST", `/api/teacher/scores/${enc(record.session_id)}`, {
      owner_id: record.owner_id, expected_revision: record.current_revision, reason, confirmed, items: decisions.map((d) => ({ ...d, score: Number(d.score) })),
    });
    setSaved(`已保存第 ${result.revision} 版裁定。${result.notice}`);
    await load(record.owner_id, record.session_id);
  });

  return (
    <div className="stack-lg">
      <div className="page-title"><h1>评分复核</h1></div>
      <Lookup params={params} onLoad={load} title="载入封存训练" />
      {error && <Msg tone="bad">{error}</Msg>}
      {saved && <Msg tone="ok">{saved}</Msg>}
      {record && <>
        <Msg tone="warn">裁定另存为独立版本，原规则评分保留。分项说明对学生可见，请勿写出参考诊断。</Msg>
        <div className="work">
          <Panel title={`${record.case.public_id}　${record.case.title}　v${record.case.version}`}
            actions={<span className="tag">已有 {record.current_revision} 版裁定</span>}>
            <div className="stack">
              <div className="spread"><span>原规则分 <b className="num">{record.original_score.total_score} / {record.original_score.max_score}</b>
                <span className={`status ${record.original_score.status === "needs_review" ? "warn" : "ok"}`} style={{ marginLeft: 8 }}>{record.original_score.status === "needs_review" ? "待人工复核" : "已完成自动评分"}</span></span>
                <span>裁定合计 <b className="num">{total.toFixed(1)}</b></span></div>
              {decisions.map((d, i) => {
                const original = record.original_score.items.find((x) => x.item_id === d.item_id)!;
                return (
                  <div key={d.item_id} className="ddx">
                    <div className="spread"><b style={{ fontWeight: 500 }}>{original.stage}　{descriptions[d.item_id] || d.item_id}</b>
                      <span className="faint">原分 {original.score} / {original.max_score}</span></div>
                    <div className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
                      <label className="field" style={{ width: 110, flex: "none" }}><span>裁定得分 (0–{original.max_score})</span>
                        <input className="input num" type="number" step={0.1} min={0} max={original.max_score} value={d.score}
                          onChange={(e) => update(i, { score: Math.min(original.max_score, Math.max(0, Number(e.target.value))) })} /></label>
                      <label className="field grow"><span>分项说明（学生可见）</span>
                        <input className="input" maxLength={600} value={d.reason} onChange={(e) => update(i, { reason: e.target.value })} /></label>
                    </div>
                    <details className="fold"><summary>证据 {d.evidence_event_ids.length} 条{d.score > 0 && !d.evidence_event_ids.length ? "（得分项须引用证据）" : ""}</summary>
                      <div><Pick label="选择封存前事件" items={events} value={d.evidence_event_ids} onChange={(v) => update(i, { evidence_event_ids: v })} /></div></details>
                  </div>
                );
              })}
              <label className="field"><span>整体裁定原因（仅教师可见）</span>
                <textarea className="textarea" maxLength={1000} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="说明原分争议与依据" /></label>
              <label className="check"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />我已逐项核对分数、封存前事件和适用量表</label>
              <div className="actions"><button className="btn btn-primary" disabled={!confirmed || !reason.trim() || busy !== null} onClick={save}>{busy ? "保存中…" : "保存独立评分裁定"}</button></div>
            </div>
          </Panel>
          <div className="stack">
            <Panel title="学生提交">
              <dl className="kv"><dt>诊断</dt><dd>{String((record.submissions.diagnosis as { diagnosis?: string } | null)?.diagnosis || "未提交")}</dd>
                <dt>病历</dt><dd style={{ whiteSpace: "pre-wrap" }}>{record.case_notes || "未填写"}</dd></dl>
            </Panel>
            <Panel title={`封存前事件（${record.events.length}）`}>
              <div className="stack" style={{ gap: 6, maxHeight: 420, overflowY: "auto" }}>
                {record.events.map((e) => <div key={e.event_id} className="small"><b className="num" style={{ fontWeight: 500 }}>{digestLabel(e)}</b>{e.answer && <div className="muted">答：{e.answer}</div>}</div>)}
              </div>
            </Panel>
            <details className="fold"><summary>历次裁定与原始数据（只读）</summary><div><JsonView value={{ reviews: record.reviews, submissions: record.submissions }} /></div></details>
          </div>
        </div>
      </>}
    </div>
  );
}

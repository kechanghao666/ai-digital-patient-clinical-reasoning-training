import { useState } from "react";
import { ApiError, enc, get, mutate } from "../../api";
import type { ScoreSnapshot } from "../../types";
import { STAGES } from "../../types";
import { Pick, JsonView, Msg, Panel, useAction } from "../../ui";
import { digestLabel, Lookup, type Digest } from "./ScoreReview";

interface Basis { basis_id: string; title: string; stages: string[]; text: string; sources: string[]; revision: number; content_hash: string; }
interface FbItem { stage: string; comment: string; improvement: string; evidence_event_ids: string[]; basis_ids: string[]; }
interface FbRecord {
  session_id: string; owner_id: string; case: { title: string; public_id: string; version: number };
  score_snapshot: ScoreSnapshot; events: Digest[]; submissions: Record<string, unknown>; case_notes: string;
  original: unknown; reviews: unknown[]; current_revision: number; validation_error: string | null; original_validation_error: string | null;
  editable_items: Partial<FbItem>[]; basis_provenance: string | null; teaching_basis: Basis[]; notice: string;
  basis_coverage: { missing_clinical_stages: string[] };
}

export function FeedbackReview({ params }: { params: URLSearchParams }) {
  const [record, setRecord] = useState<FbRecord | null>(null);
  const [items, setItems] = useState<FbItem[]>([]);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const { busy, run } = useAction();

  const load = async (owner: string, session: string) => {
    setError(null); setSaved(null); setRecord(null);
    try {
      const r = await get<FbRecord>(`/api/teacher/feedback/${enc(session)}?owner_id=${enc(owner)}`);
      const existing = Object.fromEntries((Array.isArray(r.editable_items) ? r.editable_items : []).map((i) => [i.stage, i]));
      const ids = new Set(r.events.map((e) => e.event_id));
      setItems(STAGES.map((stage) => {
        const old = existing[stage] || {};
        const allowed = new Set(r.teaching_basis.filter((b) => b.stages.includes(stage)).map((b) => b.basis_id));
        return { stage, comment: typeof old.comment === "string" ? old.comment : "", improvement: typeof old.improvement === "string" ? old.improvement : "",
          evidence_event_ids: (old.evidence_event_ids || []).filter((v: string) => ids.has(v)), basis_ids: (old.basis_ids || []).filter((v: string) => allowed.has(v)) };
      }));
      setRecord(r); setReason("");
    } catch (e) { setError(e instanceof ApiError ? e.message : "载入失败"); }
  };
  const update = (i: number, patch: Partial<FbItem>) => setItems(items.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const events = (record?.events || []).map((e) => ({ id: e.event_id, label: digestLabel(e) }));
  const save = () => record && run("save", async () => {
    const used = new Set(items.flatMap((i) => i.basis_ids));
    const result = await mutate<{ revision: number; notice: string }>(`feedback-review:${record.session_id}`, "POST", `/api/teacher/feedback/${enc(record.session_id)}`, {
      owner_id: record.owner_id, expected_revision: record.current_revision, reason, items,
      basis_hashes: Object.fromEntries(record.teaching_basis.filter((b) => used.has(b.basis_id)).map((b) => [b.basis_id, b.content_hash])),
    });
    setSaved(`已保存反馈复核第 ${result.revision} 版。${result.notice}`);
    await load(record.owner_id, record.session_id);
  });
  const complete = items.every((i) => i.comment.trim() && i.improvement.trim()) && reason.trim();

  return (
    <div className="stack-lg">
      <div className="page-title"><h1>反馈复核</h1></div>
      <Lookup params={params} onLoad={load} title="载入封存训练" />
      {error && <Msg tone="bad">{error}</Msg>}
      {saved && <Msg tone="ok">{saved}</Msg>}
      {record && <>
        <Msg tone="warn">只修订评语与引用，不改变评分。保存后学生看到最新复核版本。</Msg>
        {record.validation_error && <Msg tone="bad">当前反馈未通过学生展示校验：{record.validation_error}</Msg>}
        {record.basis_coverage?.missing_clinical_stages?.length ? <Msg tone="warn">缺少专科临床依据：{record.basis_coverage.missing_clinical_stages.join("、")}。通用反馈规范不能替代。</Msg> : null}
        <div className="work">
          <Panel title={`${record.case.public_id}　${record.case.title}`} actions={<span className="tag">已有 {record.current_revision} 版复核</span>}>
            <div className="stack">
              {items.map((item, i) => {
                const basis = record.teaching_basis.filter((b) => b.stages.includes(item.stage));
                return (
                  <div key={item.stage} className="ddx">
                    <b style={{ fontWeight: 600 }}>{item.stage}</b>
                    <label className="field"><span>复核评语</span><textarea className="textarea" style={{ minHeight: 70 }} maxLength={600} value={item.comment} onChange={(e) => update(i, { comment: e.target.value })} /></label>
                    <label className="field"><span>改进建议</span><textarea className="textarea" style={{ minHeight: 56 }} maxLength={300} value={item.improvement} onChange={(e) => update(i, { improvement: e.target.value })} /></label>
                    <Pick label="事件引用" items={events} value={item.evidence_event_ids} onChange={(v) => update(i, { evidence_event_ids: v })} />
                    <Pick label="教学依据（未医学审核）" items={basis.map((b) => ({ id: b.basis_id, label: b.title }))} value={item.basis_ids} onChange={(v) => update(i, { basis_ids: v })} />
                  </div>
                );
              })}
              <label className="field"><span>复核原因与修改说明（仅教师可见）</span><textarea className="textarea" maxLength={1000} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
              <div className="actions"><button className="btn btn-primary" disabled={!complete || busy !== null} onClick={save}>{busy ? "保存中…" : "保存反馈复核版本"}</button></div>
            </div>
          </Panel>
          <div className="stack">
            <Panel title={`封存原分 ${record.score_snapshot.total_score} / ${record.score_snapshot.max_score}`}>
              <div className="stack" style={{ gap: 6 }}>{record.score_snapshot.items.map((s) => <div key={s.item_id} className="small"><b style={{ fontWeight: 500 }}>{s.stage}</b> {s.score}/{s.max_score}　<span className="muted">{s.reason}</span></div>)}</div>
            </Panel>
            <Panel title="学生病历与诊断">
              <dl className="kv"><dt>诊断</dt><dd>{String((record.submissions.diagnosis as { diagnosis?: string } | null)?.diagnosis || "未提交")}</dd>
                <dt>病历</dt><dd style={{ whiteSpace: "pre-wrap" }}>{record.case_notes || "未填写"}</dd></dl>
            </Panel>
            <Panel title={`封存前事件（${record.events.length}）`}>
              <div className="stack" style={{ gap: 6, maxHeight: 360, overflowY: "auto" }}>
                {record.events.map((e) => <div key={e.event_id} className="small"><b className="num" style={{ fontWeight: 500 }}>{digestLabel(e)}</b>{e.answer && <div className="muted">答：{e.answer}</div>}</div>)}
              </div>
            </Panel>
            <details className="fold"><summary>适用教学依据全文（{record.teaching_basis.length}）</summary><div className="stack">
              {record.teaching_basis.map((b) => <div key={b.basis_id} className="small"><b style={{ fontWeight: 500 }}>{b.title}</b> <span className="faint">修订 {b.revision}　{b.stages.join("、")}</span><p className="muted" style={{ whiteSpace: "pre-wrap" }}>{b.text}</p>{b.sources.map((s) => <div key={s} className="faint">来源：{s}</div>)}</div>)}
            </div></details>
            <details className="fold"><summary>原始反馈与历次复核（只读）</summary><div><JsonView value={{ original: record.original, reviews: record.reviews }} /></div></details>
          </div>
        </div>
      </>}
    </div>
  );
}

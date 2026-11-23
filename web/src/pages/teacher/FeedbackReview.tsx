import { useRef, useState } from "react";
import { ApiError, enc, get, mutate, request } from "../../api";
import type { ScoreSnapshot } from "../../types";
import { STAGES } from "../../types";
import { Pick, JsonView, Msg, Panel, useAction } from "../../ui";
import { digestLabel, Lookup, type Digest } from "./ScoreReview";

interface Basis { basis_id: string; title: string; stages: string[]; applies_to_cases: string[]; kind: string; text: string; sources: string[]; revision: number; content_hash: string; }
interface FbItem { stage: string; comment: string; improvement: string; evidence_event_ids: string[]; basis_ids: string[]; }
interface FbRecord {
  session_id: string; owner_id: string; case: { title: string; public_id: string; version: number };
  score_snapshot: ScoreSnapshot; events: Digest[]; submissions: Record<string, unknown>; case_notes: string;
  original: unknown; reviews: unknown[]; current_revision: number; validation_error: string | null; original_validation_error: string | null;
  editable_items: Partial<FbItem>[]; basis_provenance: string | null; teaching_basis: Basis[]; notice: string;
  basis_coverage: BasisCoverage;
}

// Per-stage clinical coverage and the general (non-clinical) guidance ids, as returned by the API.
interface BasisCoverage {
  status: string; clinical_by_stage: Record<string, string[]>; guidance_ids: string[];
  missing_clinical_stages: string[]; notice: string;
}

// The library status is a single, library-wide fact; it is never presented as a medical review.
const BASIS_STATUS: Record<string, string> = { draft_unreviewed: "草稿，未医学审核" };
const BASIS_ALL = "全部";

interface BasisSearchResult { basis_id: string; title: string; stages: string[]; applies_to_cases: string[]; sources: string[]; revision: number; score: number; text: string; }
// Rerank reorders the already-narrowed candidates by relevance. A failed rerank returns the
// candidates in their original order WITHOUT scores, and the UI must never invent a score.
interface RerankResult { basis_id: string; title: string; stages: string[]; applies_to_cases: string[]; sources: string[]; revision: number; text: string; relevance_score?: number; }
// The server echoes the exact scope it ranked, so the panel can tell "no applicable specialty
// basis" apart from "applicable entries that scored below the match floor".
interface BasisScope { case_id: string; case_version: number; stage: string | null; clinical_candidates: number; }
interface BasisSearchResponse { available: boolean; reason?: string; status?: string; notice?: string; scope?: BasisScope; results: BasisSearchResult[]; }
interface RerankResponse { available: boolean; reranked: boolean; reason?: string; status?: string; notice?: string; scope?: BasisScope; results: RerankResult[]; original_order?: string[]; }

export function FeedbackReview({ params }: { params: URLSearchParams }) {
  const [record, setRecord] = useState<FbRecord | null>(null);
  const [items, setItems] = useState<FbItem[]>([]);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  // The save receipt names the training it belongs to, so it survives the refresh its own save triggers
  // but never leaks onto a different training the teacher has since loaded.
  const [saved, setSaved] = useState<{ session_id: string; text: string; stale?: boolean } | null>(null);
  const [basisQuery, setBasisQuery] = useState("");
  const [basisResults, setBasisResults] = useState<BasisSearchResult[] | null>(null);
  const [basisMsg, setBasisMsg] = useState<string | null>(null);
  const [basisNotice, setBasisNotice] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [rerankResults, setRerankResults] = useState<RerankResult[] | null>(null);
  const [rerankMsg, setRerankMsg] = useState<string | null>(null);
  const [rerankNotice, setRerankNotice] = useState<string | null>(null);
  const [reranking, setReranking] = useState(false);
  // OCR checklist draft: teacher-uploaded synthetic teaching checklist -> unchecked text draft.
  // Read-only: it never writes into the case, the review or the score. On any failure the
  // previous draft is dropped, so a stale transcription can never pass as a fresh one.
  const [ocrDraft, setOcrDraft] = useState<string | null>(null);
  const [ocrMsg, setOcrMsg] = useState<string | null>(null);
  const [ocrBusy, setOcrBusy] = useState(false);
  const [basisStage, setBasisStage] = useState<string>(BASIS_ALL);
  const { busy, run } = useAction();
  // Monotonic id for basis searches. Switching case or stage (or starting a newer search) bumps it,
  // so a slow response from a previous scope is discarded instead of overwriting the current panel.
  const searchSeq = useRef(0);
  // Separate monotonic id for reranks, for the same reason: a late rerank response from a previous
  // scope or training must never overwrite the panel (or the draft) now on screen.
  const rerankSeq = useRef(0);
  // Separate monotonic id for OCR drafts: selecting a new image, switching stage or loading another
  // training invalidates the in-flight request, so its late result is discarded on arrival.
  const ocrSeq = useRef(0);
  // Monotonic id for whole-record loads. Loading another training bumps it, so a slow response for a
  // previously loaded training is dropped instead of replacing the record (and draft) now on screen.
  const loadSeq = useRef(0);
  // The training currently on screen. A save's receipt and its refresh only apply while it still matches,
  // so a late save response for A cannot surface on the B the teacher has since loaded.
  const activeSession = useRef<string | null>(null);

  const clearBasisResults = () => {
    searchSeq.current += 1;
    rerankSeq.current += 1;
    ocrSeq.current += 1;
    setBasisResults(null); setBasisMsg(null); setBasisNotice(null); setSearching(false);
    setRerankResults(null); setRerankMsg(null); setRerankNotice(null); setReranking(false);
    setOcrDraft(null); setOcrMsg(null); setOcrBusy(false);
  };
  // Changing the stage only clears the semantic shortlist; it never touches the review text the
  // teacher is editing.
  const changeStage = (stage: string) => { clearBasisResults(); setBasisStage(stage); };

  const load = async (owner: string, session: string): Promise<boolean | null> => {
    const seq = ++loadSeq.current;
    activeSession.current = session;
    setError(null);
    setSaved((cur) => (cur && cur.session_id === session ? cur : null));  // keep a receipt only for its own training
    setRecord(null);
    setBasisResults(null); setBasisMsg(null); setBasisNotice(null); setBasisQuery(""); setBasisStage(BASIS_ALL);
    setRerankResults(null); setRerankMsg(null); setRerankNotice(null); setReranking(false);
    setOcrDraft(null); setOcrMsg(null); setOcrBusy(false);
    searchSeq.current += 1; rerankSeq.current += 1; ocrSeq.current += 1; setSearching(false);
    try {
      const r = await get<FbRecord>(`/api/teacher/feedback/${enc(session)}?owner_id=${enc(owner)}`);
      if (seq !== loadSeq.current) return null;  // superseded by a newer load; do not replace what is on screen
      const existing = Object.fromEntries((Array.isArray(r.editable_items) ? r.editable_items : []).map((i) => [i.stage, i]));
      const ids = new Set(r.events.map((e) => e.event_id));
      setItems(STAGES.map((stage) => {
        const old = existing[stage] || {};
        const allowed = new Set(r.teaching_basis.filter((b) => b.stages.includes(stage)).map((b) => b.basis_id));
        return { stage, comment: typeof old.comment === "string" ? old.comment : "", improvement: typeof old.improvement === "string" ? old.improvement : "",
          evidence_event_ids: (old.evidence_event_ids || []).filter((v: string) => ids.has(v)), basis_ids: (old.basis_ids || []).filter((v: string) => allowed.has(v)) };
      }));
      setRecord(r); setReason("");
      return true;
    } catch (e) {
      if (seq !== loadSeq.current) return null;  // a superseded load's failure must not surface on the current training
      setError(e instanceof ApiError ? e.message : "载入失败");
      return false;
    }
  };
  const update = (i: number, patch: Partial<FbItem>) => setItems(items.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const events = (record?.events || []).map((e) => ({ id: e.event_id, label: digestLabel(e) }));
  const save = () => record && run("save", async () => {
    const session = record.session_id;
    const used = new Set(items.flatMap((i) => i.basis_ids));
    let result: { revision: number; notice: string };
    try {
      result = await mutate<{ revision: number; notice: string }>(`feedback-review:${session}`, "POST", `/api/teacher/feedback/${enc(session)}`, {
        owner_id: record.owner_id, expected_revision: record.current_revision, reason, items,
        basis_hashes: Object.fromEntries(record.teaching_basis.filter((b) => used.has(b.basis_id)).map((b) => [b.basis_id, b.content_hash])),
      });
    } catch (e) {
      if (activeSession.current !== session) return;  // teacher moved to another training: A's rejection must not surface here
      throw e;  // still on this training: the rejection shows, the teacher's input is kept, nothing is saved
    }
    if (activeSession.current !== session) return;  // teacher moved on: drop A's receipt so it cannot appear on B
    setSaved({ session_id: session, text: `已保存反馈复核第 ${result.revision} 版。${result.notice}` });
    const refreshed = await load(record.owner_id, session);
    if (activeSession.current !== session) return;  // moved on during the refresh: say nothing about A
    if (refreshed === false) setSaved({ session_id: session, stale: true, text: `已保存反馈复核第 ${result.revision} 版，但页面刷新失败，请重新载入确认。` });
  });
  const complete = items.every((i) => i.comment.trim() && i.improvement.trim()) && reason.trim();

  // Local, non-semantic lookup: the case-filtered library entries, narrowed to the chosen stage.
  // Clinical entries are the stage-specific basis; general feedback guidance is shown separately
  // because it must not be presented as specialty clinical coverage.
  const basisScoped = (record?.teaching_basis || []).filter((b) => basisStage === BASIS_ALL || b.stages.includes(basisStage));
  const basisClinical = basisScoped.filter((b) => b.kind !== "feedback_guidance");
  const basisGuidance = basisScoped.filter((b) => b.kind === "feedback_guidance");
  const basisStatus = BASIS_STATUS[record?.basis_coverage?.status || ""] || record?.basis_coverage?.status || "草稿，未医学审核";
  const basisEntry = (b: Basis) => (
    <div key={b.basis_id} className="small">
      <b style={{ fontWeight: 500 }}>{b.title}</b>{" "}
      <span className="faint">{b.stages.join("、")}　适用范围：{b.applies_to_cases.includes("*") ? "全部病例" : b.applies_to_cases.join("、")}　修订 {b.revision}　{basisStatus}</span>
      <p className="muted" style={{ whiteSpace: "pre-wrap" }}>{b.text}</p>
      {b.sources.map((s) => <div key={s} className="faint">来源：{s}</div>)}
    </div>
  );

  const searchBasis = async () => {
    const q = basisQuery.trim();
    if (!q || !record) return;
    const seq = ++searchSeq.current;
    const stage = basisStage === BASIS_ALL ? null : basisStage;
    setSearching(true); setBasisMsg(null); setBasisResults(null); setBasisNotice(null);
    try {
      const r = await request<BasisSearchResponse>("POST", "/api/teacher/feedback/basis/search", {
        session_id: record.session_id, owner_id: record.owner_id, query: q, stage,
      });
      if (seq !== searchSeq.current) return;  // superseded by a newer search or a case/stage switch
      if (!r.available) {
        setBasisMsg(r.reason === "embedding_unconfigured" ? "语义检索未配置，暂不可用。" : "语义检索服务暂时不可用，请稍后重试。");
        setBasisResults([]);
      } else if (!r.results.length) {
        setBasisMsg(r.scope?.clinical_candidates === 0
          ? "当前病例／环节暂无适用专科依据；通用反馈规范不能替代临床依据。"
          : "在本病例／环节的适用依据中未找到语义相关条目。");
        setBasisResults([]);
      } else {
        setBasisNotice(r.notice || "草稿，未医学审核");
        setBasisResults(r.results);
      }
    } catch (e) {
      if (seq !== searchSeq.current) return;
      setBasisMsg(e instanceof ApiError ? e.message : "检索失败");
      setBasisResults([]);
    } finally { if (seq === searchSeq.current) setSearching(false); }
  };

  // Teacher-triggered rerank of the already-narrowed candidates. It never calls the embedding
  // service (the scope filter alone picks the candidates), never writes anything, and never
  // auto-cites: it only reorders the shortlist. A failed rerank keeps the original order and
  // says so — it must never look like a successful model output, and no score is invented.
  const rerankBasis = async () => {
    const q = basisQuery.trim();
    if (!q || !record) return;
    const seq = ++rerankSeq.current;
    const stage = basisStage === BASIS_ALL ? null : basisStage;
    setReranking(true); setRerankMsg(null); setRerankResults(null); setRerankNotice(null);
    try {
      const r = await request<RerankResponse>("POST", "/api/teacher/feedback/basis/rerank", {
        session_id: record.session_id, owner_id: record.owner_id, query: q, stage,
      });
      if (seq !== rerankSeq.current) return;  // superseded by a newer rerank or a case/stage switch
      if (!r.available) {
        if (r.reason === "rerank_unconfigured") {
          setRerankMsg("重排序未配置，暂不可用。");
          setRerankResults([]);
        } else {
          // A service failure keeps the original candidate order (returned by the server, without
          // scores) and says so honestly — it must never look like a successful model output.
          setRerankMsg(r.notice || "重排失败，显示原顺序。");
          setRerankResults(r.results);
        }
      } else if (r.scope?.clinical_candidates === 0) {
        // Zero candidates is an honest empty state about the scope, NOT a model failure — it must
        // be reported before the rerank-failure branch so it never reads as "重排失败".
        setRerankMsg("当前病例／环节暂无适用专科依据；通用反馈规范不能替代临床依据。");
        setRerankResults([]);
      } else if (!r.reranked) {
        setRerankMsg(r.notice || "重排失败，显示原顺序。");
        setRerankResults(r.results);
      } else if (!r.results.length) {
        setRerankMsg(r.scope?.clinical_candidates === 0
          ? "当前病例／环节暂无适用专科依据；通用反馈规范不能替代临床依据。"
          : "当前范围没有可重排的候选条目。");
        setRerankResults([]);
      } else {
        setRerankNotice(r.notice || "草稿，未医学审核");
        setRerankResults(r.results);
      }
    } catch (e) {
      if (seq !== rerankSeq.current) return;
      setRerankMsg(e instanceof ApiError ? e.message : "重排失败，显示原顺序。");
      setRerankResults([]);
    } finally { if (seq === rerankSeq.current) setReranking(false); }
  };

  // Teacher-triggered OCR of a (synthetic) teaching checklist image. The transcription is shown
  // as a raw draft for manual checking only — it never becomes a case fact, a review item or a
  // score. On any failure the previous draft is dropped and the reason is shown honestly. Every
  // transition (new image picked, request about to be sent, result applied, busy cleared) re-checks
  // ownership, so a superseded request can never surface here — success or failure alike.
  const ocrChecklist = async (file: File | undefined) => {
    if (!file || !record) return;
    const seq = ++ocrSeq.current;
    // Same criterion as the server (decoded bytes ≤ 2 MiB; for these formats the file size is the
    // decoded size), checked before reading so an oversized image never even enters the pipeline.
    // This selection already bumped the sequence, so it OWNS the panel state: the busy flag of a
    // superseded in-flight request is cleared here, and that request's late result will be dropped
    // on arrival without touching this message.
    const MAX_BYTES = 2 * 1024 * 1024;
    if (file.size > MAX_BYTES) {
      setOcrDraft(null);
      setOcrBusy(false);
      setOcrMsg("图片超过 2 MiB 限制，请压缩后重试。");
      return;
    }
    setOcrBusy(true); setOcrMsg(null); setOcrDraft(null);
    let dataUrl: string;
    try {
      dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(new Error("read_failed"));
        reader.readAsDataURL(file);
      });
    } catch { if (seq === ocrSeq.current) { setOcrBusy(false); setOcrMsg("读取图片失败。"); } return; }
    if (seq !== ocrSeq.current) return;  // superseded while reading: send nothing
    try {
      const r = await request<{ available: boolean; reason?: string; notice?: string; draft_text?: string }>(
        "POST", "/api/teacher/feedback/ocr/draft", { image_data_url: dataUrl });
      if (seq !== ocrSeq.current) return;  // superseded in flight: drop the result
      if (r.available && typeof r.draft_text === "string") setOcrDraft(r.draft_text);
      else setOcrMsg(r.reason === "ocr_unconfigured" ? "OCR 未配置，暂不可用。" : "OCR 服务暂时不可用，未生成文字草稿。");
    } catch (e) {
      if (seq !== ocrSeq.current) return;
      setOcrMsg(e instanceof ApiError ? e.message : "OCR 服务暂时不可用，未生成文字草稿。");
    } finally { if (seq === ocrSeq.current) setOcrBusy(false); }
  };

  return (
    <div className="stack-lg">
      <div className="page-title"><h1>反馈复核</h1></div>
      <Lookup params={params} onLoad={load} title="载入封存训练" />
      {error && <Msg tone="bad">{error}</Msg>}
      {saved && <Msg tone={saved.stale ? "warn" : "ok"}>{saved.text}</Msg>}
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
            <Panel title="教学依据语义检索（未医学审核）">
              <div className="stack">
                <div className="faint">检索范围：{record.case.public_id} · {basisStage === BASIS_ALL ? "全部环节" : basisStage}。仅在本病例当前版本的适用专科依据内排序，不会推荐其他科材料。</div>
                <div className="chips">
                  {[BASIS_ALL, ...STAGES].map((stage) => (
                    <button key={stage} type="button" aria-pressed={basisStage === stage} onClick={() => changeStage(stage)}>{stage}</button>
                  ))}
                </div>
                <div className="row">
                  <input className="input" style={{ flex: 1 }} value={basisQuery} onChange={(e) => setBasisQuery(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") searchBasis(); }}
                    placeholder="输入教学要点，如：胸痛应先做心电图" maxLength={200} aria-label="语义检索教学依据" />
                  <button className="btn" disabled={searching || !basisQuery.trim()} onClick={searchBasis}>{searching ? "检索中…" : "检索"}</button>
                  <button className="btn" disabled={reranking || !basisQuery.trim()} onClick={rerankBasis}
                    title="对当前病例／环节的候选做相关性重排，不调用语义检索模型">{reranking ? "重排中…" : "重排"}</button>
                </div>
                {basisMsg && <Msg tone="warn">{basisMsg}</Msg>}
                {rerankMsg && <Msg tone="warn">{rerankMsg}</Msg>}
                {rerankResults && rerankResults.length > 0 && <div className="stack" style={{ gap: 6 }}>
                  <div className="faint">重排候选 {rerankResults.length} 条　{rerankNotice || "草稿，未医学审核"}　仅辅助定位，不会自动引用。</div>
                  {rerankResults.map((r, i) => (
                    <div key={r.basis_id} className="small">
                      <b style={{ fontWeight: 500 }}>{i + 1}. {r.title}</b>{" "}
                      <span className="faint">{r.stages.join("、")}　适用：{r.applies_to_cases.join("、")}
                        {typeof r.relevance_score === "number" && <>　相关度 {Math.round(r.relevance_score * 100)}%</>}
                      </span>
                      <p className="muted" style={{ whiteSpace: "pre-wrap" }}>{r.text}</p>
                      {r.sources.map((s) => <div key={s} className="faint">来源：{s}</div>)}
                    </div>
                  ))}
                </div>}
                {basisResults && basisResults.length > 0 && <div className="stack" style={{ gap: 6 }}>
                  <div className="faint">候选 {basisResults.length} 条　{basisNotice || "草稿，未医学审核"}　仅辅助定位，不会自动引用。</div>
                  {basisResults.map((r) => (
                    <div key={r.basis_id} className="small">
                      <b style={{ fontWeight: 500 }}>{r.title}</b> <span className="faint">{r.stages.join("、")}　适用：{r.applies_to_cases.join("、")}　匹配 {Math.round(r.score * 100)}%</span>
                      <p className="muted" style={{ whiteSpace: "pre-wrap" }}>{r.text}</p>
                      {r.sources.map((s) => <div key={s} className="faint">来源：{s}</div>)}
                    </div>
                  ))}
                </div>}
              </div>
            </Panel>
            <Panel title="检查单文字草稿（OCR·未经核对）">
              <div className="stack">
                <div className="faint">仅上传合成教学样例，不要上传真实患者资料。系统返回可核对的文字草稿：缺失字段须人工核对，不得作为已识别值使用；仅辅助阅读，不自动写入病例或评分。</div>
                <div className="row">
                  <input type="file" accept="image/png,image/jpeg,image/webp" aria-label="教学检查单图片"
                    onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; ocrChecklist(f); }} />
                  {ocrBusy && <span className="faint">识别中…</span>}
                </div>
                {ocrMsg && <Msg tone="warn">{ocrMsg}</Msg>}
                {ocrDraft && <div className="stack" style={{ gap: 6 }}>
                  <div className="faint">文字草稿，未经核对　缺失字段须人工核对，不得作为已识别值使用。　仅辅助阅读，不自动写入病例或评分。</div>
                  <div className="small muted" style={{ whiteSpace: "pre-wrap" }}>{ocrDraft}</div>
                </div>}
              </div>
            </Panel>
            <details className="fold" open><summary>适用教学依据全文（本地清单筛选·非 AI 语义）（{record.teaching_basis.length}）</summary><div className="stack">
              <div className="faint">按当前病例的本地材料筛选，不调用语义模型，也不代表“AI 语义命中”。</div>
              <div className="chips">
                {[BASIS_ALL, ...STAGES].map((stage) => (
                  <button key={stage} type="button" aria-pressed={basisStage === stage} onClick={() => changeStage(stage)}>{stage}</button>
                ))}
              </div>
              {basisClinical.length > 0 ? basisClinical.map(basisEntry) : <Msg tone="warn">当前病例／环节暂无适用依据</Msg>}
              {basisGuidance.length > 0 && <>
                <div className="faint">通用反馈规范（非专科临床依据，不能替代缺失的临床依据）</div>
                {basisGuidance.map(basisEntry)}
              </>}
            </div></details>
            <details className="fold"><summary>原始反馈与历次复核（只读）</summary><div><JsonView value={{ original: record.original, reviews: record.reviews }} /></div></details>
          </div>
        </div>
      </>}
    </div>
  );
}

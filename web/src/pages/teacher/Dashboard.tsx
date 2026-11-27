import { useEffect, useRef, useState } from "react";
import { ApiError, enc, get, getIdentity, request } from "../../api";
import { navigate } from "../../App";
import { STAGES } from "../../types";
import { Bar, Loading, Msg, Panel, pct, toneFor, useLoad } from "../../ui";

interface Training {
  session_id: string; summary_id: string; owner_id: string; username: string; display_name: string;
  public_id: string; case_version: number; title: string; sealed: boolean; question_count: number;
  history_domains: number; history_domains_total: number; score_status: string | null; score_ratio: number | null;
}
interface CaseRow {
  case_id: string; public_id: string; title: string; trainings: number; sealed: number; needs_review: number;
  average_ratio: number | null; stage_ratios: Record<string, number | null>;
  frequently_lost: { item_id: string; stage: string; description: string; attempts: number; lost: number; loss_rate: number }[];
}
interface Data { overview: { trainings: number; students: number; sealed: number; needs_review: number; average_ratio: number | null }; cases: CaseRow[]; trainings: Training[]; notice: string; }
interface Budget {
  daily_turn_limit: number; turns_today: number | null; exceeded: boolean | null; notice: string;
}
interface Observability {
  ok: boolean; store_note?: string; budget_note?: string;
  budget?: Budget | null;
  stats: {
    turns_total: number; turns_today: number;
    by_case: Record<string, number>;
    tool_calls_total: number; tool_calls_failed: number; tool_calls_failure_rate: number | null;
    turn_completion: { measured_count: number; min_ms: number | null; median_ms: number | null; p95_ms: number | null; max_ms: number | null; p95_method: string };
    token_usage: string; cost: string;
  } | null;
}
interface ExportResult {
  ok: boolean; scope: string; record_count: number; records: unknown[]; notice: string;
}
const STATUS: Record<string, [string, string]> = { complete: ["已评分", "ok"], needs_review: ["待复核", "warn"], failed: ["评分失败", "bad"] };
const heat = (r: number | null | undefined) => r == null ? {} : { background: r >= 0.8 ? "var(--ok-soft)" : r >= 0.5 ? "var(--warn-soft)" : "var(--bad-soft)", color: r >= 0.8 ? "var(--ok)" : r >= 0.5 ? "var(--warn)" : "var(--bad)" };

export function Dashboard({ params }: { params: URLSearchParams }) {
  const classId = params.get("class_id") || "";
  const local = getIdentity()?.mode === "local";
  const { data: classes } = useLoad(() => local ? get<{ id: string; name: string }[]>("/api/teacher/classes") : Promise.resolve([]), [local]);
  const { data: loaded, error, reload } = useLoad(async () => ({ classId,
    value: await get<Data>(`/api/teacher/dashboard${classId ? `?class_id=${enc(classId)}` : ""}`) }), [classId]);
  const data = loaded?.classId === classId ? loaded.value : null;
  const [obsCase, setObsCase] = useState("");
  const [obsFrom, setObsFrom] = useState("");
  const [obsTo, setObsTo] = useState("");
  const obsQuery = new URLSearchParams();
  if (obsCase) obsQuery.set("case_id", obsCase);
  if (obsFrom) obsQuery.set("date_from", obsFrom);
  if (obsTo) obsQuery.set("date_to", obsTo);
  const obsQs = obsQuery.toString();
  const { data: obs, error: obsError, reload: reloadObs } = useLoad(
    () => get<Observability>(`/api/teacher/observability${obsQs ? `?${obsQs}` : ""}`), [obsCase, obsFrom, obsTo]);
  const [caseFilter, setCaseFilter] = useState("");
  const [reviewOnly, setReviewOnly] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState<ExportResult | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const exportVersion = useRef(0);
  useEffect(() => {
    exportVersion.current++;
    setExportResult(null); setExportError(null); setExporting(false);
  }, [classId]);
  async function runExport() {
    const version = ++exportVersion.current;
    setExporting(true); setExportError(null); setExportResult(null);
    try {
      const data = await request<ExportResult>("POST", "/api/teacher/export", { class_id: classId || undefined });
      if (version === exportVersion.current) setExportResult(data);
    } catch (e) {
      if (version === exportVersion.current) setExportError(e instanceof ApiError ? e.message : "导出失败");
    } finally { if (version === exportVersion.current) setExporting(false); }
  }
  function downloadExport() {
    if (!exportResult) return;
    const blob = new Blob([JSON.stringify({ format: "teaching-deidentified-v1", notice: exportResult.notice,
      records: exportResult.records }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "deidentified-export.json"; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  if (!data || error) return <Loading error={error} onRetry={reload} />;
  const o = data.overview;
  const rows = data.trainings.filter((t) => (!caseFilter || t.public_id === caseFilter) && (!reviewOnly || t.score_status === "needs_review"));
  const lost = data.cases.flatMap((c) => c.frequently_lost.map((i) => ({ ...i, public_id: c.public_id }))).sort((a, b) => b.loss_rate - a.loss_rate || b.lost - a.lost).slice(0, 8);
  return (
    <div className="stack-lg">
      <div className="page-title">
        <h1>学情统计</h1>
        <div className="row">
          {local && <select className="select" style={{ width: 200 }} value={classId} aria-label="班级"
            onChange={(e) => navigate(`/teacher/dashboard${e.target.value ? `?class_id=${enc(e.target.value)}` : ""}`)}>
            <option value="">全部自有班级</option>{(classes || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>}
          <button className="btn" onClick={() => { reload(); reloadObs(); }}>刷新</button>
        </div>
      </div>
      <section className="panel"><div className="panel-body" style={{ display: "flex", gap: 36, flexWrap: "wrap" }}>
        {([["训练次数", o.trainings], ["学生", o.students], ["已提交", o.sealed], ["待复核", o.needs_review], ["平均得分率", pct(o.average_ratio)]] as const).map(([k, v]) =>
          <div key={k}><div className="faint">{k}</div><div style={{ fontSize: 20, fontWeight: 600 }} className="num">{v}</div></div>)}
      </div></section>
      <Panel title="模型调用观测" flush>
        <div className="panel-body" style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <input className="input" style={{ width: 150 }} placeholder="病例编号（内部ID）" value={obsCase} aria-label="观测病例筛选"
            onChange={(e) => setObsCase(e.target.value)} />
          <input className="input" type="date" value={obsFrom} aria-label="观测起始日期" onChange={(e) => setObsFrom(e.target.value)} />
          <input className="input" type="date" value={obsTo} aria-label="观测结束日期" onChange={(e) => setObsTo(e.target.value)} />
          <button className="btn" onClick={reloadObs}>刷新</button>
        </div>
        {obsError ? <div className="empty">统计暂不可用　<button className="btn-link btn" onClick={reloadObs}>重试</button></div>
          : !obs || !obs.stats ? <div className="empty">统计加载中…</div>
          : obs.stats.turns_total === 0 ? <div className="empty">筛选范围内暂无已记录的可信回合数据{obs.store_note ? `（${obs.store_note}）` : ""}</div>
          : <div className="panel-body" style={{ display: "flex", gap: 36, flexWrap: "wrap" }}>
              <div><div className="faint">记录回合数</div><div className="num" style={{ fontSize: 20, fontWeight: 600 }}>{obs.stats.turns_total}</div></div>
              <div><div className="faint">今日回合数</div><div className="num" style={{ fontSize: 20, fontWeight: 600 }}>{obs.stats.turns_today}</div></div>
              <div><div className="faint">工具调用失败率</div><div className="num" style={{ fontSize: 20, fontWeight: 600 }}>{obs.stats.tool_calls_failure_rate == null ? "—" : pct(obs.stats.tool_calls_failure_rate)}</div></div>
              <div><div className="faint">回合完成区间（中位 / P95）</div><div className="num" style={{ fontSize: 20, fontWeight: 600 }}>
                {obs.stats.turn_completion.median_ms == null ? "—" : `${(obs.stats.turn_completion.median_ms / 1000).toFixed(2)} 秒`}
                {" / "}{obs.stats.turn_completion.p95_ms == null ? "—" : `${(obs.stats.turn_completion.p95_ms / 1000).toFixed(2)} 秒`}</div></div>
              <div><div className="faint">费用</div><div className="num" style={{ fontSize: 20, fontWeight: 600 }}>{obs.stats.cost === "unknown" ? "未知" : obs.stats.cost}</div></div>
            </div>}
        {obs && obs.budget && (
          <div style={{ padding: "10px 16px 0" }}>
            <Msg tone={obs.budget.exceeded ? "bad" : "ok"}>
              {`每日预算 ${obs.budget.daily_turn_limit} 回合，今日已 ${obs.budget.turns_today == null ? "未知" : obs.budget.turns_today}`}
              {obs.budget.exceeded ? "：已达预算上限，请注意调用量。" : "：未达上限。"}
            </Msg>
          </div>
        )}
        {obs && obs.budget_note && <div style={{ padding: "10px 16px 0" }}><Msg tone="warn">{obs.budget_note}</Msg></div>}
        <p className="faint" style={{ margin: "10px 16px 0" }}>仅统计当前教师班级内学生已记录的可信回合（Nexent 适配器路径）；筛选按病例内部编号与 UTC 日期；适配器模式不代表真实供应商调用，预算按回合次数告警而非费用，token 用量与费用未知时显式标注。</p>
      </Panel>
      <Panel title="数据导出（去标识化）" flush>
        <div className="panel-body" style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <button className="btn btn-primary" disabled={exporting} onClick={runExport}>{exporting ? "导出中…" : (exportResult ? "重新导出" : "预览导出")}</button>
          {exportResult && <button className="btn" onClick={downloadExport}>下载 JSON</button>}
          <span className="faint">{local ? `范围：${classId ? "当前班级" : "全部自有班级"}` : "范围：全部自有班级"}</span>
        </div>
        {exportError && <div style={{ padding: "0 16px" }}><Msg tone="bad">{exportError}</Msg></div>}
        {exportResult && (
          <div style={{ padding: "0 16px 12px" }}>
            <p className="faint">已生成 {exportResult.record_count} 条去标识化记录。{exportResult.notice}</p>
            <p className="faint">保留字段：包内病例编号及版本、训练状态、相对事件顺序、模拟时间与原始工程评分。审计记录文件生成，不代表浏览器已保存下载。</p>
            <details><summary>查看字段与记录预览（最多 3 条）</summary>
              <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(exportResult.records.slice(0, 3), null, 2)}</pre>
            </details>
          </div>
        )}
      </Panel>
      {data.trainings.length === 0 ? <section className="panel"><div className="empty">暂无训练数据</div></section> : <>
        <div className="work">
          <Panel title="五环节得分率" flush>
            <div className="table-wrap"><table className="table">
              <thead><tr><th>病例</th><th className="num">提交/训练</th>{STAGES.map((s) => <th key={s} style={{ textAlign: "center" }}>{s}</th>)}<th className="num">总体</th></tr></thead>
              <tbody>{data.cases.map((c) => (
                <tr key={c.case_id}><td>{c.public_id}<div className="faint">{c.title}</div></td><td className="num">{c.sealed}/{c.trainings}</td>
                  {STAGES.map((s) => <td key={s}><div className="heat" style={heat(c.stage_ratios[s])}>{pct(c.stage_ratios[s])}</div></td>)}
                  <td className="num">{pct(c.average_ratio)}</td></tr>))}</tbody>
            </table></div>
          </Panel>
          <Panel title="高频失分项" flush>
            {lost.length === 0 ? <div className="empty">暂无</div> : <table className="table"><tbody>{lost.map((i) => (
              <tr key={i.public_id + i.item_id}><td><span className="faint">{i.public_id}　{i.stage}</span><div>{i.description}</div></td>
                <td style={{ width: 110 }}><Bar value={i.loss_rate} tone="bad" /><div className="faint num">{i.lost}/{i.attempts} 失分</div></td></tr>))}</tbody></table>}
          </Panel>
        </div>
        <Panel title={`训练明细（${rows.length}）`} flush actions={<>
          <select className="select" style={{ width: 140 }} value={caseFilter} onChange={(e) => setCaseFilter(e.target.value)} aria-label="病例">
            <option value="">全部病例</option>{data.cases.map((c) => <option key={c.public_id}>{c.public_id}</option>)}</select>
          <label className="check"><input type="checkbox" checked={reviewOnly} onChange={(e) => setReviewOnly(e.target.checked)} />只看待复核</label></>}>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>学生</th><th>病例</th><th>训练编号</th><th className="num">提问</th><th className="num">问诊结构</th><th>状态</th><th>得分率</th><th /></tr></thead>
            <tbody>{rows.map((t) => (
              <tr key={t.session_id}>
                <td>{t.display_name || t.username}<div className="faint mono">{t.username}</div></td>
                <td>{t.public_id} <span className="faint">v{t.case_version}</span></td>
                <td className="mono">{t.summary_id}</td><td className="num">{t.question_count}</td>
                <td className="num">{t.history_domains}/{t.history_domains_total}</td>
                <td>{t.sealed ? <span className={`status ${STATUS[t.score_status || ""]?.[1] || ""}`}>{STATUS[t.score_status || ""]?.[0] || "已提交"}</span> : <span className="status info">进行中</span>}</td>
                <td style={{ minWidth: 120 }}>{t.score_ratio == null ? "—" : <div className="row" style={{ flexWrap: "nowrap" }}><div className="grow"><Bar value={t.score_ratio} tone={toneFor(t.score_ratio)} /></div><span className="num">{pct(t.score_ratio)}</span></div>}</td>
                <td className="num" style={{ whiteSpace: "nowrap" }}>
                  <button className="btn" disabled={!t.sealed || !t.score_status || t.score_status === "failed"} onClick={() => navigate(`/teacher/scores?owner=${enc(t.owner_id)}&session=${enc(t.session_id)}`)}>评分复核</button>{" "}
                  <button className="btn" disabled={!t.sealed} onClick={() => navigate(`/teacher/feedback?owner=${enc(t.owner_id)}&session=${enc(t.session_id)}`)}>反馈复核</button></td>
              </tr>))}</tbody>
          </table></div>
        </Panel>
        <p className="faint">{data.notice}</p>
      </>}
    </div>
  );
}

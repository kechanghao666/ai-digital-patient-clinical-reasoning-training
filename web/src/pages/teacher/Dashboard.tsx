import { useState } from "react";
import { enc, get, getIdentity } from "../../api";
import { navigate } from "../../App";
import { STAGES } from "../../types";
import { Bar, Loading, Panel, pct, toneFor, useLoad } from "../../ui";

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
const STATUS: Record<string, [string, string]> = { complete: ["已评分", "ok"], needs_review: ["待复核", "warn"], failed: ["评分失败", "bad"] };
const heat = (r: number | null | undefined) => r == null ? {} : { background: r >= 0.8 ? "var(--ok-soft)" : r >= 0.5 ? "var(--warn-soft)" : "var(--bad-soft)", color: r >= 0.8 ? "var(--ok)" : r >= 0.5 ? "var(--warn)" : "var(--bad)" };

export function Dashboard({ params }: { params: URLSearchParams }) {
  const classId = params.get("class_id") || "";
  const local = getIdentity()?.mode === "local";
  const { data: classes } = useLoad(() => local ? get<{ id: string; name: string }[]>("/api/teacher/classes") : Promise.resolve([]), [local]);
  const { data, error, reload } = useLoad(() => get<Data>(`/api/teacher/dashboard${classId ? `?class_id=${enc(classId)}` : ""}`), [classId]);
  const [caseFilter, setCaseFilter] = useState("");
  const [reviewOnly, setReviewOnly] = useState(false);
  if (!data) return <Loading error={error} onRetry={reload} />;
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
            <option value="">全部学生</option>{(classes || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>}
          <button className="btn" onClick={reload}>刷新</button>
        </div>
      </div>
      <section className="panel"><div className="panel-body" style={{ display: "flex", gap: 36, flexWrap: "wrap" }}>
        {([["训练次数", o.trainings], ["学生", o.students], ["已提交", o.sealed], ["待复核", o.needs_review], ["平均得分率", pct(o.average_ratio)]] as const).map(([k, v]) =>
          <div key={k}><div className="faint">{k}</div><div style={{ fontSize: 20, fontWeight: 600 }} className="num">{v}</div></div>)}
      </div></section>
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

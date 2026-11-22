import { useMemo, useState } from "react";
import { enc, get, request } from "../api";
import { navigate } from "../App";
import type { CaseSummary } from "../types";
import { patientLine } from "../types";
import { Loading, useAction, useLoad } from "../ui";

export function Library() {
  const { data, error, reload } = useLoad(() => get<CaseSummary[]>("/api/cases"), []);
  const [query, setQuery] = useState("");
  const [specialty, setSpecialty] = useState("");
  const { busy, run } = useAction();
  const specialties = useMemo(() => [...new Set((data || []).map((c) => c.specialty))].sort(), [data]);
  const rows = useMemo(() => (data || [])
    .filter((c) => !specialty || c.specialty === specialty)
    .filter((c) => `${c.title}${c.public_id}${c.chief_complaint}`.toLowerCase().includes(query.trim().toLowerCase()))
    .sort((a, b) => a.public_id.localeCompare(b.public_id)), [data, query, specialty]);
  if (!data) return <Loading error={error} onRetry={reload} />;
  const start = (c: CaseSummary) => run(c.public_id, async () => {
    const created = await request<{ session_id: string }>("POST", `/api/sessions?case_id=${enc(c.public_id)}`);
    navigate(`/training/${created.session_id}`);
  });
  return (
    <div className="stack-lg">
      <div className="page-title">
        <h1>病例库</h1>
        <div className="row">
          <select className="select" style={{ width: 150 }} value={specialty} onChange={(e) => setSpecialty(e.target.value)} aria-label="专科">
            <option value="">全部专科</option>{specialties.map((s) => <option key={s}>{s}</option>)}
          </select>
          <input className="input" style={{ width: 220 }} placeholder="搜索主诉或编号" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="搜索" />
        </div>
      </div>
      <section className="panel">
        <div className="table-wrap"><table className="table">
          <thead><tr><th>编号</th><th>主诉</th><th>患者</th><th>专科</th><th>病例</th><th>审核</th><th /></tr></thead>
          <tbody>{rows.map((c) => (
            <tr key={c.public_id}>
              <td className="mono">{c.public_id}<span className="faint"> v{c.version}</span></td>
              <td style={{ fontWeight: 600 }}>{c.chief_complaint}</td>
              <td>{patientLine(c)}</td>
              <td>{c.specialty}</td>
              <td className="muted">{c.title}</td>
              <td>{c.review_status === "published" ? <span className="status ok">已审核</span> : <span className="status warn">教学草稿</span>}</td>
              <td className="num"><button className="btn btn-primary" disabled={busy !== null} onClick={() => start(c)}>{busy === c.public_id ? "创建中…" : "开始训练"}</button></td>
            </tr>))}</tbody>
        </table></div>
        {rows.length === 0 && <div className="empty">没有匹配的病例</div>}
      </section>
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";
import { enc, get, request } from "../api";
import { navigate } from "../App";
import type { CaseSummary, DepartmentCatalog } from "../types";
import { patientLine } from "../types";
import { Loading, useAction, useLoad } from "../ui";

export function Library() {
  const { data, error, reload } = useLoad(() => get<CaseSummary[]>("/api/cases"), []);
  const { data: deptCatalog, error: deptCatalogError } = useLoad(() => get<DepartmentCatalog>("/api/department-catalog"), []);
  const [query, setQuery] = useState("");
  const [department, setDepartment] = useState("");
  const [subspecialty, setSubspecialty] = useState("");
  const { busy, run } = useAction();

  // 当科室变化时，重置亚专科选择
  useEffect(() => {
    setSubspecialty("");
  }, [department]);

  // 获取当前选中科室的亚专科列表
  const currentDept = useMemo(() =>
    deptCatalog?.departments.find(d => d.code === department),
    [deptCatalog, department]);

  // 筛选后的病例列表
  const rows = useMemo(() => (data || [])
    .filter((c) => !department || c.department_code === department ||
      (c.subspecialty_code && deptCatalog?.departments.find(d => d.code === department)?.subspecialties.some(s => s.code === c.subspecialty_code)))
    .filter((c) => !subspecialty || c.subspecialty_code === subspecialty)
    .filter((c) => `${c.title}${c.public_id}${c.chief_complaint}`.toLowerCase().includes(query.trim().toLowerCase()))
    .sort((a, b) => a.public_id.localeCompare(b.public_id)), [data, query, department, subspecialty, deptCatalog]);

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
          <select className="select" style={{ width: 140 }} value={department} onChange={(e) => setDepartment(e.target.value)} aria-label="科室">
            <option value="">全部科室</option>
            {(deptCatalog?.departments || []).map((d) => <option key={d.code} value={d.code}>{d.name}</option>)}
          </select>
          {currentDept && currentDept.subspecialties.length > 0 && (
            <select className="select" style={{ width: 160 }} value={subspecialty} onChange={(e) => setSubspecialty(e.target.value)} aria-label="亚专科">
              <option value="">全部{currentDept.name}专业</option>
              {currentDept.subspecialties.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
            </select>
          )}
          <input className="input" style={{ width: 220 }} placeholder="搜索主诉或编号" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="搜索" />
        </div>
      </div>
      {deptCatalogError && <div role="alert" className="empty">科室筛选暂不可用：{String(deptCatalogError)}</div>}
      {deptCatalog && !deptCatalog.configured && <div role="status" className="empty">尚无院校启用的科室目录；当前不能开始新训练。</div>}
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

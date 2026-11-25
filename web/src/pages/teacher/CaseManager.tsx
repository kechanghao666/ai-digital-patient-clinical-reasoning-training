import { useMemo, useRef, useState } from "react";
import { ApiError, enc, get, mutate, request } from "../../api";
import { REVIEW_STATUS, type DepartmentCatalog } from "../../types";
import { Loading, Msg, Panel, useAction, useLoad } from "../../ui";

interface Version { case_id: string; public_id: string; version: number; title: string; specialty: string; department_code: string | null; subspecialty_code: string | null; review_status: string; source: string; source_case_id: string | null; source_version: number | null; authored_by: string | null; reviewer: string | null; review_date: string | null; training_stopped: boolean; stopped_at: number | null; stopped_by: string | null; stop_reason: string | null; }
interface AuditEvent { case_id: string; case_version: number; public_id: string; action: string; actor_id: string; occurred_at: string; status_from?: string | null; status_to?: string | null; reviewer_label?: string; review_date?: string; reason?: string; }
interface Issue { level: string; code: string; message: string; }
type CaseJson = Record<string, any>;
const ADVANCED = [["facts", "症状与体征"], ["exams", "检查与回报"], ["treatments", "模拟处置"], ["differential_options", "鉴别候选"], ["rules", "病情演进规则"], ["rubric", "评分量表"]] as const;
const STATUS_TONE: Record<string, string> = { draft: "", validated: "blue", under_review: "warn", published: "ok" };
const AUDIT_LABEL: Record<string, string> = {
  draft_saved: "保存草稿", case_validated: "结构校验通过", case_submitted_for_review: "提交医学审核",
  case_withdrawn: "撤回为草稿", case_published: "发布版本", training_stopped: "停止新训练",
};

function newDraft(source: CaseJson, copiedFromCurrent = false): CaseJson {
  const data = structuredClone(source);
  Object.assign(data, { case_id: `TEACH-${Math.random().toString(16).slice(2, 10)}`, title: `${data.title}（新草稿）`, version: 1,
    review_status: "draft", reviewer: null, review_date: null, authored_by: null,
    source_case_id: copiedFromCurrent ? source.case_id : (data.source_case_id || null),
    source_version: copiedFromCurrent ? source.version : (data.source_version || null),
    department_code: data.department_code || null, subspecialty_code: data.subspecialty_code || null });
  delete data.public_id;
  return data;
}

const fmtTime = (value?: number | null) => (value ? new Date(value * 1000).toLocaleString("zh-CN") : "");
const fmtAuditTime = (value: string) => new Date(value).toLocaleString("zh-CN");

export function CaseManager({ selected }: { selected?: string }) {
  const { data: versions, error, reload } = useLoad(() => get<Version[]>("/api/teacher/cases"), []);
  const { data: deptCatalog, error: deptCatalogError } = useLoad(() => get<DepartmentCatalog>("/api/teacher/department-catalog"), []);
  const [current, setCurrent] = useState<Version | null>(null);
  const [draft, setDraft] = useState<CaseJson | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [jsonText, setJsonText] = useState<Record<string, string>>({});
  const [issues, setIssues] = useState<{ valid?: boolean; issues: Issue[] } | null>(null);
  const [coverage, setCoverage] = useState<{ missing_clinical_stages: string[]; notice: string } | null>(null);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [publish, setPublish] = useState({ reviewer: "", date: "", confirm: false });
  const [stopOpen, setStopOpen] = useState(false);
  const [stopReason, setStopReason] = useState("");
  const [stopConfirm, setStopConfirm] = useState(false);
  const [filter, setFilter] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const { busy, run } = useAction();
  void selected;

  // 获取当前选中科室的亚专科列表
  const currentDept = useMemo(() =>
    deptCatalog?.departments.find(d => d.code === draft?.department_code),
    [deptCatalog, draft?.department_code]);
  const departmentListed = Boolean(deptCatalog?.departments.some(d => d.code === draft?.department_code));
  const subspecialtyListed = Boolean(currentDept?.subspecialties.some(s => s.code === draft?.subspecialty_code));

  const grouped = useMemo(() => (versions || []).filter((v) => `${v.title} ${v.case_id} ${v.public_id} ${v.specialty}`.toLowerCase().includes(filter.toLowerCase()))
    .sort((a, b) => a.public_id.localeCompare(b.public_id) || b.version - a.version), [versions, filter]);
  if (!versions) return <Loading error={error} onRetry={reload} />;

  const path = (v: { case_id: string; version: number }) => `/api/teacher/cases/${enc(v.case_id)}/versions/${v.version}`;
  const loadEditor = (raw: CaseJson, meta: Version | null) => {
    setDraft(raw); setCurrent(meta); setIssues(null); setAdvanced(false); setAuditEvents([]);
    setJsonText(Object.fromEntries(ADVANCED.map(([k]) => [k, JSON.stringify(raw[k] ?? [], null, 2)])));
    setPublish({ reviewer: "", date: "", confirm: false });
    setStopOpen(false); setStopReason(""); setStopConfirm(false);
  };
  const open = (v: Version) => run("open", async () => {
    const [raw, cov, audit] = await Promise.all([
      get<CaseJson>(path(v)), get(path(v) + "/teaching-basis"), get<AuditEvent[]>(path(v) + "/audit"),
    ]);
    setCoverage(cov); loadEditor(raw, v); setAuditEvents(audit);
  });
  const set = (key: string, value: unknown) => setDraft((d) => ({ ...d!, [key]: value }));
  const compose = (): CaseJson => {
    const data = { ...draft! };
    if (advanced) for (const [k] of ADVANCED) data[k] = JSON.parse(jsonText[k]);
    data.learning_goals = (data.learning_goals || []).filter((x: string) => x.trim());
    data.sources = (data.sources || []).filter((x: string) => x.trim());
    data.hidden_diagnosis = (data.hidden_diagnosis || "").trim() || null;
    return data;
  };
  const validate = () => run("validate", async () => {
    let data: CaseJson;
    try { data = compose(); } catch { throw new ApiError(400, "高级配置不是有效的 JSON，尚未校验"); }
    setIssues(await request("POST", "/api/teacher/cases/validate", { public_id: "CASE-T-DRAFT", ...data }));
  });
  const save = () => run("save", async () => {
    let data: CaseJson;
    try { data = compose(); } catch { throw new ApiError(400, "高级配置不是有效的 JSON，尚未保存"); }
    if (!String(data.case_id || "").trim() || !String(data.title || "").trim()) throw new ApiError(400, "请填写病例编号和标题");
    try {
      const result = await request<Version & { issues: Issue[] }>("PUT", `/api/teacher/cases/${enc(data.case_id)}`, data);
      await reload();
      const list = await get<Version[]>("/api/teacher/cases");
      const meta = list.find((v) => v.case_id === result.case_id && v.version === result.version) || null;
      loadEditor(await get<CaseJson>(path(result)), meta);
      setAuditEvents(await get<AuditEvent[]>(path(result) + "/audit"));
      setIssues({ issues: result.issues });
    } catch (e) {
      if (e instanceof ApiError && (e.detail as any)?.issues) setIssues({ valid: false, issues: (e.detail as any).issues });
      throw e;
    }
  }, "已保存为新版本草稿（尚未医学审核）");
  const transition = (action: string, body?: unknown) => current && run(action, async () => {
    await request("POST", `${path(current)}/${action}`, body);
    await reload();
    const list = await get<Version[]>("/api/teacher/cases");
    setCurrent(list.find((v) => v.case_id === current.case_id && v.version === current.version) || null);
    setAuditEvents(await get<AuditEvent[]>(path(current) + "/audit"));
  }, "状态已更新");
  const stopVersion = () => current && run("stop", async () => {
    await mutate(`stop-case:${current.case_id}:${current.version}`, "POST",
      `/api/teacher/cases/${enc(current.case_id)}/versions/${current.version}/stop`, { reason: stopReason.trim() });
    await reload();
    const list = await get<Version[]>("/api/teacher/cases");
    setCurrent(list.find((v) => v.case_id === current.case_id && v.version === current.version) || null);
    setAuditEvents(await get<AuditEvent[]>(path(current) + "/audit"));
    setStopOpen(false); setStopReason(""); setStopConfirm(false);
  }, "已停止该版本接受新训练，已有训练与评分保留");
  const importFile = async (file: File) => {
    try {
      const raw = JSON.parse((await file.text()).replace(/^﻿/, ""));
      if (!raw || typeof raw !== "object" || !raw.title) throw new Error("文件必须是包含病例标题的对象");
      loadEditor(newDraft(raw), null);
    } catch (e) { setIssues({ valid: false, issues: [{ level: "error", code: "import", message: `无法读取病例：${(e as Error).message}` }] }); }
  };
  const download = () => {
    const blob = new Blob([JSON.stringify(compose(), null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${draft?.case_id || "case"}.json`; a.click();
  };
  const isTeacherCase = current?.source === "teacher";
  const status = current?.review_status;

  return (
    <div className="stack-lg">
      <div className="page-title">
        <h1>病例管理</h1>
        <div className="row">
          <input ref={fileInput} type="file" accept="application/json,.json" hidden onChange={(e) => e.target.files?.[0] && importFile(e.target.files[0])} />
          <button className="btn" onClick={() => fileInput.current?.click()}>导入 JSON</button>
        </div>
      </div>
      <div className="row" style={{ gap: 28 }}>
        <span className="small"><span className="faint">病例版本</span> <b>{versions.length}</b></span>
        <span className="small"><span className="faint">教师草稿</span> <b>{versions.filter((v) => v.source === "teacher").length}</b></span>
        <span className="small"><span className="faint">待医学审核</span> <b>{versions.filter((v) => v.review_status === "under_review").length}</b></span>
        <span className="small"><span className="faint">已发布</span> <b>{versions.filter((v) => v.review_status === "published").length}</b></span>
      </div>
      <div className="split">
        <section className="panel" aria-label="病例版本列表">
          <div className="panel-head"><input className="input" placeholder="搜索病例或编号" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="搜索病例" /></div>
          <div className="" style={{ maxHeight: "70vh", overflowY: "auto" }}>
            {grouped.map((v) => (
              <button key={`${v.case_id}-${v.version}`} className="list-item" aria-current={current?.case_id === v.case_id && current.version === v.version}
                onClick={() => open(v)}>
                <div className="spread"><b style={{ fontWeight: 500 }}>{v.public_id}　v{v.version}</b>
                  <span className="row">{v.training_stopped && <span className="tag">已停止新训练</span>}
                    <span className="tag">{REVIEW_STATUS[v.review_status] || v.review_status}</span></span></div>
                <span className="small">{v.title}</span>
                <span className="faint">{v.specialty}　{v.source === "teacher" ? "教师病例" : "内置病例"}{v.source_case_id && `　来源 ${v.source_case_id} v${v.source_version}`}</span>
              </button>
            ))}
          </div>
        </section>
        {!draft ? <Panel><div className="empty">选择左侧病例版本</div></Panel> : (
          <div className="stack">
            {deptCatalogError && <Msg tone="bad">科室目录读取失败：{String(deptCatalogError)}</Msg>}
            {deptCatalog && !deptCatalog.configured && <Msg tone="warn">部署尚未配置院校启用目录。教师无权修改此设置；请部署管理员运行 scripts/configure_department_catalog.py。</Msg>}
            {coverage?.missing_clinical_stages?.length && current ? <Msg tone="warn">该病例缺少专科临床依据：{coverage.missing_clinical_stages.join("、")}。</Msg> : null}
            <Panel title={current ? `${current.public_id}　v${current.version}　${REVIEW_STATUS[current.review_status]}` : "新草稿（未保存）"} actions={<>
              {current && <button className="btn " onClick={() => loadEditor(newDraft(draft, true), null)}>复制为新草稿</button>}
              <button className="btn " onClick={download}>下载 JSON</button></>}>
              <div className="stack">
                {draft.source_case_id && <Msg tone="info">来源病例：{draft.source_case_id} v{draft.source_version}。保存后该来源不可更改。</Msg>}
                <div className="form-grid">
                  <label className="field"><span>病例编号</span><input className="input mono" value={draft.case_id || ""} onChange={(e) => set("case_id", e.target.value)} />
                    <small>3–40 位字母、数字、短横线或下划线</small></label>
                  <label className="field"><span>病例标题</span><input className="input" value={draft.title || ""} onChange={(e) => set("title", e.target.value)} /></label>
                  <label className="field"><span>科室</span>
                    <select className="select" value={draft.department_code || ""} onChange={(e) => {
                      const dept = e.target.value || null;
                      setDraft((d) => ({ ...d!, department_code: dept, subspecialty_code: null }));
                    }}>
                      <option value="">未选择</option>
                      {draft.department_code && !departmentListed && <option value={draft.department_code} disabled>历史分类 {draft.department_code}（当前未启用）</option>}
                      {(deptCatalog?.departments || []).map((d) => <option key={d.code} value={d.code}>{d.name}</option>)}
                    </select>
                    <small>发布前须选择科室</small>
                  </label>
                  <label className="field"><span>亚专科</span>
                    <select className="select" value={draft.subspecialty_code || ""} onChange={(e) => set("subspecialty_code", e.target.value || null)}
                      disabled={!currentDept || currentDept.subspecialties.length === 0}>
                      <option value="">未选择</option>
                      {draft.subspecialty_code && !subspecialtyListed && <option value={draft.subspecialty_code} disabled>历史分类 {draft.subspecialty_code}（当前未启用）</option>}
                      {(currentDept?.subspecialties || []).map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
                    </select>
                    <small>{currentDept && currentDept.subspecialties.length > 0 ? "发布前必须选择" : "该科室无已配置亚专科，可按科室级分类"}</small>
                  </label>
                  <label className="field"><span>专科（原字段）</span><input className="input" value={draft.specialty || ""} onChange={(e) => set("specialty", e.target.value)} />
                    <small>保留展示兼容，优先使用科室/亚专科</small></label>
                  <label className="field"><span>适用人群</span><input className="input" value={draft.target_population || ""} onChange={(e) => set("target_population", e.target.value)} /></label>
                  <label className="field"><span>主诉</span><input className="input" value={draft.chief_complaint || ""} onChange={(e) => set("chief_complaint", e.target.value)} /></label>
                  <label className="field"><span>隐藏参考诊断（仅教师可见）</span><input className="input" value={draft.hidden_diagnosis || ""} onChange={(e) => set("hidden_diagnosis", e.target.value)} /></label>
                  <label className="field full"><span>教学目标（每行一项）</span><textarea className="textarea" style={{ minHeight: 70 }} value={(draft.learning_goals || []).join("\n")} onChange={(e) => set("learning_goals", e.target.value.split("\n"))} /></label>
                </div>
                {!advanced && <>
                  <h4>症状与体征（{(draft.facts || []).length} 条）</h4>
                  <p className="faint">患者仅在问题命中触发词时回答该条，多个触发词用“、”分隔。</p>
                  <div className="table-wrap"><table className="table edit-table">
                    <thead><tr><th style={{ width: 110 }}>字段</th><th>患者回答</th><th style={{ width: "30%" }}>询问触发词</th><th>知晓</th></tr></thead>
                    <tbody>{(draft.facts || []).map((f: any, i: number) => (
                      <tr key={f.fact_id}>
                        <td className="small">{f.field}<div className="faint mono">{f.fact_id}</div></td>
                        <td><textarea className="textarea" value={f.content} onChange={(e) => set("facts", draft.facts.map((x: any, j: number) => j === i ? { ...x, content: e.target.value } : x))} /></td>
                        <td><textarea className="textarea" value={(f.disclosure_triggers || []).join("、")} onChange={(e) => set("facts", draft.facts.map((x: any, j: number) => j === i ? { ...x, disclosure_triggers: e.target.value.split("、").map((t) => t.trim()).filter(Boolean) } : x))} /></td>
                        <td><input type="checkbox" checked={f.patient_known !== false} onChange={(e) => set("facts", draft.facts.map((x: any, j: number) => j === i ? { ...x, patient_known: e.target.checked } : x))} aria-label="患者知晓" /></td>
                      </tr>))}</tbody>
                  </table></div>
                  <h4>检查与回报（{(draft.exams || []).length} 项）</h4>
                  <div className="table-wrap"><table className="table edit-table">
                    <thead><tr><th style={{ width: 160 }}>检查名称</th><th>结果内容</th><th style={{ width: 100 }}>回报（分钟）</th><th>已配置</th></tr></thead>
                    <tbody>{(draft.exams || []).map((x: any, i: number) => (
                      <tr key={x.exam_id}>
                        <td><input className="input" value={x.standard_name} onChange={(e) => set("exams", draft.exams.map((y: any, j: number) => j === i ? { ...y, standard_name: e.target.value } : y))} /></td>
                        <td><textarea className="textarea" value={x.result || ""} onChange={(e) => set("exams", draft.exams.map((y: any, j: number) => j === i ? { ...y, result: e.target.value || null } : y))} /></td>
                        <td><input className="input num" type="number" min={0} value={x.return_after_minutes} onChange={(e) => set("exams", draft.exams.map((y: any, j: number) => j === i ? { ...y, return_after_minutes: Math.max(0, Number(e.target.value) || 0) } : y))} /></td>
                        <td><input type="checkbox" checked={x.configured !== false} onChange={(e) => set("exams", draft.exams.map((y: any, j: number) => j === i ? { ...y, configured: e.target.checked } : y))} aria-label="已配置结果" /></td>
                      </tr>))}</tbody>
                  </table></div>
                </>}
                <label className="check"><input type="checkbox" checked={advanced} onChange={(e) => {
                  if (e.target.checked) setJsonText(Object.fromEntries(ADVANCED.map(([k]) => [k, JSON.stringify(draft[k] ?? [], null, 2)])));
                  setAdvanced(e.target.checked);
                }} />编辑完整结构化配置（JSON，优先于上方表格）</label>
                {advanced && <div className="stack">
                  <Msg tone="info">检查须显式开单；即时演进的触发名须对应某项处置的 trigger，延误规则须指定已配置检查。处置评分可配置 required_prior_exam_ids。程序校验不代表医学审核。</Msg>
                  {ADVANCED.map(([k, label]) => <label key={k} className="field"><span>{label}（{k}）</span>
                    <textarea className="textarea mono" style={{ minHeight: 160 }} value={jsonText[k] || ""} onChange={(e) => setJsonText({ ...jsonText, [k]: e.target.value })} /></label>)}
                </div>}
                <label className="field"><span>来源说明（每行一项）</span><textarea className="textarea" style={{ minHeight: 60 }} value={(draft.sources || []).join("\n")} onChange={(e) => set("sources", e.target.value.split("\n"))} /></label>
                {issues && <div className="stack">
                  {issues.valid && <Msg tone="ok">结构校验通过。结构校验不代表医学审核。</Msg>}
                  {issues.issues.map((i, n) => <Msg key={n} tone={i.level === "error" ? "bad" : "warn"}>{i.message}</Msg>)}
                </div>}
                <div className="actions">
                  <button className="btn" disabled={busy !== null} onClick={validate}>校验病例</button>
                  <button className="btn btn-primary" disabled={busy !== null} onClick={save}>{busy === "save" ? "保存中…" : "保存为新版本草稿"}</button>
                </div>
              </div>
            </Panel>
            {current && <Panel title="审核与发布">
              {!isTeacherCase ? <p className="faint">内置病例不能直接送审。请“复制为新草稿”并保存后，再进行校验和送审。</p> : (
                <div className="stack">
                  <div className="row">
                    {["draft", "validated", "under_review", "published"].map((s, i) => <span key={s} className="row"><span className="tag" style={status === s ? { borderColor: "var(--cyan)", color: "var(--cyan)", fontWeight: 600 } : undefined}>{i + 1}. {REVIEW_STATUS[s]}</span>{i < 3 && <span className="faint">→</span>}</span>)}
                  </div>
                  <div className="row">
                    {status === "draft" && <button className="btn" disabled={busy !== null} onClick={() => transition("validate")}>标记结构校验通过</button>}
                    {status === "validated" && <button className="btn btn-primary" disabled={busy !== null} onClick={() => transition("submit-review")}>提交医学审核</button>}
                    {(status === "validated" || status === "under_review") && <button className="btn" disabled={busy !== null} onClick={() => transition("withdraw")}>撤回为草稿</button>}
                  </div>
                  {status === "under_review" && <div className="stack">
                    <Msg tone="warn">仅在真实医学审核完成后填写。结构校验或程序测试不属于医学审核。</Msg>
                    <div className="form-grid">
                      <label className="field"><span>真实医学审核人</span><input className="input" value={publish.reviewer} onChange={(e) => setPublish({ ...publish, reviewer: e.target.value })} /></label>
                      <label className="field"><span>审核日期</span><input className="input" type="date" max={new Date().toISOString().slice(0, 10)} value={publish.date} onChange={(e) => setPublish({ ...publish, date: e.target.value })} /></label>
                    </div>
                    <label className="check"><input type="checkbox" checked={publish.confirm} onChange={(e) => setPublish({ ...publish, confirm: e.target.checked })} />我确认该版本已由上述人员完成真实医学审核</label>
                    <div><button className="btn btn-primary" disabled={!publish.confirm || !publish.reviewer.trim() || !publish.date || busy !== null}
                      onClick={() => transition("publish", { reviewer: publish.reviewer.trim(), review_date: publish.date, confirm_medical_review: true })}>确认审核并发布</button></div>
                  </div>}
                  {status === "published" && (current.training_stopped ? (
                    <Msg tone="warn">此版本已停止接受新训练（{current.public_id} v{current.version}）：操作者 {current.stopped_by}，时间 {fmtTime(current.stopped_at)}，原因：{current.stop_reason}。已有训练与封存评分仍可按原版本回看。</Msg>
                  ) : (
                    <div className="stack">
                      <Msg tone="ok">此版本已发布（审核人 {current.reviewer}，{current.review_date}）。需要修改时请复制为新草稿。</Msg>
                      {!stopOpen && <div>
                        <button className="btn" disabled={busy !== null} onClick={() => setStopOpen(true)}>停止新训练</button>
                      </div>}
                      {stopOpen && <div className="stack">
                        <Msg tone="warn">将对 {current.public_id} v{current.version} 停止接受新训练。不会删除病例、不会改写医学审核结论、不会解封已封存评分；已有训练与评分保留。</Msg>
                        <label className="field"><span>停止原因（教师可见）</span>
                          <textarea className="textarea" maxLength={500} value={stopReason} onChange={(e) => setStopReason(e.target.value)} /></label>
                        <label className="check"><input type="checkbox" checked={stopConfirm} onChange={(e) => setStopConfirm(e.target.checked)} />我确认停止该版本接受新训练</label>
                        <div className="row">
                          <button className="btn btn-primary" disabled={!stopReason.trim() || !stopConfirm || busy !== null}
                            onClick={stopVersion}>{busy === "stop" ? "停止中…" : "确认停止新训练"}</button>
                          <button className="btn" disabled={busy !== null} onClick={() => { setStopOpen(false); setStopReason(""); setStopConfirm(false); }}>取消</button>
                        </div>
                      </div>}
                    </div>
                  ))}
                </div>
              )}
            </Panel>}
            {current?.source === "teacher" && <Panel title="病例操作审计">
              {auditEvents.length === 0 ? <div className="empty">未发现已记录的操作事件；功能启用前的历史操作可能未留痕</div> : <div className="stack">
                {auditEvents.map((event, index) => <div className="spread" key={`${event.action}-${event.occurred_at}-${index}`}>
                  <div><b>{AUDIT_LABEL[event.action] || event.action}</b>
                    {event.status_from && event.status_to && <span className="faint">　{REVIEW_STATUS[event.status_from] || event.status_from} → {REVIEW_STATUS[event.status_to] || event.status_to}</span>}
                    {event.reviewer_label && <div className="faint">审核人声明：{event.reviewer_label}（系统未核验资质）</div>}
                    {event.reason && <div className="faint">原因：{event.reason}</div>}
                  </div>
                  <span className="faint">{fmtAuditTime(event.occurred_at)} · {event.actor_id}</span>
                </div>)}
              </div>}
            </Panel>}
          </div>
        )}
      </div>
    </div>
  );
}

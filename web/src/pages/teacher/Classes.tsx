import { useState, type FormEvent } from "react";
import { enc, get, request } from "../../api";
import { navigate } from "../../App";
import type { CaseSummary } from "../../types";
import { downloadCsv, Loading, Msg, Panel, pct, useAction, useLoad } from "../../ui";

interface ClassRow { id: string; name: string; students: number; assignments: number; archived: boolean; created_at: number; }
interface Member { id: string; username: string; display_name: string; disabled: boolean; must_change_password: boolean; trainings: number; sealed: number; average_ratio: number | null; }
interface Progress { status: string; best_ratio: number | null; attempts: number; latest_sealed_session_id: string | null; needs_review: boolean; }
interface Task { id: string; title: string; case_public_id: string; due_date: string | null; note: string; case: { title: string; available: boolean };
  progress: Record<string, Progress>; completed: number; in_progress: number; average_ratio: number | null; }
interface Detail extends Omit<ClassRow, "assignments" | "students"> { members: Member[]; assignments: Task[]; }
interface Issued { username: string; display_name?: string; status: string; temporary_password: string | null; message?: string; }

export function Classes({ classId }: { classId?: string }) {
  return classId ? <ClassDetail classId={classId} /> : <ClassList />;
}

function ClassList() {
  const { data, error, reload } = useLoad(() => get<ClassRow[]>("/api/teacher/classes"), []);
  const [name, setName] = useState("");
  const { busy, run } = useAction();
  if (!data) return <Loading error={error} onRetry={reload} />;
  const create = (e: FormEvent) => {
    e.preventDefault();
    run("create", async () => { const c = await request<ClassRow>("POST", "/api/teacher/classes", { name }); setName(""); navigate(`/teacher/classes/${c.id}`); }, "班级已创建");
  };
  return (
    <div className="stack-lg">
      <div className="page-title"><h1>班级与任务</h1>
        <form className="row" onSubmit={create}><input className="input" style={{ width: 220 }} placeholder="新班级名称，如 临床2024级1班" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} aria-label="班级名称" />
          <button className="btn btn-primary" disabled={!name.trim() || busy !== null}>新建班级</button></form></div>
      <section className="panel">
        {data.length === 0 ? <div className="empty">还没有班级。新建班级后可导入学生并布置训练任务。</div> : (
          <table className="table"><thead><tr><th>班级</th><th className="num">学生</th><th className="num">进行中任务</th><th>状态</th><th /></tr></thead>
            <tbody>{data.map((c) => (
              <tr key={c.id}><td style={{ fontWeight: 600 }}>{c.name}</td><td className="num">{c.students}</td><td className="num">{c.assignments}</td>
                <td>{c.archived ? <span className="status">已归档</span> : <span className="status ok">使用中</span>}</td>
                <td className="num"><button className="btn" onClick={() => navigate(`/teacher/classes/${c.id}`)}>管理</button></td></tr>))}</tbody></table>
        )}
      </section>
    </div>
  );
}

function parseRoster(text: string) {
  return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
    .map((line) => line.split(/[,，\t ]+/)).filter(([u]) => u && !/^(账号|学号|username)$/i.test(u))
    .map(([username, display_name = ""]) => ({ username, display_name }));
}

function ClassDetail({ classId }: { classId: string }) {
  const { data, error, reload } = useLoad(() => get<Detail>(`/api/teacher/classes/${enc(classId)}`), [classId]);
  const { data: cases } = useLoad(() => get<CaseSummary[]>("/api/cases"), []);
  const [tab, setTab] = useState<"tasks" | "students">("tasks");
  const [roster, setRoster] = useState("");
  const [issued, setIssued] = useState<Issued[] | null>(null);
  const [task, setTask] = useState({ case_public_id: "", due_date: "", note: "" });
  const { busy, run } = useAction();
  if (!data) return <Loading error={error} onRetry={reload} />;
  const parsed = parseRoster(roster);
  const exportIssued = (rows: Issued[]) => downloadCsv(`${data.name}-初始密码.csv`,
    [["账号", "姓名", "初始密码", "结果"], ...rows.map((r) => [r.username, r.display_name, r.temporary_password, r.status === "created" ? "新建" : r.status === "enrolled" ? "已有账号，已加入" : r.message || "未处理"])]);
  const addStudents = () => run("add", async () => {
    const r = await request<{ results: Issued[] }>("POST", `/api/teacher/classes/${enc(classId)}/students`, { students: parsed });
    setIssued(r.results); setRoster(""); await reload();
  }, "名单已导入");
  const reset = (m: Member) => window.confirm(`重置 ${m.display_name} 的密码？原密码立即失效。`) && run("reset", async () => {
    const r = await request<{ username: string; temporary_password: string }>("POST", `/api/teacher/students/${enc(m.id)}/reset-password`);
    setIssued([{ username: r.username, display_name: m.display_name, status: "reset", temporary_password: r.temporary_password }]);
  });
  const toggle = (m: Member) => run("toggle", async () => { await request("POST", `/api/teacher/students/${enc(m.id)}/${m.disabled ? "enable" : "disable"}`); await reload(); });
  const remove = (m: Member) => window.confirm(`将 ${m.display_name} 移出本班？账号和训练记录保留。`) && run("remove", async () => { await request("DELETE", `/api/teacher/classes/${enc(classId)}/students/${enc(m.id)}`); await reload(); });
  const assign = (e: FormEvent) => {
    e.preventDefault();
    run("assign", async () => { await request("POST", `/api/teacher/classes/${enc(classId)}/assignments`, { ...task, due_date: task.due_date || null }); setTask({ case_public_id: "", due_date: "", note: "" }); await reload(); }, "任务已布置");
  };
  const archiveTask = (t: Task) => window.confirm(`撤回任务“${t.title}”？已完成的训练记录保留。`) && run("archive", async () => { await request("DELETE", `/api/teacher/assignments/${enc(t.id)}`); await reload(); });
  const exportProgress = () => downloadCsv(`${data.name}-任务完成情况.csv`, [
    ["账号", "姓名", ...data.assignments.map((t) => `${t.title}(${t.case_public_id})`)],
    ...data.members.map((m) => [m.username, m.display_name, ...data.assignments.map((t) => {
      const p = t.progress[m.id];
      return !p || p.status === "not_started" ? "未开始" : p.status === "in_progress" ? "进行中" : pct(p.best_ratio);
    })])]);

  return (
    <div className="stack-lg">
      <div className="page-title">
        <div className="row"><button className="btn" onClick={() => navigate("/teacher/classes")}>返回</button><h1>{data.name}</h1>
          <span className="faint">{data.members.length} 名学生　{data.assignments.length} 项任务</span></div>
        <div className="row">
          <button className="btn" onClick={() => navigate(`/teacher/dashboard?class_id=${enc(classId)}`)}>本班学情</button>
          <button className="btn" disabled={!data.members.length} onClick={exportProgress}>导出完成情况</button>
        </div>
      </div>
      {issued && issued.length > 0 && (
        <Msg tone="warn"><div className="stack" style={{ gap: 6, width: "100%" }}>
          <div className="spread"><b>以下密码只显示这一次，请导出后发给学生（首次登录须修改）</b>
            <span className="row"><button className="btn" onClick={() => exportIssued(issued)}>导出 CSV</button><button className="btn" onClick={() => setIssued(null)}>关闭</button></span></div>
          <table className="table"><tbody>{issued.map((r) => (
            <tr key={r.username}><td className="mono">{r.username}</td><td>{r.display_name}</td>
              <td className="mono">{r.temporary_password || "—"}</td><td>{r.status === "created" ? "新建" : r.status === "enrolled" ? "已有账号，已加入" : r.status === "reset" ? "已重置" : r.message}</td></tr>))}</tbody></table>
        </div></Msg>
      )}
      <div className="segmented" role="group" aria-label="视图">
        <button aria-pressed={tab === "tasks"} onClick={() => setTab("tasks")}>训练任务</button>
        <button aria-pressed={tab === "students"} onClick={() => setTab("students")}>学生名单</button>
      </div>

      {tab === "tasks" && <>
        <Panel title="布置任务">
          <form className="row" onSubmit={assign} style={{ alignItems: "flex-end" }}>
            <label className="field" style={{ width: 300 }}><span>病例</span>
              <select className="select" value={task.case_public_id} onChange={(e) => setTask({ ...task, case_public_id: e.target.value })} required>
                <option value="">选择病例</option>
                {(cases || []).sort((a, b) => a.public_id.localeCompare(b.public_id)).map((c) => <option key={c.public_id} value={c.public_id}>{c.public_id} {c.chief_complaint}（{c.specialty}）</option>)}
              </select></label>
            <label className="field"><span>截止日期</span><input className="input" type="date" value={task.due_date} onChange={(e) => setTask({ ...task, due_date: e.target.value })} /></label>
            <label className="field grow"><span>要求（选填）</span><input className="input" maxLength={500} value={task.note} onChange={(e) => setTask({ ...task, note: e.target.value })} placeholder="如：重点完成鉴别诊断" /></label>
            <button className="btn btn-primary" disabled={!task.case_public_id || busy !== null}>布置</button>
          </form>
        </Panel>
        <Panel title="完成情况" flush>
          {data.assignments.length === 0 ? <div className="empty">尚未布置任务</div> : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>学生</th>{data.assignments.map((t) => (
                <th key={t.id} style={{ whiteSpace: "normal", minWidth: 130 }}>{t.case_public_id} {t.title}
                  <div className="faint" style={{ fontWeight: 400 }}>截止 {t.due_date || "不限"}　完成 {t.completed}/{data.members.length}　均分 {pct(t.average_ratio)}</div>
                  <button className="btn-link btn small" onClick={() => archiveTask(t)}>撤回</button></th>))}</tr></thead>
              <tbody>{data.members.map((m) => (
                <tr key={m.id}><td>{m.display_name}<div className="faint mono">{m.username}</div></td>
                  {data.assignments.map((t) => {
                    const p = t.progress[m.id];
                    if (!p || p.status === "not_started") return <td key={t.id}><span className="status">未开始</span></td>;
                    if (p.status === "in_progress") return <td key={t.id}><span className="status info">进行中</span></td>;
                    return <td key={t.id}><span className={`status ${p.best_ratio != null && p.best_ratio >= 0.6 ? "ok" : "warn"}`}>{pct(p.best_ratio)}</span>
                      {p.needs_review && <span className="faint"> 待复核</span>}
                      {p.latest_sealed_session_id && <div><button className="btn-link btn small" onClick={() => navigate(`/teacher/scores?owner=${enc(m.id)}&session=${enc(p.latest_sealed_session_id!)}`)}>评分复核</button></div>}</td>;
                  })}</tr>))}</tbody>
            </table></div>
          )}
        </Panel>
      </>}

      {tab === "students" && <>
        <Panel title="导入学生">
          <div className="stack">
            <textarea className="textarea mono" value={roster} onChange={(e) => setRoster(e.target.value)} placeholder={"每行一名学生：账号,姓名\n2024001,张三\n2024002,李四"} aria-label="学生名单" />
            <div className="spread"><span className="faint">识别到 {parsed.length} 名。新账号自动生成初始密码；已有学生账号直接加入本班。也可从 Excel 复制两列粘贴。</span>
              <button className="btn btn-primary" disabled={!parsed.length || busy !== null} onClick={addStudents}>导入</button></div>
          </div>
        </Panel>
        <Panel title={`学生名单（${data.members.length}）`} flush>
          {data.members.length === 0 ? <div className="empty">暂无学生</div> : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>账号</th><th>姓名</th><th>账号状态</th><th className="num">训练次数</th><th className="num">已完成</th><th className="num">平均得分率</th><th /></tr></thead>
              <tbody>{data.members.map((m) => (
                <tr key={m.id}><td className="mono">{m.username}</td><td>{m.display_name}</td>
                  <td>{m.disabled ? <span className="status bad">已停用</span> : m.must_change_password ? <span className="status warn">待首次登录</span> : <span className="status ok">正常</span>}</td>
                  <td className="num">{m.trainings}</td><td className="num">{m.sealed}</td><td className="num">{pct(m.average_ratio)}</td>
                  <td className="num" style={{ whiteSpace: "nowrap" }}>
                    <button className="btn" onClick={() => reset(m)}>重置密码</button>{" "}
                    <button className="btn" onClick={() => toggle(m)}>{m.disabled ? "启用" : "停用"}</button>{" "}
                    <button className="btn btn-danger" onClick={() => remove(m)}>移出</button></td></tr>))}</tbody>
            </table></div>
          )}
        </Panel>
      </>}
    </div>
  );
}

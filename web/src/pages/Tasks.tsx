import { useState } from "react";
import { enc, get, request } from "../api";
import { navigate } from "../App";
import { Loading, pct, useAction, useLoad } from "../ui";

interface Task {
  id: string; title: string; note: string; due_date: string | null; class_name: string; case_public_id: string;
  case: { title: string; specialty: string; chief_complaint?: string; available: boolean; unavailable_reason?: string | null };
  status: "not_started" | "in_progress" | "completed"; attempts: number; best_ratio: number | null;
  open_session_id: string | null; latest_sealed_session_id: string | null; needs_review: boolean;
}
const STATUS = { not_started: ["未开始", ""], in_progress: ["进行中", "info"], completed: ["已完成", "ok"] } as const;

function dueLabel(due: string | null) {
  if (!due) return { text: "不限", tone: "" };
  const days = Math.ceil((new Date(due + "T23:59:59").getTime() - Date.now()) / 86400000);
  if (days < 0) return { text: `${due}（已截止）`, tone: "bad" };
  if (days <= 2) return { text: `${due}（剩 ${days} 天）`, tone: "warn" };
  return { text: due, tone: "" };
}

export function Tasks() {
  const { data, error, reload } = useLoad(() => get<Task[]>("/api/assignments"), []);
  const [show, setShow] = useState<"open" | "all">("open");
  const { busy, run } = useAction();
  if (!data) return <Loading error={error} onRetry={reload} />;
  const open = data.filter((t) => t.open_session_id || t.status !== "completed");
  const rows = show === "all" ? data : open;
  const start = (t: Task) => run(t.id, async () => {
    const r = await request<{ session_id: string }>("POST", `/api/assignments/${enc(t.id)}/start`);
    navigate(`/training/${r.session_id}`);
  });
  return (
    <div className="stack-lg">
      <div className="page-title">
        <h1>我的任务</h1>
        <div className="segmented" role="group" aria-label="筛选">
          <button aria-pressed={show === "open"} onClick={() => setShow("open")}>待完成 {open.length}</button>
          <button aria-pressed={show === "all"} onClick={() => setShow("all")}>全部 {data.length}</button>
        </div>
      </div>
      <section className="panel">
        {rows.length === 0 ? <div className="empty">{data.length ? "任务都已完成。" : "老师还没有布置任务，可以先到病例库自主练习。"}</div> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>任务</th><th>病例</th><th>班级</th><th>截止日期</th><th>状态</th><th className="num">最好成绩</th><th /></tr></thead>
            <tbody>{rows.map((t) => {
              const due = dueLabel(t.due_date);
              const [label, tone] = STATUS[t.status];
              return (
                <tr key={t.id}>
                  <td><div style={{ fontWeight: 600 }}>{t.title}</div>{t.note && <div className="faint">{t.note}</div>}</td>
                  <td>{t.case_public_id}<div className="faint">{t.case.specialty}</div></td>
                  <td>{t.class_name}</td>
                  <td><span className={due.tone ? `status ${due.tone}` : ""}>{due.text}</span></td>
                  <td><span className={`status ${tone}`}>{label}</span>{t.needs_review && <div className="faint">部分评分待教师复核</div>}</td>
                  <td className="num">{pct(t.best_ratio)}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {t.latest_sealed_session_id && <button className="btn" onClick={() => navigate(`/training/${t.latest_sealed_session_id}`)}>查看复盘</button>}{" "}
                    <button className={`btn ${t.status === "completed" ? "" : "btn-primary"}`} disabled={(!t.case.available && !t.open_session_id) || busy !== null} onClick={() => start(t)}>
                      {t.open_session_id ? "继续" : t.status === "completed" ? "再练一次" : "开始"}</button>
                    {!t.case.available && <div className="faint">{t.case.unavailable_reason === "stopped"
                      ? t.open_session_id ? "该版本已停止新训练，已有训练可继续。" : "该版本已停止新训练，请联系教师重新选版。"
                      : t.case.unavailable_reason === "department_disabled"
                        ? "该病例所属科室或亚专科当前未启用，请联系管理员。"
                        : "该病例当前不可用，请联系教师。"}</div>}
                  </td>
                </tr>
              );
            })}</tbody>
          </table></div>
        )}
      </section>
    </div>
  );
}

import { useState } from "react";
import { get } from "../api";
import { navigate } from "../App";
import type { SessionSummary } from "../types";
import { Loading, useLoad } from "../ui";

const SCORE: Record<string, [string, string]> = { complete: ["已评分", "ok"], needs_review: ["待教师复核", "warn"], failed: ["评分失败", "bad"] };

export function MyTrainings() {
  const { data, error, reload } = useLoad(() => get<SessionSummary[]>("/api/sessions"), []);
  const [filter, setFilter] = useState<"all" | "open" | "sealed">("all");
  if (!data) return <Loading error={error} onRetry={reload} />;
  const rows = data.filter((s) => filter === "all" || s.sealed === (filter === "sealed"));
  return (
    <div className="stack-lg">
      <div className="page-title">
        <h1>训练记录</h1>
        <div className="segmented" role="group" aria-label="筛选">
          {([["all", "全部"], ["open", "进行中"], ["sealed", "已完成"]] as const).map(([id, label]) =>
            <button key={id} aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>)}
        </div>
      </div>
      <section className="panel">
        {rows.length === 0 ? <div className="empty">暂无记录</div> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>训练编号</th><th>病例</th><th>主诉</th><th className="num">模拟用时</th><th className="num">操作数</th><th>状态</th><th /></tr></thead>
            <tbody>{rows.map((s) => (
              <tr key={s.session_id}>
                <td className="mono">{s.summary_id}</td>
                <td>{s.case.public_id} <span className="faint">v{s.case.version}</span><div className="faint">{s.case.specialty}</div></td>
                <td>{s.case.chief_complaint}</td>
                <td className="num">{s.sim_time_minutes} 分</td>
                <td className="num">{s.event_count}</td>
                <td>{s.sealed ? <span className={`status ${SCORE[s.score_status || ""]?.[1] || ""}`}>{SCORE[s.score_status || ""]?.[0] || "已完成"}</span> : <span className="status info">进行中</span>}</td>
                <td className="num"><button className="btn" onClick={() => navigate(`/training/${s.session_id}`)}>{s.sealed ? "复盘" : "继续"}</button></td>
              </tr>))}</tbody>
          </table></div>
        )}
      </section>
    </div>
  );
}

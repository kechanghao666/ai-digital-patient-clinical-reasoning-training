"""De-identified teaching-data export and its append-only audit.

The export is built from an explicit field whitelist — never a whole ``SessionState``, a database
row or a raw adapter trace. It excludes names, usernames, account/session internal ids, request ids,
tokens, contact details, precise wall-clock times, vendor text and internal diagnostics, and it
drops free text (questions, answers, case notes, submissions) rather than "anonymising" it with
regex. Cross-record reference uses a sequential surrogate index, not a hash of an internal id.

This is a *de-identified* export for teaching review: it is not a claim that re-identification is
impossible.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Teaching structure kept per training. Free text and identity fields are deliberately absent.
def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_deidentified_records(states, case_lookup: Callable) -> list[dict[str, Any]]:
    """Build whitelist-only records for the given (already authorisation-scoped) session states."""
    records: list[dict[str, Any]] = []
    case_indexes: dict[str, int] = {}
    for index, state in enumerate(states, start=1):
        case = case_lookup(state.case_id, state.case_version)
        case_indexes.setdefault(case.case_id, len(case_indexes) + 1)
        snapshot = state.score_snapshot
        record: dict[str, Any] = {
            # Surrogate index for in-export reference; never a hash of an internal id.
            "export_index": index,
            "case_index": case_indexes[case.case_id],
            "case_version": case.version,
            "sealed": state.sealed,
            "workflow": state.workflow.value if state.workflow is not None else None,
            "clinical_state": state.clinical_state.value if state.clinical_state is not None else None,
            "sim_time_minutes": state.sim_time_minutes,
            "disclosed_fact_count": len(state.disclosed_fact_ids),
            "events": [
                {"order": event.seq, "action": event.action.value, "sim_time": event.sim_time}
                for event in sorted(state.events, key=lambda e: e.seq)
            ],
        }
        if snapshot is not None:
            record["score"] = {
                "total": snapshot.total_score,
                "max": snapshot.max_score,
                "ratio": round(snapshot.total_score / snapshot.max_score, 4) if snapshot.max_score else None,
                "status": snapshot.status,
                "items": [
                    {"stage": item.stage if item.stage in ("病史采集", "检查选择", "鉴别诊断", "诊断", "治疗") else "未分类",
                     "score": item.score, "max": item.max_score, "hit": item.hit}
                    for item in snapshot.items
                ],
            }
        else:
            record["score"] = None
        records.append(record)
    return records


EXPORT_NOTICE = (
    "去标识化教学数据导出：默认排除姓名、用户名、账号/会话内部标识、请求标识、令牌、联系方式、"
    "精确时间、供应商原文与内部诊断，以及问诊/病历/反馈等自由文本；本导出不保证不可重新识别。"
)


class ExportAudit:
    """Transactional audit in accounts.db, included in the existing offline backups.

    Success means the export payload was generated, not that a browser saved its download.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, actor_id: str, scope: dict[str, Any], record_count: int, result: str,
               error: str = "") -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "action": "deidentified_export",
            "actor_id": actor_id,
            "occurred_at": _now_utc(),
            "scope": scope,
            "record_count": record_count,
            "result": result,  # "success" | "rejected" | "failed"
            "error": error,
        }
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            with connection:
                connection.execute("CREATE TABLE IF NOT EXISTS teaching_export_audit "
                                   "(id INTEGER PRIMARY KEY, event_json TEXT NOT NULL)")
                connection.execute("INSERT INTO teaching_export_audit(event_json) VALUES (?)",
                                   (json.dumps(event, ensure_ascii=False, sort_keys=True),))
        return event

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='teaching_export_audit'").fetchone():
                return []
            rows = connection.execute("SELECT event_json FROM teaching_export_audit ORDER BY id DESC LIMIT ?",
                                      (max(1, min(limit, 1000)),)).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

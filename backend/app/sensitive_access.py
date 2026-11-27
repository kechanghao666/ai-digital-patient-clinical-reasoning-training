"""Sensitive-access audit and retention preview (data-governance helpers).

The audit is an append-only table in ``accounts.db`` next to the existing export audit, so the
offline backup/restore carries it without a whitelist change. Writes go through a fixed action
whitelist; readers are the local operations CLI only — no HTTP API exposes audit content, and
every successful audit read is itself recorded here.

The retention preview is a strictly read-only dry run over the three persistent stores. It reports
what a future retention policy *would* consider removable; it never deletes anything, and deletion
execution is not implemented in this release.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ACTIONS = frozenset({
    "retention_preview",     # a retention dry-run preview was computed
    "sensitive_audit_read",  # an audit log (this one, account, qualification, export) was read
    "turn_budget_updated",   # the daily trusted-turn budget was set or cleared by local operations
})
RESULTS = frozenset({"success", "rejected", "failed"})
# Turn statuses a future deletion policy could safely consider; live turns are never in scope.
RETAINABLE_TURN_STATUSES = ("finalized", "expired", "cancelled")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class SensitiveAccessAudit:
    """Append-only sensitive-access audit rows in ``accounts.db``.

    ``append`` is the only writer; ``recent`` opens the database read-only and returns decoded
    events oldest-first. The table is created lazily on the first append — a read against a
    database that has never recorded anything returns an empty list and creates nothing.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, action: str, actor_id: str, scope: dict[str, Any], result: str,
               error: str = "") -> dict[str, Any]:
        if action not in ACTIONS:
            raise ValueError("敏感访问审计操作无效")
        if result not in RESULTS:
            raise ValueError("敏感访问审计结果无效")
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("敏感访问审计操作者不能为空")
        event = {
            "action": action,
            "actor_id": actor_id,
            "occurred_at": _now_utc(),
            "scope": scope,
            "result": result,
            "error": error,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            with connection:
                connection.execute("CREATE TABLE IF NOT EXISTS sensitive_access_audit "
                                   "(id INTEGER PRIMARY KEY, event_json TEXT NOT NULL)")
                connection.execute("INSERT INTO sensitive_access_audit(event_json) VALUES (?)",
                                   (json.dumps(event, ensure_ascii=False, sort_keys=True),))
        return event

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sensitive_access_audit'"
            ).fetchone():
                return []
            rows = connection.execute(
                "SELECT event_json FROM sensitive_access_audit ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 1000)),),
            ).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]


def record_audit_read(audit: SensitiveAccessAudit | None, log_name: str, actor_id: str,
                      limit: int) -> None:
    """Record one audit-log read as a sensitive access.

    ``audit=None`` or a missing database keeps the historic read-only guarantee for a nonexistent
    accounts.db (a read of nothing creates nothing); any real read against an existing store is
    recorded before callers release records; a failed append propagates to fail closed.
    """
    if audit is None:
        return
    if not audit.path.exists():
        return
    audit.append("sensitive_audit_read", actor_id, {"log": log_name, "limit": limit}, "success")


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


def _session_timestamp(state_json: dict[str, Any]) -> datetime | None:
    """Last meaningful activity of one training: the seal time, else the newest event time."""
    if not isinstance(state_json, dict) or not isinstance(state_json.get("events"), list):
        return None
    stamps: list[datetime] = []
    seals: list[datetime] = []
    for event in state_json["events"]:
        if not isinstance(event, dict):
            return None
        raw = event.get("created_at")
        if not isinstance(raw, str):
            return None
        try:
            stamp = datetime.fromisoformat(raw)
            # Unknown activity/timezones must not turn into apparent deletion candidates.
            if stamp.tzinfo is None:
                return None
            stamp = stamp.astimezone(timezone.utc)
        except ValueError:
            return None
        stamps.append(stamp)
        if event.get("action") == "seal":
            seals.append(stamp)
    return max(seals if state_json.get("sealed") and seals else stamps, default=None)


def _require_age(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 3650:
        raise ValueError(f"{name} 须为 1–3650 的整数天数")
    return value


def _turn_cutoff(now: datetime, turn_age_days: int | None) -> datetime | None:
    return None if turn_age_days is None else now - timedelta(days=turn_age_days)


def preview_retention(app_db: Path, accounts_db: Path, integration_db: Path, *,
                      session_age_days: int | None, turn_age_days: int | None,
                      include_expired_logins: bool = True) -> dict[str, Any]:
    """Read-only dry run: what a retention policy *would* remove. Nothing is ever deleted here.

    Session ages use the seal time (or the newest event) from the persisted state; turn and login
    ages use the stored epoch timestamps compared against UTC. Day-based criteria are coarse: a
    session on the boundary day may be either side of the cutoff, so counts are reported as they
    are, never rounded to a claim.
    """
    session_age_days = _require_age(session_age_days, "session_age_days")
    turn_age_days = _require_age(turn_age_days, "turn_age_days")
    if session_age_days is None and turn_age_days is None and not include_expired_logins:
        raise ValueError("预览至少需要一个留存条件（会话天数、回合天数或过期登录）")
    now = datetime.now(timezone.utc)
    session_cutoff = None if session_age_days is None else now - timedelta(days=session_age_days)
    turn_cutoff = _turn_cutoff(now, turn_age_days)

    sessions: dict[str, Any] = {"present": False}
    if Path(app_db).is_file():
        sessions["present"] = True
        total = sealed_old = unsealed_old = unparseable = 0
        oldest: datetime | None = None
        newest: datetime | None = None
        with closing(sqlite3.connect(Path(app_db).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            rows = connection.execute("SELECT state_json FROM sessions").fetchall()
        for (raw,) in rows:
            total += 1
            try:
                state = json.loads(raw)
            except json.JSONDecodeError:
                unparseable += 1
                continue
            stamp = _session_timestamp(state)
            if stamp is None:
                unparseable += 1
                continue
            oldest = stamp if oldest is None or stamp < oldest else oldest
            newest = stamp if newest is None or stamp > newest else newest
            if session_cutoff is not None and stamp < session_cutoff:
                if state.get("sealed"):
                    sealed_old += 1
                else:
                    unsealed_old += 1
        sessions.update({
            "total": total, "sealed_over_age": sealed_old, "unsealed_over_age": unsealed_old,
            "unparseable_timestamp": unparseable, "oldest": _iso_or_none(oldest), "newest": _iso_or_none(newest),
        })

    expired_login_sessions = expired_login_attempts = None
    if include_expired_logins and Path(accounts_db).is_file():
        with closing(sqlite3.connect(Path(accounts_db).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            epoch_now = now.timestamp()
            for table, column, target in (("login_sessions", "expires_at", "expired_login_sessions"),
                                          ("login_attempts", "reset_at", "expired_login_attempts")):
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if not exists:
                    continue
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} <= ?", (epoch_now,)
                ).fetchone()[0]
                if target == "expired_login_sessions":
                    expired_login_sessions = count
                else:
                    expired_login_attempts = count

    turns: dict[str, Any] = {"present": False}
    if Path(integration_db).is_file():
        turns["present"] = True
        with closing(sqlite3.connect(Path(integration_db).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "turns" in tables:
                in_scope = 0
                by_status: dict[str, int] = {}
                oldest = newest = None
                total = 0
                for status, created_at in connection.execute(
                    "SELECT status, created_at FROM turns"
                ).fetchall():
                    total += 1
                    stamp = datetime.fromtimestamp(created_at, tz=timezone.utc) if created_at is not None else None
                    oldest = stamp if (stamp and (oldest is None or stamp < oldest)) else oldest
                    newest = stamp if (stamp and (newest is None or stamp > newest)) else newest
                    by_status[status] = by_status.get(status, 0) + 1
                    if turn_cutoff is not None and stamp is not None and stamp < turn_cutoff \
                            and status in RETAINABLE_TURN_STATUSES:
                        in_scope += 1
                followed_calls = None
                if "turn_calls" in tables and turn_cutoff is not None:
                    followed_calls = connection.execute(
                        "SELECT COUNT(*) FROM turn_calls WHERE turn_id IN "
                        "(SELECT turn_id FROM turns WHERE status IN (?, ?, ?) AND created_at < ?)",
                        (*RETAINABLE_TURN_STATUSES, turn_cutoff.timestamp()),
                    ).fetchone()[0]
                turns.update({
                    "total": total, "over_age": in_scope, "by_status": by_status,
                    "followed_tool_calls_over_age": followed_calls,
                    "oldest": _iso_or_none(oldest), "newest": _iso_or_none(newest),
                })
            else:
                turns["note"] = "集成库没有 turns 表"

    return {
        "schema_version": 1,
        "executed": False,
        "policy": {
            "session_age_days": session_age_days, "turn_age_days": turn_age_days,
            "include_expired_logins": include_expired_logins, "clock": "UTC",
        },
        "sessions": sessions,
        "expired_login_sessions": expired_login_sessions,
        "expired_login_attempts": expired_login_attempts,
        "turns": turns,
        "notice": "只读预览：本命令不会删除任何业务数据；删除执行尚未实现，须另行授权并单独验证。",
    }


def parse_iso_date(value: str, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须为 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须为 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} 必须为 YYYY-MM-DD")
    return parsed

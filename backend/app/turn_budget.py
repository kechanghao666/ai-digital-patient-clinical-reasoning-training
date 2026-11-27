"""Daily trusted-turn budget: an operations count alert, never a cost figure.

The budget limits nothing by itself — it surfaces how many trusted turns were issued today against
a configured daily count, so operators get an early warning before vendor spend grows. Token usage
and cost stay "unknown" until a real bill exists. Configuration lives in a single-row table in
``accounts.db``; every set/clear writes its sensitive-access audit entry in the same transaction,
so a config change and its audit record succeed or fail together.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sensitive_access import _now_utc

SCHEMA_VERSION = 1


class TurnBudgetError(ValueError):
    """The stored budget configuration is malformed and must not be silently ignored."""


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _ensure_table(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS turn_budget_config (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        schema_version INTEGER NOT NULL,
        daily_turn_limit INTEGER NOT NULL,
        updated_by TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")


def read_budget(path: str | Path) -> dict[str, Any] | None:
    """Return the configured budget or ``None`` when none is stored.

    A malformed row raises :class:`TurnBudgetError` — callers surface it as "budget unavailable",
    never as "no budget exceeded".
    """
    database = Path(path)
    if not database.is_file():
        return None
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='turn_budget_config'"
        ).fetchone()
        if not exists:
            return None
        row = connection.execute("SELECT * FROM turn_budget_config WHERE id=1").fetchone()
    if row is None:
        return None
    if row["schema_version"] != SCHEMA_VERSION:
        raise TurnBudgetError("预算配置版本不受支持")
    limit = row["daily_turn_limit"]
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 1_000_000:
        raise TurnBudgetError("预算配置的每日回合上限无效")
    return {
        "daily_turn_limit": limit,
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


def write_budget(path: str | Path, daily_turn_limit: int, *, actor_id: str = "local-ops") -> dict[str, Any]:
    """Store (or replace) the daily budget; the audit entry is written in the same transaction."""
    if isinstance(daily_turn_limit, bool) or not isinstance(daily_turn_limit, int) \
            or daily_turn_limit < 1 or daily_turn_limit > 1_000_000:
        raise ValueError("每日回合上限须为 1–1000000 的整数")
    if actor_id != "local-ops":
        raise ValueError("预算只能由本机运维设置")
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    updated_at = _now_utc()
    event = {
        "action": "turn_budget_updated",
        "actor_id": actor_id,
        "occurred_at": updated_at,
        "scope": {"daily_turn_limit": daily_turn_limit},
        "result": "success",
        "error": "",
    }
    with closing(_connection(database)) as connection:
        with connection:
            _ensure_table(connection)
            connection.execute("DELETE FROM turn_budget_config WHERE id=1")
            connection.execute(
                "INSERT INTO turn_budget_config (id, schema_version, daily_turn_limit, updated_by, updated_at) "
                "VALUES (1, ?, ?, ?, ?)",
                (SCHEMA_VERSION, daily_turn_limit, actor_id, updated_at),
            )
            connection.execute("CREATE TABLE IF NOT EXISTS sensitive_access_audit "
                               "(id INTEGER PRIMARY KEY, event_json TEXT NOT NULL)")
            connection.execute("INSERT INTO sensitive_access_audit(event_json) VALUES (?)",
                               (json.dumps(event, ensure_ascii=False, sort_keys=True),))
    return {"daily_turn_limit": daily_turn_limit, "updated_by": actor_id, "updated_at": updated_at}


def clear_budget(path: str | Path, *, actor_id: str = "local-ops") -> bool:
    """Remove the configured budget; returns False when none was configured (no audit row then)."""
    if actor_id != "local-ops":
        raise ValueError("预算只能由本机运维清除")
    database = Path(path)
    if not database.is_file():
        return False
    event = {
        "action": "turn_budget_updated",
        "actor_id": actor_id,
        "occurred_at": _now_utc(),
        "scope": {"cleared": True},
        "result": "success",
        "error": "",
    }
    with closing(_connection(database)) as connection:
        with connection:
            _ensure_table(connection)
            deleted = connection.execute("DELETE FROM turn_budget_config WHERE id=1").rowcount
            if deleted:
                connection.execute("CREATE TABLE IF NOT EXISTS sensitive_access_audit "
                                   "(id INTEGER PRIMARY KEY, event_json TEXT NOT NULL)")
                connection.execute("INSERT INTO sensitive_access_audit(event_json) VALUES (?)",
                                   (json.dumps(event, ensure_ascii=False, sort_keys=True),))
    return bool(deleted)


def budget_status(config: dict[str, Any] | None, turns_today: int | None) -> dict[str, Any] | None:
    """Merge a configured budget with the observed turn count into an alert payload.

    ``exceeded`` fires when today's count has *reached* the limit — the point of a budget is to
    warn before overspend, not after. ``turns_today=None`` (unknown coverage) is reported as such
    instead of pretending zero.
    """
    if not config:
        return None
    limit = config["daily_turn_limit"]
    return {
        "daily_turn_limit": limit,
        "turns_today": turns_today,
        "exceeded": None if turns_today is None else turns_today >= limit,
        "notice": "预算按可信回合次数告警，不是费用；token 用量与费用仍为 unknown。",
    }

"""Sensitive-access audit and retention preview (M10 engineering checks).

Synthetic temporary stores only: the preview is a read-only dry run, and these tests also prove it
deletes nothing. Deletion execution does not exist in the product yet; nothing here runs against a
real business database.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.backup_restore import create_backup, restore_backup
from app.models import ActionKind, Event, SessionState
from app.sensitive_access import (
    ACTIONS,
    SensitiveAccessAudit,
    preview_retention,
    record_audit_read,
)
from app.storage import SessionRepository

ROOT = Path(__file__).parents[1]


def _state(sealed_at: datetime | None, *, sealed: bool | None = None) -> SessionState:
    events: list[Event] = []
    if sealed_at is not None:
        events.append(Event(request_id="seal-r", seq=1, actor="student", action=ActionKind.SEAL,
                            payload={}, result={"sealed": True}, sim_time=0,
                            state_before={}, state_after={}, created_at=sealed_at))
    state = SessionState(owner_id="student-1", case_id="CASE-001", case_version=1, events=events)
    if sealed is not None:
        state.sealed = sealed
        state.seal_seq = len(events)
    return state


def _turn_row(turn_id: str, *, status: str, created_at: float, case_id: str = "CASE-001") -> tuple:
    return (turn_id, "hash", "patient", "student-1", f"ses-{turn_id}", case_id, 1, f"r-{turn_id}", 0,
            "问", "[]", status, created_at + 60, None, None, created_at, None, "nexent")


@pytest.fixture()
def stores(tmp_path: Path):
    app_db = tmp_path / "app.db"
    accounts_db = tmp_path / "accounts.db"
    integration_db = tmp_path / "integration.db"
    repository = SessionRepository(app_db)
    repository.create(_state(datetime.now(timezone.utc) - timedelta(days=40), sealed=True))
    repository.create(_state(datetime.now(timezone.utc) - timedelta(days=10), sealed=True))
    repository.create(_state(datetime.now(timezone.utc) - timedelta(days=40), sealed=False))
    repository.create(_state(None))
    with closing(sqlite3.connect(accounts_db)) as connection:
        connection.executescript("""
            CREATE TABLE login_sessions (token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL, expires_at REAL NOT NULL);
            CREATE TABLE login_attempts (bucket TEXT PRIMARY KEY, attempts INTEGER NOT NULL, reset_at REAL NOT NULL);
        """)
        connection.execute("INSERT INTO login_sessions VALUES ('old', 'a', ?)", (time.time() - 3600,))
        connection.execute("INSERT INTO login_sessions VALUES ('fresh', 'a', ?)", (time.time() + 3600,))
        connection.execute("INSERT INTO login_attempts VALUES ('b', 1, ?)", (time.time() - 3600,))
        connection.commit()
    with closing(sqlite3.connect(integration_db)) as connection:
        connection.executescript("""
            CREATE TABLE turns (turn_id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, kind TEXT NOT NULL,
                owner_id TEXT NOT NULL, session_id TEXT NOT NULL, case_id TEXT NOT NULL, case_version INTEGER NOT NULL,
                request_id TEXT NOT NULL, state_version INTEGER NOT NULL, question TEXT NOT NULL,
                candidates_json TEXT NOT NULL, status TEXT NOT NULL, expires_at REAL NOT NULL,
                submission_json TEXT, cancel_reason TEXT, created_at REAL NOT NULL, finalized_at REAL, mode TEXT NOT NULL DEFAULT 'unknown');
            CREATE TABLE turn_calls (call_id INTEGER PRIMARY KEY AUTOINCREMENT, turn_id TEXT NOT NULL,
                tool TEXT NOT NULL, ok INTEGER NOT NULL, detail TEXT NOT NULL, at REAL NOT NULL);
        """)
        old = time.time() - 40 * 86400
        fresh = time.time() - 86400
        connection.execute("INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           _turn_row("t-old", status="finalized", created_at=old))
        connection.execute("INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           _turn_row("t-fresh", status="finalized", created_at=fresh))
        connection.execute("INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           _turn_row("t-live", status="open", created_at=old))
        connection.execute("INSERT INTO turn_calls(turn_id, tool, ok, detail, at) VALUES ('t-old','tool',1,'',?)", (old,))
        connection.commit()
    return {"app_db": app_db, "accounts_db": accounts_db, "integration_db": integration_db}


# --- sensitive access audit ---------------------------------------------------

def test_audit_append_and_recent_are_ordered_and_validated(tmp_path):
    audit = SensitiveAccessAudit(tmp_path / "accounts.db")
    first = audit.append("retention_preview", "local-ops", {"day": 30}, "success")
    second = audit.append("sensitive_audit_read", "local-ops", {"log": "sensitive_access_audit"}, "success")
    assert first["result"] == "success" and second["action"] == "sensitive_audit_read"
    rows = audit.recent()
    assert [row["action"] for row in rows] == ["retention_preview", "sensitive_audit_read"]
    assert [row["occurred_at"] for row in rows] == sorted(row["occurred_at"] for row in rows)
    with pytest.raises(ValueError):
        audit.append("not_an_action", "local-ops", {}, "success")
    with pytest.raises(ValueError):
        audit.append("retention_preview", "local-ops", {}, "happened")
    with pytest.raises(ValueError):
        audit.append("retention_preview", "  ", {}, "success")
    assert set(ACTIONS) == {"retention_preview", "sensitive_audit_read", "turn_budget_updated"}


def test_audit_read_of_missing_database_creates_nothing(tmp_path):
    audit = SensitiveAccessAudit(tmp_path / "missing.db")
    assert audit.recent() == []
    record_audit_read(audit, "sensitive_access_audit", "local-ops", 100)
    assert not (tmp_path / "missing.db").exists()


# --- retention preview --------------------------------------------------------

def test_preview_counts_scope_and_deletes_nothing(stores):
    before = {name: Path(path).read_bytes() for name, path in stores.items()}
    result = preview_retention(stores["app_db"], stores["accounts_db"], stores["integration_db"],
                               session_age_days=30, turn_age_days=30)
    assert result["executed"] is False
    sessions = result["sessions"]
    assert sessions["total"] == 4
    assert sessions["sealed_over_age"] == 1   # 40-day-old sealed training
    assert sessions["unsealed_over_age"] == 1  # 40-day-old open training is reported, not hidden
    assert result["expired_login_sessions"] == 1
    assert result["expired_login_attempts"] == 1
    turns = result["turns"]
    assert turns["total"] == 3 and turns["over_age"] == 1  # the live old turn is never in scope
    assert turns["by_status"] == {"finalized": 2, "open": 1}
    assert turns["followed_tool_calls_over_age"] == 1
    assert "不会删除任何业务数据" in result["notice"]
    assert {name: Path(path).read_bytes() for name, path in stores.items()} == before


def test_preview_without_any_criterion_is_rejected(stores):
    with pytest.raises(ValueError):
        preview_retention(stores["app_db"], stores["accounts_db"], stores["integration_db"],
                          session_age_days=None, turn_age_days=None, include_expired_logins=False)
    with pytest.raises(ValueError):
        preview_retention(stores["app_db"], stores["accounts_db"], stores["integration_db"],
                          session_age_days=0, turn_age_days=None)


def test_preview_tolerates_missing_stores(tmp_path):
    result = preview_retention(tmp_path / "no-app.db", tmp_path / "no-accounts.db", tmp_path / "no-int.db",
                               session_age_days=30, turn_age_days=None)
    assert result["sessions"]["present"] is False
    assert result["expired_login_sessions"] is None
    assert result["turns"]["present"] is False


# --- audit reads leave a trace ------------------------------------------------

def test_cli_audit_reads_record_sensitive_access(tmp_path, monkeypatch):
    accounts_db = tmp_path / "accounts.db"
    from app.accounts import AccountStore

    store = AccountStore(accounts_db)
    store.create("ops-viewer", "Op-Pass-2026-xyz", "teacher", "运维视图")
    audit = SensitiveAccessAudit(accounts_db)

    sys.path.insert(0, str(ROOT / "scripts"))
    import manage_accounts

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["manage_accounts.py", "audit-log"])
    manage_accounts.main()
    monkeypatch.setattr(sys, "argv", ["manage_accounts.py", "sensitive-audit"])
    manage_accounts.main()

    rows = audit.recent()
    reads = [row for row in rows if row["action"] == "sensitive_audit_read"]
    assert [row["scope"]["log"] for row in reads] == ["account_management_audit", "sensitive_access_audit"]
    assert all(row["actor_id"] == "local-ops" for row in reads)


def test_cli_audit_read_on_missing_database_creates_nothing(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    import manage_accounts

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["manage_accounts.py", "audit-log"])
    manage_accounts.main()
    assert json.loads(capsys.readouterr().out) == []
    assert not (tmp_path / "accounts.db").exists()


# --- backup / restore consistency ---------------------------------------------

def test_backup_restore_carries_sensitive_access_audit(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    app_db = data_dir / "app.db"
    accounts_db = data_dir / "accounts.db"
    integration_db = data_dir / "integration.db"
    SessionRepository(app_db).create(_state(datetime.now(timezone.utc), sealed=True))
    audit = SensitiveAccessAudit(accounts_db)
    audit.append("retention_preview", "local-ops", {"day": 30}, "success")

    from app.integration.turns import TurnStore

    TurnStore(integration_db)  # create the required integration store with its schema

    # The backup whitelist must accept the stores exactly as the application lays them out.
    backup_dir = tmp_path / "backup"
    create_backup(data_dir, integration_db, backup_dir, confirm_services_stopped=True)
    restore_dir = tmp_path / "restored"
    restore_backup(backup_dir, restore_dir, confirm_services_stopped=True)

    restored = SensitiveAccessAudit(restore_dir / "accounts.db").recent()
    assert [row["action"] for row in restored] == ["retention_preview"]

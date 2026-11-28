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


def test_preview_skips_expired_logins(stores):
    result = preview_retention(**stores, session_age_days=30, turn_age_days=None,
                               include_expired_logins=False)
    assert result["expired_login_sessions"] is None
    assert result["expired_login_attempts"] is None


def test_preview_handles_invalid_and_naive_session_timestamps(stores):
    states = [[], {"events": [None]}, {"events": "invalid"},
              {"sealed": True, "events": [{"action": "seal", "created_at": "invalid"},
                                          {"created_at": "2020-01-01T00:00:00Z"}]},
              {"events": [{"created_at": "2020-01-01T00:00:00"},
                          {"created_at": "2020-01-02T00:00:00Z"}]}]
    for state in states:
        with closing(sqlite3.connect(stores["app_db"])) as connection:
            connection.execute("UPDATE sessions SET state_json=?", (json.dumps(state),))
            connection.commit()
        result = preview_retention(**stores, session_age_days=30, turn_age_days=None)
        # Ambiguous or malformed activity must never suggest deletion.
        assert result["sessions"]["unparseable_timestamp"] == 4
        assert result["sessions"]["sealed_over_age"] == 0
        assert result["sessions"]["unsealed_over_age"] == 0


def test_preview_turn_boundary_matches_followed_calls(stores, monkeypatch):
    import app.sensitive_access as module

    fixed = datetime(2026, 11, 28, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    monkeypatch.setattr(module, "datetime", Clock)
    with closing(sqlite3.connect(stores["integration_db"])) as connection:
        connection.execute("UPDATE turns SET created_at=? WHERE turn_id='t-old'",
                           ((fixed - timedelta(days=30)).timestamp(),))
        connection.commit()
    result = preview_retention(**stores, session_age_days=None, turn_age_days=30)
    assert result["turns"]["over_age"] == 0
    assert result["turns"]["followed_tool_calls_over_age"] == 0


@pytest.mark.parametrize("action", ["audit-log", "qualification-audit", "sensitive-audit"])
def test_cli_audit_read_fails_closed(tmp_path, monkeypatch, capsys, action):
    from app.accounts import AccountStore
    sys.path.insert(0, str(ROOT / "scripts"))
    import manage_accounts

    AccountStore(tmp_path / "accounts.db").create("viewer", "Op-Pass-2026-xyz", "teacher", "合成教师")
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic audit failure")
    monkeypatch.setattr(SensitiveAccessAudit, "append", fail)
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["manage_accounts.py", action])
    with pytest.raises(SystemExit) as exc:
        manage_accounts.main()
    assert exc.value.code != 0
    assert capsys.readouterr().out == ""


def test_preview_cli_writes_only_audit_and_withholds_result_on_failure(stores, monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    import retention_preview

    argv = ["retention_preview.py", "--session-age-days", "30", "--skip-expired-logins"]
    for key, path in stores.items():
        argv.extend(["--" + key.replace("_", "-"), str(path)])
    monkeypatch.setattr(sys, "argv", argv)
    before = {name: path.read_bytes() for name, path in stores.items()}
    retention_preview.main()
    assert json.loads(capsys.readouterr().out)["expired_login_sessions"] is None
    assert stores["app_db"].read_bytes() == before["app_db"]
    assert stores["integration_db"].read_bytes() == before["integration_db"]
    assert SensitiveAccessAudit(stores["accounts_db"]).recent()[-1]["action"] == "retention_preview"
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic write failure")
    monkeypatch.setattr(SensitiveAccessAudit, "append", fail)
    with pytest.raises(SystemExit):
        retention_preview.main()
    assert not capsys.readouterr().out
    stores["app_db"].write_bytes(b"corrupted")
    with pytest.raises(SystemExit):
        retention_preview.main()
    captured = capsys.readouterr()
    assert not captured.out and "Traceback" not in captured.err


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

def test_backup_restore_carries_sensitive_access_audit_and_budget(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    app_db = data_dir / "app.db"
    accounts_db = data_dir / "accounts.db"
    integration_db = data_dir / "integration.db"
    SessionRepository(app_db).create(_state(datetime.now(timezone.utc), sealed=True))
    audit = SensitiveAccessAudit(accounts_db)
    audit.append("retention_preview", "local-ops", {"day": 30}, "success")

    from app.integration.turns import TurnStore
    from app.turn_budget import read_budget, write_budget

    TurnStore(integration_db)  # create the required integration store with its schema
    write_budget(accounts_db, 42)

    # The backup whitelist must accept the stores exactly as the application lays them out.
    backup_dir = tmp_path / "backup"
    create_backup(data_dir, integration_db, backup_dir, confirm_services_stopped=True)
    restore_dir = tmp_path / "restored"
    restore_backup(backup_dir, restore_dir, confirm_services_stopped=True)

    restored = SensitiveAccessAudit(restore_dir / "accounts.db").recent()
    assert [row["action"] for row in restored] == ["retention_preview", "turn_budget_updated"]
    assert read_budget(restore_dir / "accounts.db")["daily_turn_limit"] == 42

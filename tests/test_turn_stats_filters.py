"""Observability filters, P95 and the daily turn budget (M12 engineering checks).

Synthetic temporary stores only. Semantics stay conservative: the P95 is the nearest-rank
percentile over recorded completion intervals; date filters use UTC day boundaries; the budget is
a count alert, never a cost figure — token usage and cost stay "unknown".
"""
from __future__ import annotations

import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.integration.turns import TurnStore, empty_stats, read_stats
from app.turn_budget import TurnBudgetError, budget_status, clear_budget, read_budget, write_budget

ROOT = Path(__file__).parents[1]


def _issue_finalized(store: TurnStore, rid: str, *, case_id: str = "CASE-001", mode: str = "nexent",
                     created_at: float | None = None, db_path: Path | None = None,
                     owner_id: str = "student-1") -> str:
    turn, _ = store.issue(kind="patient", owner_id=owner_id, session_id=f"ses-{rid}",
                          case_id=case_id, case_version=1, request_id=rid, state_version=0,
                          question="问", candidate_fact_ids=["f"], mode=mode)
    assert store.submit(turn.turn_id, {"fact_ids": ["f"]})
    assert store.finalize(turn.turn_id)
    if created_at is not None:
        # submit/finalize validate against the real clock, so historical rows are backdated here.
        with closing(sqlite3.connect(db_path)) as connection:
            connection.execute("UPDATE turns SET created_at=? WHERE turn_id=?", (created_at, turn.turn_id))
            connection.commit()
    return turn.turn_id


def _set_interval(db_path: Path, turn_id: str, created_at: float, finalized_at: float) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("UPDATE turns SET created_at=?, finalized_at=? WHERE turn_id=?",
                           (created_at, finalized_at, turn_id))
        connection.commit()


def _p95_of(db_path: Path) -> dict:
    return read_stats(db_path)["stats"]["turn_completion"]


def test_p95_is_nearest_rank_over_recorded_intervals(tmp_path):
    store = TurnStore(tmp_path / "turns.db")
    ids = [_issue_finalized(store, f"r{i}") for i in range(20)]
    for index, turn_id in enumerate(ids):
        _set_interval(tmp_path / "turns.db", turn_id, 0.0, (index + 1) / 100)  # 10ms..200ms
    completion = _p95_of(tmp_path / "turns.db")
    assert completion["measured_count"] == 20
    assert completion["p95_method"] == "nearest-rank"
    assert completion["p95_ms"] == 190  # ceil(0.95*20)=19 -> 190ms, not interpolated
    assert completion["median_ms"] == 105
    assert completion["max_ms"] == 200


def test_p95_empty_single_and_negative(tmp_path):
    store = TurnStore(tmp_path / "turns.db")
    assert _p95_of(tmp_path / "turns.db")["p95_ms"] is None
    turn = _issue_finalized(store, "r1")
    _set_interval(tmp_path / "turns.db", turn, 0.0, 2.5)
    assert _p95_of(tmp_path / "turns.db")["p95_ms"] == 2500
    _set_interval(tmp_path / "turns.db", turn, 5.0, 4.0)  # negative -> excluded, never clamped
    completion = _p95_of(tmp_path / "turns.db")
    assert completion["measured_count"] == 0 and completion["negative_count"] == 1
    assert completion["p95_ms"] is None


def test_by_case_counts_internal_case_ids(tmp_path):
    store = TurnStore(tmp_path / "turns.db")
    _issue_finalized(store, "r1", case_id="CASE-001")
    _issue_finalized(store, "r2", case_id="CASE-001")
    _issue_finalized(store, "r3", case_id="CASE-002")
    stats = store.stats()
    assert stats["by_case"] == {"CASE-001": 2, "CASE-002": 1}
    assert stats["token_usage"] == "unknown" and stats["cost"] == "unknown"


def test_case_and_date_filters_narrow_turns_and_calls(tmp_path):
    db_path = tmp_path / "turns.db"
    store = TurnStore(db_path)
    old_case1 = _issue_finalized(store, "r1", case_id="CASE-001", db_path=db_path,
                                 created_at=datetime(2026, 1, 10, 12, tzinfo=timezone.utc).timestamp())
    old_case2 = _issue_finalized(store, "r2", case_id="CASE-002", db_path=db_path,
                                 created_at=datetime(2026, 1, 10, 12, tzinfo=timezone.utc).timestamp())
    new_case1 = _issue_finalized(store, "r3", case_id="CASE-001")
    store.record_call(old_case1, "tool-a", ok=True)
    store.record_call(old_case2, "tool-b", ok=False)
    store.record_call(new_case1, "tool-c", ok=True)

    by_case = store.stats(case_ids=["CASE-001"])
    assert by_case["turns_total"] == 2 and by_case["tool_calls_total"] == 2

    by_day = store.stats(date_from="2026-01-10", date_to="2026-01-10")
    assert by_day["turns_total"] == 2 and by_day["tool_calls_failed"] == 1

    combined = store.stats(case_ids=["CASE-002"], date_from="2026-01-01", date_to="2026-01-31")
    assert combined["turns_total"] == 1 and combined["tool_calls_total"] == 1

    assert store.stats(date_from="2026-02-01", date_to="2026-02-02")["turns_total"] == 0
    assert read_stats(db_path, case_ids=["CASE-001"])["stats"]["turns_total"] == 2


def test_filter_validation_errors(tmp_path):
    store = TurnStore(tmp_path / "turns.db")
    with pytest.raises(ValueError):
        store.stats(date_from="2026-1-1")
    with pytest.raises(ValueError):
        store.stats(date_from="2026-02-02", date_to="2026-02-01")
    with pytest.raises(ValueError):
        store.stats(case_ids=[])
    with pytest.raises(ValueError):
        store.stats(case_ids=["  "])
    with pytest.raises(ValueError):
        read_stats(tmp_path / "turns.db", date_from="not-a-date")


def test_turns_today_counts_only_the_local_day(tmp_path):
    store = TurnStore(tmp_path / "turns.db")
    _issue_finalized(store, "old", db_path=tmp_path / "turns.db",
                     created_at=datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
    _issue_finalized(store, "today")
    stats = store.stats()
    assert stats["turns_today"] == 1
    assert stats["turns_today_boundary"] == "server-local calendar day"
    assert empty_stats()["turns_today"] == 0


# --- daily budget ---------------------------------------------------------------

def test_budget_write_read_clear_and_audit(tmp_path):
    accounts_db = tmp_path / "accounts.db"
    assert read_budget(accounts_db) is None
    config = write_budget(accounts_db, 25)
    assert config["daily_turn_limit"] == 25 and config["updated_by"] == "local-ops"
    assert read_budget(accounts_db)["daily_turn_limit"] == 25

    from app.sensitive_access import SensitiveAccessAudit

    rows = SensitiveAccessAudit(accounts_db).recent()
    assert [row["action"] for row in rows] == ["turn_budget_updated"]
    assert rows[0]["scope"] == {"daily_turn_limit": 25}

    assert clear_budget(accounts_db) is True
    assert read_budget(accounts_db) is None
    actions = [row["action"] for row in SensitiveAccessAudit(accounts_db).recent()]
    assert actions == ["turn_budget_updated", "turn_budget_updated"]
    assert clear_budget(accounts_db) is False  # nothing to clear, no new audit row

    with pytest.raises(ValueError):
        write_budget(accounts_db, 0)
    with pytest.raises(ValueError):
        write_budget(accounts_db, 10**7)


def test_budget_read_rejects_malformed_config(tmp_path):
    accounts_db = tmp_path / "accounts.db"
    write_budget(accounts_db, 10)
    with closing(sqlite3.connect(accounts_db)) as connection:
        connection.execute("UPDATE turn_budget_config SET daily_turn_limit=0 WHERE id=1")
        connection.commit()
    with pytest.raises(TurnBudgetError):
        read_budget(accounts_db)


def test_budget_status_reaches_alert_at_the_limit_not_after():
    assert budget_status({"daily_turn_limit": 5}, 4)["exceeded"] is False
    assert budget_status({"daily_turn_limit": 5}, 5)["exceeded"] is True
    assert budget_status({"daily_turn_limit": 5}, None)["exceeded"] is None
    assert budget_status(None, 100) is None
    assert budget_status({"daily_turn_limit": 5}, 5)["notice"].endswith("unknown。")


# --- teacher endpoint -----------------------------------------------------------

@pytest.fixture()
def endpoint_env(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("APP_AUTH_MODE", raising=False)
    from fastapi.testclient import TestClient

    from app import main
    from app.accounts import AccountStore
    from app.integration.config import IntegrationSettings
    from app.integration.runtime import IntegrationRuntime
    from app.roster import RosterStore

    accounts = AccountStore(tmp_path / "accounts.db")
    monkeypatch.setattr(main, "ACCOUNTS", accounts)
    settings = IntegrationSettings(
        mode="simulator", nexent_base_url="", nexent_api_key="", patient_agent_name="",
        feedback_agent_name="", connect_timeout_s=1.0, first_event_timeout_s=1.0,
        total_timeout_s=5.0, turn_ttl_s=60.0, mcp_service_token="",
        integration_db_path=tmp_path / "integration.db", knowledge_dir=ROOT / "knowledge",
    )
    runtime = IntegrationRuntime(settings)
    monkeypatch.setattr(main, "RUNTIME", runtime)
    roster = RosterStore(accounts)
    teacher_id = accounts.create("teachera", "Ta-Pass-2026", "teacher", "老师甲")
    class_id = roster.create_class(teacher_id, "甲班")["id"]
    roster.add_students(class_id, teacher_id, [{"username": "s001", "display_name": "学生一"}])
    student_id = accounts.find("s001")["id"]

    old_day = datetime(2026, 1, 10, 12, tzinfo=timezone.utc).timestamp()
    old_turn = _issue_finalized(runtime.turns, "r1", case_id="CASE-001", db_path=tmp_path / "integration.db",
                                created_at=old_day, owner_id=student_id)
    runtime.turns.record_call(old_turn, "tool-a", ok=False)
    fresh_turn = _issue_finalized(runtime.turns, "r2", case_id="CASE-002", owner_id=student_id)
    runtime.turns.record_call(fresh_turn, "tool-b", ok=True)

    client = TestClient(main.app)
    # Demo X-Owner-Id identities are not roster account ids; log in for a real teacher identity.
    login = client.post("/api/auth/login", json={"username": "teachera", "password": "Ta-Pass-2026"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": "Bearer " + login.json()["token"]}
    return {"client": client, "headers": headers, "accounts_path": accounts.path, "runtime": runtime}


def test_endpoint_filters_and_budget(endpoint_env):
    client, headers = endpoint_env["client"], endpoint_env["headers"]

    base = client.get("/api/teacher/observability", headers=headers).json()
    assert base["ok"] is True and base["stats"]["turns_total"] == 2
    assert base["stats"]["by_case"] == {"CASE-001": 1, "CASE-002": 1}
    assert base["budget"] is None  # no budget configured -> explicit null, not a fabricated alert

    scoped = client.get("/api/teacher/observability", headers=headers,
                        params={"case_id": "CASE-001"}).json()
    assert scoped["stats"]["turns_total"] == 1 and scoped["stats"]["tool_calls_failed"] == 1

    day = client.get("/api/teacher/observability", headers=headers,
                     params={"date_from": "2026-01-10", "date_to": "2026-01-10"}).json()
    assert day["stats"]["turns_total"] == 1 and day["stats"]["by_case"] == {"CASE-001": 1}

    assert client.get("/api/teacher/observability", headers=headers,
                      params={"date_from": "2026-13-01"}).status_code == 400
    assert client.get("/api/teacher/observability", headers=headers,
                      params={"date_from": "2026-02-02", "date_to": "2026-02-01"}).status_code == 400
    assert client.get("/api/teacher/observability", headers=headers,
                      params={"case_id": ""}).json()["stats"]["turns_total"] == 2


def test_endpoint_budget_alert(endpoint_env):
    client, headers, accounts_path = endpoint_env["client"], endpoint_env["headers"], endpoint_env["accounts_path"]
    # One fixture turn was issued today (the other is backdated); a limit of 1 is already reached.
    write_budget(accounts_path, 1)
    body = client.get("/api/teacher/observability", headers=headers).json()
    assert body["budget"]["daily_turn_limit"] == 1
    assert body["budget"]["exceeded"] is True
    assert body["budget"]["turns_today"] == 1
    assert body["budget"]["notice"].endswith("unknown。")
    clear_budget(accounts_path)

    write_budget(accounts_path, 2)
    body = client.get("/api/teacher/observability", headers=headers).json()
    assert body["budget"]["exceeded"] is False


def test_cli_turn_budget_round_trip(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    import json

    import turn_budget as turn_budget_cli  # scripts/turn_budget.py, distinct from app.turn_budget

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["turn_budget.py", "set", "--limit", "7"])
    turn_budget_cli.main()
    assert "已设置每日可信回合预算 7" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["turn_budget.py", "show"])
    turn_budget_cli.main()
    shown = json.loads(capsys.readouterr().out)
    assert shown["configured"] is True and shown["budget"]["daily_turn_limit"] == 7
    monkeypatch.setattr(sys, "argv", ["turn_budget.py", "clear"])
    turn_budget_cli.main()
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["turn_budget.py", "show"])
    turn_budget_cli.main()
    assert json.loads(capsys.readouterr().out)["configured"] is False

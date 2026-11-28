"""Regressions for defects found in the 2026-11-13 project review."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.accounts import AccountStore
from app.case_store import CaseStore
from app.models import ActionKind, SessionState
from app.state_machine import StateMachine
from app.storage import SessionRepository
from test_accounts import PASSWORD, accounts  # noqa: F401  (fixture)

ROOT = Path(__file__).parents[1]


def test_oversized_password_is_rejected_without_echo(accounts):  # noqa: F811
    api, _ = accounts
    secret = "S3cret-" + "x" * 130
    response = api.client.post("/api/auth/login", json={"username": "alice", "password": secret})
    assert response.status_code == 422
    assert secret not in response.text and "S3cret" not in response.text


def test_failed_guesses_through_loopback_proxy_do_not_lock_other_users(tmp_path):
    store = AccountStore(tmp_path / "accounts.db")
    store.create("alice", PASSWORD, "student", "Alice")
    for index in range(130):
        with pytest.raises(HTTPException) as exc:
            store.login(f"ghost{index}", "wrong-password-xx", "127.0.0.1")
        assert exc.value.status_code == 401
    assert store.login("alice", PASSWORD, "127.0.0.1")["user"]["username"] == "alice"


def test_remote_source_is_still_rate_limited(tmp_path):
    store = AccountStore(tmp_path / "accounts.db")
    store.create("alice", PASSWORD, "student", "Alice")
    for index in range(120):
        with pytest.raises(HTTPException):
            store.login(f"ghost{index}", "wrong-password-xx", "203.0.113.9")
    with pytest.raises(HTTPException) as exc:
        store.login("alice", PASSWORD, "203.0.113.9")
    assert exc.value.status_code == 429


def _score(tmp_path, support, oppose):
    cases = CaseStore(ROOT / "cases")
    repository = SessionRepository(tmp_path / "s.db")
    state = SessionState(owner_id="student-a", case_id="STEMI-001", case_version=3)
    repository.create(state)
    machine = StateMachine(cases, repository)
    events = []
    for index, fact_id in enumerate(["f_pain_onset", "f_pain_character", "f_pain_radiation"]):
        event = machine._append(state, f"q{index}", ActionKind.ASK,
                                {"question": "x", "matched_fact_ids": [fact_id]},
                                {"answer": "", "fact_ids": [fact_id]}, fact_ids=[fact_id])
        events.append(event.event_id)
        repository.save(state, expected_version=state.state_version - 1)
    machine.apply(state, "d", ActionKind.SUBMIT_DIFFERENTIAL, {"candidates": [{
        "candidate_id": "aortic_dissection", "supporting_evidence": support,
        "opposing_evidence": oppose, "evidence_event_ids": events}]})
    machine.apply(state, "s", ActionKind.SEAL, {})
    return next(item for item in state.score_snapshot.items if item.item_id == "aortic_dissection")


def test_one_box_listing_several_cited_facts_is_scored_like_separate_entries(tmp_path):
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir(), (tmp_path / "c").mkdir()
    single = _score(tmp_path / "a", ["休息时突然发作"], ["胸痛像压榨一样"])
    joined = _score(tmp_path / "b", ["休息时突然发作"], ["胸痛像压榨一样、疼痛向左肩和左上肢放射"])
    assert joined.score == single.score
    # A list containing an unmatched part is kept whole and still goes to review.
    partial = _score(tmp_path / "c", ["休息时突然发作"], ["胸痛像压榨一样、我猜不是夹层"])
    assert partial.score < single.score

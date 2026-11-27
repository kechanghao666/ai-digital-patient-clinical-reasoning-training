"""Synthetic qualification lifecycle and publication authorization, no medical verification."""
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event

import pytest

from app.accounts import AccountStore, ReviewerQualificationError
from reviewer_helpers import bind_synthetic_demo_reviewer
from test_cases_config import Api, ROOT
from test_teacher_cases import new_case, put, step, PUBLISH, TEACHER


@pytest.fixture
def reviewer(tmp_path, monkeypatch):
    store = bind_synthetic_demo_reviewer(tmp_path, monkeypatch)
    qid = store.reviewer_qualifications_for_account("teacher-wang")[0]["qualification_id"]
    return store, qid


def test_pending_activation_scope_dates_and_revocation(reviewer):
    store, qid = reviewer
    store.set_reviewer_qualification_status(qid, "revoked")
    pending = store.create_reviewer_qualification("teacher-wang", "04", "04.01", "2020-01-01", "2099-12-31")
    def check(department="04", sub="04.01", day=None):
        return store.verified_reviewer_for_publication("teacher-wang", department, sub, day or date.today().isoformat())
    with pytest.raises(ReviewerQualificationError), check():
        pass
    with pytest.raises(ValueError, match="外部人工核验"):
        store.set_reviewer_qualification_status(pending, "active")
    store.set_reviewer_qualification_status(pending, "active", confirmed_external_verification=True)
    with check() as bound:
        assert bound["qualification_id"] == pending and bound["account_id"] == "teacher-wang"
    for args in [("03", "03.04", None), ("04", None, None), ("04", "04.01", "2019-12-31"), ("04", "04.01", "2099-01-01")]:
        with pytest.raises(ReviewerQualificationError), check(*args):
            pass
    store.set_reviewer_qualification_status(pending, "suspended")
    with pytest.raises(ReviewerQualificationError), check():
        pass
    store.set_reviewer_qualification_status(pending, "active", confirmed_external_verification=True)
    store.disable("teacher-wang")
    with pytest.raises(ReviewerQualificationError), check():
        pass
    with pytest.raises(ValueError):
        store.set_reviewer_qualification_status(qid, "active", confirmed_external_verification=True)


def test_expired_and_future_validity_fail_closed(reviewer):
    store, qid = reviewer
    for start, end in [("2000-01-01", "2001-01-01"), ("2098-01-01", "2099-01-01")]:
        with store.connect() as db:
            db.execute("UPDATE reviewer_qualifications SET valid_from=?,valid_until=? WHERE qualification_id=?", (start, end, qid))
        assert store.reviewer_qualifications_for_account("teacher-wang")[0]["effective_status"] == "expired"
        with pytest.raises(ReviewerQualificationError), store.verified_reviewer_for_publication("teacher-wang", "04", "04.01", date.today().isoformat()):
            pass


def test_api_binds_identity_rejects_forgery_and_preserves_history(tmp_path, monkeypatch, reviewer):
    store, qid = reviewer
    api = Api(tmp_path, monkeypatch)
    assert api.client.get("/api/teacher/reviewer-qualifications", headers=api.headers).status_code == 403
    own = api.client.get("/api/teacher/reviewer-qualifications", headers=TEACHER).json()
    assert own["account_linked"] and own["records"][0]["qualification_id"] == qid
    assert "verified_by" not in own["records"][0]
    assert put(api, new_case()).status_code == 200
    for action in ("validate", "submit-review"):
        assert step(api, "TEACH-001", 1, action).status_code == 200
    for extra in ({"reviewer": "forged"}, {"reviewer_account_id": "other"}, {"reviewer_qualification_id": qid}, {"confirm_medical_review": "yes"}):
        assert step(api, "TEACH-001", 1, "publish", {**PUBLISH, **extra}).status_code == 422
    assert step(api, "TEACH-001", 1, "publish", PUBLISH).status_code == 200
    published = api.store.get("TEACH-001", 1)
    assert published.reviewer_account_id == "teacher-wang" and published.reviewer_qualification_id == qid
    event = api.store.audit_events("TEACH-001", 1)[-1]
    assert event["reviewer_account_id"] == "teacher-wang" and event["reviewer_qualification_id"] == qid
    raw = published.model_dump(mode="json")
    raw.update(review_status="draft", title="synthetic revised draft")
    assert put(api, raw).status_code == 200  # imported stale bindings cleared before validation
    assert api.store.get("TEACH-001", 2).reviewer_qualification_id is None
    before = published.model_dump_json()
    store.set_reviewer_qualification_status(qid, "revoked")
    for action in ("validate", "submit-review"):
        assert step(api, "TEACH-001", 2, action).status_code == 200
    assert step(api, "TEACH-001", 2, "publish", PUBLISH).status_code == 403
    assert api.store.get("TEACH-001", 1).model_dump_json() == before


def test_qualification_audit_failure_rolls_back_and_read_is_nonmutating(tmp_path, monkeypatch, reviewer):
    store, qid = reviewer
    before = store.read_reviewer_qualification_audit(store.path)
    def fail(*args):
        raise sqlite3.OperationalError("synthetic audit failure")
    monkeypatch.setattr(store, "_append_qualification_audit", fail)
    with pytest.raises(sqlite3.Error):
        store.set_reviewer_qualification_status(qid, "revoked")
    with pytest.raises(sqlite3.Error):
        store.create_reviewer_qualification("teacher-wang", "04", "04.01", "2020-01-01", "2099-12-31")
    assert store.reviewer_qualifications_for_account("teacher-wang")[0]["effective_status"] == "active"
    assert len(store.reviewer_qualifications_for_account("teacher-wang")) == 1
    assert store.read_reviewer_qualification_audit(store.path) == before
    missing = tmp_path / "missing.db"
    assert store.read_reviewer_qualification_audit(missing) == [] and not missing.exists()
    old = tmp_path / "old.db"
    with sqlite3.connect(old) as db:
        db.execute("CREATE TABLE old_data (value TEXT)")
    data = old.read_bytes()
    assert store.read_reviewer_qualification_audit(old) == [] and old.read_bytes() == data


def test_revocation_waits_for_publication_guard(reviewer):
    store, qid = reviewer
    acquired, attempt, release = Event(), Event(), Event()
    def publication():
        with store.verified_reviewer_for_publication("teacher-wang", "04", "04.01", date.today().isoformat()):
            acquired.set()
            assert release.wait(5)
    def revoke():
        attempt.set()
        return store.set_reviewer_qualification_status(qid, "revoked")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(publication)
        try:
            assert acquired.wait(5)
            second = pool.submit(revoke)
            assert attempt.wait(5)
            assert not second.done()
        finally:
            release.set()
        first.result(timeout=5)
        assert second.result(timeout=5)
    with pytest.raises(ReviewerQualificationError), store.verified_reviewer_for_publication("teacher-wang", "04", "04.01", date.today().isoformat()):
        pass


def test_cli_pending_activation_and_audit(tmp_path):
    store = AccountStore(tmp_path / "accounts.db")
    store.create("synthetic-cli", "synthetic-cli-only-password", "teacher", "合成核验测试人员")
    env = {**os.environ, "APP_DATA_DIR": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
    def cli(*args):
        return subprocess.run([sys.executable, str(ROOT / "scripts/manage_accounts.py"), *args], env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
    made = cli("qualification-create", "synthetic-cli", "--department-code", "04", "--subspecialty-code", "04.01", "--valid-from", "2020-01-01", "--valid-until", "2099-12-31")
    assert made.returncode == 0, made.stderr
    qid = store.reviewer_qualifications_for_account(store.find("synthetic-cli")["id"])[0]["qualification_id"]
    assert cli("qualification-status", qid, "--status", "active").returncode != 0
    assert cli("qualification-status", qid, "--status", "active", "--confirm-external-verification").returncode == 0
    assert len(json.loads(cli("qualification-audit").stdout)) == 2


def test_management_rejects_invalid_identity_scope_and_operator(reviewer):
    store, qid = reviewer
    store.create("synthetic-student", "synthetic-student-only-password", "student", "合成学生")
    for username, department, sub, start, end in [
        ("synthetic-student", "04", "04.01", "2020-01-01", "2099-12-31"),
        ("missing-teacher", "04", "04.01", "2020-01-01", "2099-12-31"),
        ("teacher-wang", "03", "04.01", "2020-01-01", "2099-12-31"),
        ("teacher-wang", "bad", None, "2020-01-01", "2099-12-31"),
        ("teacher-wang", "04", "04.01", "20200101", "2099-12-31"),
        ("teacher-wang", "04", "04.01", "2099-12-31", "2020-01-01"),
    ]:
        with pytest.raises(ValueError):
            store.create_reviewer_qualification(username, department, sub, start, end)
    with pytest.raises(PermissionError):
        store.set_reviewer_qualification_status(qid, "revoked", actor_id="teacher-wang", entry="teacher_api")
    assert len(store.read_reviewer_qualification_audit(store.path)) == 2


def test_broken_qualification_store_blocks_publication(tmp_path, monkeypatch, reviewer):
    store, _ = reviewer
    api = Api(tmp_path, monkeypatch)
    assert put(api, new_case()).status_code == 200
    for action in ("validate", "submit-review"):
        assert step(api, "TEACH-001", 1, action).status_code == 200
    with store.connect() as db:
        db.execute("DROP TABLE reviewer_qualification_audit")
        db.execute("DROP TABLE reviewer_qualifications")
    response = api.client.get("/api/teacher/reviewer-qualifications", headers=TEACHER)
    assert response.status_code == 503 and str(tmp_path) not in response.text
    assert step(api, "TEACH-001", 1, "publish", PUBLISH).status_code == 503
    assert api.store.get("TEACH-001", 1).review_status.value == "under_review"

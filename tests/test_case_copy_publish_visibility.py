"""Copy-and-publish visibility in production mode, using clearly labelled simulated review data.

The publish step in this test is an ENGINEERING FIXTURE, not a medical review:
the reviewer string says so explicitly. This test does not prove any real medical
review happened, and it must not be cited as a production release or clinical approval.

What it does prove, offline and with the simulator:
- a teacher can copy a built-in case with the existing "copy as new draft" flow;
- the copy stays invisible to students while draft / validated / under_review;
- after the simulated publish, students see only the published copy, can create a
  training and seal it;
- a later teacher draft version does not change the published version or an existing
  training's case_version;
- the built-in source file is never modified.
"""
from __future__ import annotations

import copy
import hashlib
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.accounts import AccountStore
from app.case_store import CaseStore
from app.integration.runtime import IntegrationRuntime
from app.state_machine import StateMachine
from app.storage import SessionRepository
from test_integration import ROOT, settings_for

TEACHER = ("copy_teacher", "Teacher-Copy-2026")
STUDENT = ("copy_student", "Student-Copy-2026")
# Clearly labelled simulated review data for an isolated engineering test only.
SIMULATED_REVIEWER = "模拟审核（测试夹具，非真实医学审核）"
SOURCE_FILE = ROOT / "cases" / "stemi_case_v3.json"


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def install(tmp_path: Path, monkeypatch) -> tuple[TestClient, dict[str, str], dict[str, str]]:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_AUTH_MODE", "local")
    repository = SessionRepository(tmp_path / "app.db")
    store = CaseStore(ROOT / "cases", tmp_path / "teacher_cases")
    accounts = AccountStore(tmp_path / "accounts.db")
    accounts.create(*TEACHER, "teacher", "合成教师（测试夹具）", must_change=False)
    accounts.create(*STUDENT, "student", "合成学生（测试夹具）", must_change=False)
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "CASE_STORE", store)
    monkeypatch.setattr(main, "MACHINE", StateMachine(store, repository))
    monkeypatch.setattr(main, "ACCOUNTS", accounts)
    monkeypatch.setattr(main, "RUNTIME", IntegrationRuntime(settings_for(tmp_path, mode="simulator")))
    client = TestClient(main.app)
    return client, login(client, *TEACHER), login(client, *STUDENT)


def copy_payload(source: dict) -> dict:
    """Mirror the page's newDraft(): new TEACH id, version 1, draft, no public id."""
    payload = copy.deepcopy(source)
    payload.update({
        "case_id": "TEACH-COPY-TEST",
        "title": payload["title"] + "（测试副本）",
        "version": 1,
        "review_status": "draft",
        "reviewer": None,
        "review_date": None,
        "authored_by": None,
    })
    payload.pop("public_id", None)
    return payload


def test_copy_then_simulated_publish_controls_student_visibility(tmp_path, monkeypatch):
    client, teacher, student = install(tmp_path, monkeypatch)
    source_hash = hashlib.sha256(SOURCE_FILE.read_bytes()).hexdigest()

    # Production starts empty: the 11 repository versions are drafts and no teacher asset exists.
    listed = client.get("/api/cases", headers=student)
    assert listed.status_code == 200
    assert listed.json() == []

    source = client.get("/api/teacher/cases/STEMI-001/versions/3", headers=teacher)
    assert source.status_code == 200, source.text
    assert source.json()["public_id"] == "CASE-001"
    assert source.json()["review_status"] == "draft"

    saved = client.put("/api/teacher/cases/TEACH-COPY-TEST", headers=teacher, json=copy_payload(source.json()))
    assert saved.status_code == 200, saved.text
    meta = saved.json()
    assert meta["version"] == 1 and meta["review_status"] == "draft"
    assert meta["public_id"].startswith("CASE-T")
    copy_public_id = meta["public_id"]

    # Draft: students see nothing and cannot start a training on it.
    still_empty = client.get("/api/cases", headers=student)
    assert still_empty.status_code == 200 and still_empty.json() == []
    assert client.post(f"/api/sessions?case_id={copy_public_id}", headers=student).status_code == 404

    # Draft and validated cannot jump straight to published, even with the simulated review payload.
    review_payload = {"reviewer": SIMULATED_REVIEWER, "review_date": date.today().isoformat(),
                      "confirm_medical_review": True}
    assert client.post("/api/teacher/cases/TEACH-COPY-TEST/versions/1/publish",
                       headers=teacher, json=review_payload).status_code == 409
    assert client.post(f"/api/teacher/cases/TEACH-COPY-TEST/versions/1/validate",
                       headers=teacher).status_code == 200
    assert client.post("/api/teacher/cases/TEACH-COPY-TEST/versions/1/publish",
                       headers=teacher, json=review_payload).status_code == 409
    assert client.post(f"/api/teacher/cases/TEACH-COPY-TEST/versions/1/submit-review",
                       headers=teacher).status_code == 200
    under_review = client.get("/api/teacher/cases", headers=teacher).json()
    assert next(v for v in under_review if v["case_id"] == "TEACH-COPY-TEST")["review_status"] == "under_review"
    hidden = client.get("/api/cases", headers=student)
    assert hidden.status_code == 200 and hidden.json() == []

    publish = client.post(
        "/api/teacher/cases/TEACH-COPY-TEST/versions/1/publish", headers=teacher,
        json=review_payload,
    )
    assert publish.status_code == 200, publish.text
    assert publish.json()["review_status"] == "published"

    # Student sees only the published copy, not the still-draft built-in source.
    visible = client.get("/api/cases", headers=student)
    assert visible.status_code == 200
    assert [(c["public_id"], c["version"]) for c in visible.json()] == [(copy_public_id, 1)]

    created = client.post(f"/api/sessions?case_id={copy_public_id}", headers=student)
    assert created.status_code == 200, created.text
    session = created.json()
    session_id = session["session_id"]
    assert session["case"]["version"] == 1

    sealed = client.post(f"/api/sessions/{session_id}/seal", headers=student, json={"request_id": "seal-copy"})
    assert sealed.status_code == 200, sealed.text
    assert client.get(f"/api/sessions/{session_id}/score", headers=student).status_code == 200

    # A later teacher draft must not replace the published version or rewrite the existing training.
    latest = client.get("/api/teacher/cases/TEACH-COPY-TEST/versions/1", headers=teacher).json()
    latest["title"] = latest["title"] + "（第二版草稿）"
    second = client.put("/api/teacher/cases/TEACH-COPY-TEST", headers=teacher, json=latest)
    assert second.status_code == 200, second.text
    assert second.json()["version"] == 2 and second.json()["review_status"] == "draft"

    visible_again = client.get("/api/cases", headers=student).json()
    assert [(c["public_id"], c["version"]) for c in visible_again] == [(copy_public_id, 1)]
    detail = client.get(f"/api/sessions/{session_id}", headers=student)
    assert detail.status_code == 200
    assert detail.json()["case"]["version"] == 1
    assert detail.json()["case"]["public_id"] == copy_public_id

    # The built-in source stays byte-identical; the copy lives only in the teacher directory.
    assert hashlib.sha256(SOURCE_FILE.read_bytes()).hexdigest() == source_hash
    assert len(list((tmp_path / "teacher_cases").glob("TEACH-COPY-TEST__v*.json"))) == 2

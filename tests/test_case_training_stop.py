"""Stopping a published case version blocks new trainings only.

Publishing here uses an explicit engineering fixture ("模拟审核（测试夹具，非真实医学审核）").
It is not a medical review and must not be cited as one.

Covered:
- before stop: the published version is visible and trainable (library, direct id, assignment);
- after stop: library and direct start agree, no fallback to an older published version;
- an in-progress training can still be opened and sealed; a sealed score is byte-for-byte stable;
- students cannot stop, repeats do not create duplicate records, a fresh runtime still enforces it;
- stopping an older version does not affect a newer published version.
"""
from __future__ import annotations

import copy
import sqlite3
from datetime import date

from fastapi.testclient import TestClient

from app import main
from app.accounts import AccountStore
from app.case_store import CaseStore
from app.integration.runtime import IntegrationRuntime
from app.state_machine import StateMachine
from app.storage import SessionRepository
from test_integration import ROOT, settings_for

TEACHER = ("stop_teacher", "Teacher-Stop-2026")
STUDENT = ("stop_student", "Student-Stop-2026")
CASE_ID = "TEACH-STOP-TEST"
REVIEWER = "模拟审核（测试夹具，非真实医学审核）"
REASON = "医学审核发现需要修订，先停止新训练"


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def install(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_AUTH_MODE", "local")
    repository = SessionRepository(tmp_path / "app.db")
    store = CaseStore(ROOT / "cases", tmp_path / "teacher_cases")
    accounts = AccountStore(tmp_path / "accounts.db")
    accounts.create(*TEACHER, "teacher", "合成教师（测试夹具）", must_change=False)
    accounts.create(*STUDENT, "student", "合成学生（测试夹具）", must_change=False)
    runtime = IntegrationRuntime(settings_for(tmp_path, mode="simulator"))
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "CASE_STORE", store)
    monkeypatch.setattr(main, "MACHINE", StateMachine(store, repository))
    monkeypatch.setattr(main, "ACCOUNTS", accounts)
    monkeypatch.setattr(main, "RUNTIME", runtime)
    client = TestClient(main.app)
    return client, login(client, *TEACHER), login(client, *STUDENT), store, runtime


def publish_copy(client: TestClient, teacher: dict[str, str], title_suffix: str = "（测试副本）") -> dict:
    source = client.get("/api/teacher/cases/STEMI-001/versions/3", headers=teacher)
    assert source.status_code == 200, source.text
    payload = copy.deepcopy(source.json())
    payload.update({"case_id": CASE_ID, "title": payload["title"] + title_suffix, "version": 1,
                    "review_status": "draft", "reviewer": None, "review_date": None, "authored_by": None})
    payload.pop("public_id", None)
    saved = client.put(f"/api/teacher/cases/{CASE_ID}", headers=teacher, json=payload)
    assert saved.status_code == 200, saved.text
    for action in ("validate", "submit-review"):
        response = client.post(f"/api/teacher/cases/{CASE_ID}/versions/1/{action}", headers=teacher)
        assert response.status_code == 200, response.text
    published = client.post(
        f"/api/teacher/cases/{CASE_ID}/versions/1/publish", headers=teacher,
        json={"reviewer": REVIEWER, "review_date": date.today().isoformat(), "confirm_medical_review": True},
    )
    assert published.status_code == 200, published.text
    return published.json()


def stop(client: TestClient, teacher: dict[str, str], version: int = 1, request_id: str = "stop-1",
         reason: str = REASON) -> dict:
    response = client.post(f"/api/teacher/cases/{CASE_ID}/versions/{version}/stop", headers=teacher,
                           json={"reason": reason, "request_id": request_id})
    assert response.status_code == 200, response.text
    return response.json()


def stop_rows(database) -> int:
    with sqlite3.connect(database) as connection:
        return connection.execute("SELECT COUNT(*) FROM case_training_stops").fetchone()[0]


def test_stop_blocks_new_training_and_preserves_in_progress_and_sealed(tmp_path, monkeypatch):
    client, teacher, student, store, runtime = install(tmp_path, monkeypatch)
    meta = publish_copy(client, teacher)
    public_id = meta["public_id"]

    assert [(c["public_id"], c["version"]) for c in client.get("/api/cases", headers=student).json()] == [(public_id, 1)]

    in_progress = client.post(f"/api/sessions?case_id={public_id}", headers=student).json()["session_id"]
    sealed_id = client.post(f"/api/sessions?case_id={public_id}", headers=student).json()["session_id"]
    for session_id, request_id, question in (
        (in_progress, "ask-a", "什么时候开始的"),
        (sealed_id, "ask-b", "疼痛是什么性质"),
    ):
        asked = client.post(f"/api/sessions/{session_id}/ask", headers=student,
                            json={"request_id": request_id, "question": question})
        assert asked.status_code == 200, asked.text
    assert client.post(f"/api/sessions/{sealed_id}/seal", headers=student,
                       json={"request_id": "seal-before-stop"}).status_code == 200
    sealed_detail_before = client.get(f"/api/sessions/{sealed_id}", headers=student).json()
    sealed_score_before = client.get(f"/api/sessions/{sealed_id}/score", headers=student).json()
    assert sealed_detail_before["state"]["events"], "sealed training must keep its recorded events"
    assert sealed_detail_before["state"]["sealed"] is True
    assert sealed_score_before["total_score"] > 0, "sealed score must be non-trivial for the stability check"

    record = stop(client, teacher)
    assert record["already_stopped"] is False
    assert record["case_version"] == 1 and record["public_id"] == public_id
    assert record["stopped_at"] > 0 and record["stopped_by"] and record["reason"] == REASON

    # Library and direct start agree: neither offers a new training, and the case is not hidden
    # behind an older published version.
    listed = client.get("/api/cases", headers=student)
    assert listed.status_code == 200 and listed.json() == []
    blocked = client.post(f"/api/sessions?case_id={public_id}", headers=student)
    assert blocked.status_code == 409 and "停止" in blocked.json()["detail"]

    # Only a teacher can stop.
    assert client.post(f"/api/teacher/cases/{CASE_ID}/versions/1/stop", headers=student,
                       json={"reason": "学生尝试", "request_id": "student-stop"}).status_code == 403

    # An in-progress training can still be read and completed on its original version.
    detail = client.get(f"/api/sessions/{in_progress}", headers=student)
    assert detail.status_code == 200 and detail.json()["case"]["version"] == 1
    assert client.post(f"/api/sessions/{in_progress}/seal", headers=student,
                       json={"request_id": "seal-after-stop"}).status_code == 200
    assert client.get(f"/api/sessions/{in_progress}/score", headers=student).status_code == 200

    # A sealed training and its score are unchanged by the stop.
    assert client.get(f"/api/sessions/{sealed_id}", headers=student).json() == sealed_detail_before
    assert client.get(f"/api/sessions/{sealed_id}/score", headers=student).json() == sealed_score_before

    # Repeats do not create a second record.
    again = stop(client, teacher, request_id="stop-2", reason="另一个原因")
    assert again["already_stopped"] is True and again["reason"] == REASON
    same = stop(client, teacher, request_id="stop-1")
    assert same["already_stopped"] is True
    reused = client.post(f"/api/teacher/cases/{CASE_ID}/versions/1/stop", headers=teacher,
                         json={"reason": "同一请求编号换了原因", "request_id": "stop-1"})
    assert reused.status_code == 409
    assert stop_rows(runtime.settings.integration_db_path) == 1

    # The case file and its medical review fields are untouched.
    case = store.get(CASE_ID, 1)
    assert case.review_status.value == "published" and case.reviewer == REVIEWER

    # A fresh runtime on the same integration database still sees the stop (service restart).
    fresh_runtime = IntegrationRuntime(settings_for(tmp_path, mode="simulator"))
    assert stop_rows(fresh_runtime.settings.integration_db_path) == 1
    monkeypatch.setattr(main, "RUNTIME", fresh_runtime)
    restarted = TestClient(main.app)
    assert restarted.get("/api/cases", headers=student).json() == []
    assert restarted.post(f"/api/sessions?case_id={public_id}", headers=student).status_code == 409


def test_stopping_an_old_version_does_not_affect_newer_or_fall_back(tmp_path, monkeypatch):
    client, teacher, student, _, _ = install(tmp_path, monkeypatch)
    meta = publish_copy(client, teacher)
    public_id = meta["public_id"]

    # Publish a newer version with the same public id.
    latest = client.get(f"/api/teacher/cases/{CASE_ID}/versions/1", headers=teacher).json()
    latest["title"] = latest["title"] + "（第二版）"
    second = client.put(f"/api/teacher/cases/{CASE_ID}", headers=teacher, json=latest)
    assert second.status_code == 200 and second.json()["version"] == 2
    for action in ("validate", "submit-review"):
        assert client.post(f"/api/teacher/cases/{CASE_ID}/versions/2/{action}", headers=teacher).status_code == 200
    published_two = client.post(
        f"/api/teacher/cases/{CASE_ID}/versions/2/publish", headers=teacher,
        json={"reviewer": REVIEWER, "review_date": date.today().isoformat(), "confirm_medical_review": True},
    )
    assert published_two.status_code == 200, published_two.text
    assert [(c["public_id"], c["version"]) for c in client.get("/api/cases", headers=student).json()] == [(public_id, 2)]

    # Stopping v1 must not hurt the newer published v2.
    stop(client, teacher, version=1, request_id="stop-v1")
    assert [(c["public_id"], c["version"]) for c in client.get("/api/cases", headers=student).json()] == [(public_id, 2)]
    started = client.post(f"/api/sessions?case_id={public_id}", headers=student)
    assert started.status_code == 200 and started.json()["case"]["version"] == 2

    # Stopping v2 removes the public id entirely: no silent fallback to the published-but-stopped v1.
    stop(client, teacher, version=2, request_id="stop-v2")
    conflicting = client.post(f"/api/teacher/cases/{CASE_ID}/versions/2/stop", headers=teacher,
                              json={"reason": REASON, "request_id": "stop-v1"})
    assert conflicting.status_code == 409
    assert client.get("/api/cases", headers=student).json() == []
    blocked = client.post(f"/api/sessions?case_id={public_id}", headers=student)
    assert blocked.status_code == 409 and "停止" in blocked.json()["detail"]


def test_stop_requires_a_published_version_and_a_known_target(tmp_path, monkeypatch):
    client, teacher, _, _, _ = install(tmp_path, monkeypatch)
    source = client.get("/api/teacher/cases/STEMI-001/versions/3", headers=teacher).json()
    payload = copy.deepcopy(source)
    payload.update({"case_id": CASE_ID, "title": payload["title"] + "（草稿）", "version": 1,
                    "review_status": "draft", "reviewer": None, "review_date": None, "authored_by": None})
    payload.pop("public_id", None)
    assert client.put(f"/api/teacher/cases/{CASE_ID}", headers=teacher, json=payload).status_code == 200

    draft = client.post(f"/api/teacher/cases/{CASE_ID}/versions/1/stop", headers=teacher,
                        json={"reason": "草稿不应可停止", "request_id": "stop-draft"})
    assert draft.status_code == 409
    unknown = client.post(f"/api/teacher/cases/{CASE_ID}/versions/9/stop", headers=teacher,
                          json={"reason": "版本不存在", "request_id": "stop-unknown"})
    assert unknown.status_code == 404


def test_assignment_start_respects_stop_and_resumes_an_open_session(tmp_path, monkeypatch):
    client, teacher, student, _, _ = install(tmp_path, monkeypatch)
    meta = publish_copy(client, teacher)
    public_id = meta["public_id"]

    created = client.post("/api/teacher/classes", headers=teacher, json={"name": "停止新训练测试班"})
    assert created.status_code == 200, created.text
    class_id = created.json()["id"]
    added = client.post(f"/api/teacher/classes/{class_id}/students", headers=teacher,
                        json={"students": [{"username": STUDENT[0], "display_name": "合成学生"}]})
    assert added.status_code == 200, added.text
    assignment = client.post(f"/api/teacher/classes/{class_id}/assignments", headers=teacher,
                             json={"case_public_id": public_id, "title": "停止入口测试"})
    assert assignment.status_code == 200, assignment.text
    assignment_id = assignment.json()["id"]

    before = client.get("/api/assignments", headers=student).json()
    assert before[0]["case"]["available"] is True
    started = client.post(f"/api/assignments/{assignment_id}/start", headers=student)
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]

    assert client.post(f"/api/sessions/{session_id}/seal", headers=student,
                       json={"request_id": "seal-first-attempt"}).status_code == 200
    first_sealed = session_id
    second = client.post(f"/api/assignments/{assignment_id}/start", headers=student)
    assert second.status_code == 200
    session_id = second.json()["session_id"]
    assert session_id != first_sealed
    stop(client, teacher)
    after = client.get("/api/assignments", headers=student).json()
    assert after[0]["latest_sealed_session_id"] == first_sealed
    assert after[0]["open_session_id"] == session_id
    assert after[0]["case"]["available"] is False
    assert after[0]["case"]["unavailable_reason"] == "stopped"

    # An already-open training is resumed, not silently replaced by another version.
    resumed = client.post(f"/api/assignments/{assignment_id}/start", headers=student)
    assert resumed.status_code == 200
    assert resumed.json() == {"session_id": session_id, "resumed": True}

    assert client.post(f"/api/sessions/{session_id}/seal", headers=student,
                       json={"request_id": "seal-assignment"}).status_code == 200
    blocked = client.post(f"/api/assignments/{assignment_id}/start", headers=student)
    assert blocked.status_code == 409 and "停止" in blocked.json()["detail"]

    # A teacher cannot create a new assignment pointing at the stopped public id.
    rejected = client.post(f"/api/teacher/classes/{class_id}/assignments", headers=teacher,
                           json={"case_public_id": public_id, "title": "新任务"})
    assert rejected.status_code == 422

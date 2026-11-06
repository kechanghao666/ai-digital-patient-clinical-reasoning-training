from fastapi.testclient import TestClient

from app import main
from app.case_store import CaseStore
from app.state_machine import StateMachine
from app.storage import SessionRepository


def test_http_contracts_and_generic_seal_are_controlled(tmp_path, monkeypatch):
    repository = SessionRepository(tmp_path / "api.db")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(CaseStore(main.ROOT / "cases"), repository))
    client = TestClient(main.app)
    headers = {"X-Owner-Id": "demo-api"}

    missing = client.post("/api/sessions?case_id=missing", headers=headers)
    assert missing.status_code == 404

    created = client.post("/api/sessions?case_id=CASE-001", headers=headers)
    assert created.status_code == 200
    body = created.json()
    session_id = body["session_id"]
    assert "case_id" not in body["state"]
    assert "owner_id" not in body["state"]

    first = client.post(
        f"/api/sessions/{session_id}/ask",
        headers=headers,
        json={"request_id": "same-request", "question": "疼痛是什么性质"},
    )
    assert first.status_code == 200
    assert "disclosed_fact_ids" not in first.json()

    changed = client.post(
        f"/api/sessions/{session_id}/ask",
        headers=headers,
        json={"request_id": "same-request", "question": "放射到哪里"},
    )
    assert changed.status_code == 409

    sealed = client.post(
        f"/api/sessions/{session_id}/action",
        headers=headers,
        json={"request_id": "seal-request", "action": "seal", "payload": {}},
    )
    assert sealed.status_code == 200
    score = client.get(f"/api/sessions/{session_id}/score", headers=headers)
    assert score.status_code == 200
    assert score.json()["seal_seq"] == 1
    assert "score_snapshot" not in body["state"]

    blocked = client.post(
        f"/api/sessions/{session_id}/ask",
        headers=headers,
        json={"request_id": "after-seal", "question": "放射到哪里"},
    )
    assert blocked.status_code == 409


def test_session_list_and_replay_projection(tmp_path, monkeypatch):
    repository = SessionRepository(tmp_path / "list.db")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(CaseStore(main.ROOT / "cases"), repository))
    client = TestClient(main.app)
    headers = {"X-Owner-Id": "demo-list"}
    created = client.post("/api/sessions?case_id=CASE-001", headers=headers)
    session_id = created.json()["session_id"]

    listed = client.get("/api/sessions", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["session_id"] == session_id

    detail = client.get(f"/api/sessions/{session_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["state"]["events"] == []


def test_same_summary_sessions_keep_distinct_restore_ids(tmp_path, monkeypatch):
    repository = SessionRepository(tmp_path / "same-summary.db")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(CaseStore(main.ROOT / "cases"), repository))
    client = TestClient(main.app)
    headers = {"X-Owner-Id": "demo-same-summary"}

    session_ids = [
        client.post("/api/sessions?case_id=CASE-001", headers=headers).json()["session_id"]
        for _ in range(3)
    ]
    listed = client.get("/api/sessions", headers=headers)
    assert listed.status_code == 200
    entries = listed.json()
    assert {item["session_id"] for item in entries} >= set(session_ids)
    assert len({item["summary_id"] for item in entries if item["session_id"] in session_ids}) == 3


def test_restored_submissions_are_publicly_projected(tmp_path, monkeypatch):
    repository = SessionRepository(tmp_path / "submissions.db")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(CaseStore(main.ROOT / "cases"), repository))
    client = TestClient(main.app)
    headers = {"X-Owner-Id": "demo-submissions"}
    session_id = client.post("/api/sessions?case_id=CASE-001", headers=headers).json()["session_id"]

    assert client.post(
        f"/api/sessions/{session_id}/diagnosis",
        headers=headers,
        json={"request_id": "restore-diagnosis", "diagnosis": "STEMI"},
    ).status_code == 200
    assert client.post(
        f"/api/sessions/{session_id}/differential",
        headers=headers,
        json={
            "request_id": "restore-ddx",
            "candidates": [{
                "candidate_id": "aortic_dissection",
                "supporting_evidence": ["突发胸痛"],
                "opposing_evidence": ["心电图支持 STEMI"],
                "evidence_event_ids": ["event-1"],
            }],
        },
    ).status_code == 200
    assert client.post(
        f"/api/sessions/{session_id}/treatment",
        headers=headers,
        json={
            "request_id": "restore-treatment",
            "treatment_id": "aspirin",
            "parameters": {"dose": "300mg"},
            "confirm_execution": True,
        },
    ).status_code == 200

    detail = client.get(f"/api/sessions/{session_id}", headers=headers)
    assert detail.status_code == 200
    submissions = detail.json()["state"]["submissions"]
    assert submissions["diagnosis"] == "STEMI"
    assert submissions["differential"][0]["supporting_evidence"] == ["突发胸痛"]
    assert submissions["differential"][0]["evidence_event_ids"] == ["event-1"]
    assert submissions["treatment"]["treatment_id"] == "aspirin"
    assert submissions["treatment"]["treatment_name"] == "阿司匹林"
    assert submissions["treatment"]["parameters"] == {"dose": "300mg"}
    assert "owner_id" not in submissions

    replay = detail.json()["state"]["events"]
    assert any(event["result"].get("diagnosis") == "STEMI" for event in replay)
    assert any(event["result"].get("candidates") for event in replay)
    assert any(event["result"].get("treatment", {}).get("treatment_id") == "aspirin" for event in replay)


def test_case_notes_are_publicly_reloaded_without_internal_fields(tmp_path, monkeypatch):
    repository = SessionRepository(tmp_path / "notes.db")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(CaseStore(main.ROOT / "cases"), repository))
    client = TestClient(main.app)
    headers = {"X-Owner-Id": "demo-notes"}
    session_id = client.post("/api/sessions?case_id=CASE-001", headers=headers).json()["session_id"]
    response = client.post(
        f"/api/sessions/{session_id}/action",
        headers=headers,
        json={
            "request_id": "notes-1",
            "action": "update_notes",
            "payload": {"notes": "胸痛两小时，拟完善心电图。"},
        },
    )
    assert response.status_code == 200
    detail = client.get(f"/api/sessions/{session_id}", headers=headers).json()
    assert detail["state"]["case_notes"] == "胸痛两小时，拟完善心电图。"
    assert detail["state"]["events"][-1]["action"] == "update_notes"
    assert "owner_id" not in detail["state"]["events"][-1]


def test_public_order_projection_hides_internal_exam_id(tmp_path, monkeypatch):
    repository = SessionRepository(tmp_path / "order.db")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(CaseStore(main.ROOT / "cases"), repository))
    client = TestClient(main.app)
    headers = {"X-Owner-Id": "demo-order"}
    created = client.post("/api/sessions?case_id=CASE-001", headers=headers)
    session_id = created.json()["session_id"]
    response = client.post(
        f"/api/sessions/{session_id}/action",
        headers=headers,
        json={
            "request_id": "order",
            "action": "order_exam",
            "payload": {"exam_id": "ex_ecg"},
        },
    )
    assert response.status_code == 200
    public_order = response.json()["state"]["orders"][0]
    assert public_order["exam_name"] == "心电图"
    assert "exam_id" not in public_order
    assert "order_id" not in public_order
    assert "exam_id" not in response.json()["response"]
    assert "order_id" not in response.json()["response"]


def test_unknown_action_payload_is_rejected_without_persisting_event(tmp_path, monkeypatch):
    repository = SessionRepository(tmp_path / "payload.db")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(CaseStore(main.ROOT / "cases"), repository))
    client = TestClient(main.app)
    headers = {"X-Owner-Id": "demo-payload"}
    created = client.post("/api/sessions?case_id=CASE-001", headers=headers)
    session_id = created.json()["session_id"]
    response = client.post(
        f"/api/sessions/{session_id}/action",
        headers=headers,
        json={
            "request_id": "bad-order",
            "action": "order_exam",
            "payload": {"exam_id": "ex_ecg", "unexpected": True},
        },
    )
    assert response.status_code == 400
    detail = client.get(f"/api/sessions/{session_id}", headers=headers).json()["state"]
    assert detail["state_version"] == 0
    assert detail["orders"] == []


def test_specialized_submission_endpoints_strip_request_metadata(tmp_path, monkeypatch):
    repository = SessionRepository(tmp_path / "specialized.db")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(CaseStore(main.ROOT / "cases"), repository))
    client = TestClient(main.app)
    headers = {"X-Owner-Id": "demo-specialized"}
    session_id = client.post("/api/sessions?case_id=CASE-001", headers=headers).json()["session_id"]

    diagnosis = client.post(
        f"/api/sessions/{session_id}/diagnosis",
        headers=headers,
        json={"request_id": "diagnosis-1", "diagnosis": "STEMI"},
    )
    assert diagnosis.status_code == 200, diagnosis.text

    differential = client.post(
        f"/api/sessions/{session_id}/differential",
        headers=headers,
        json={
            "request_id": "ddx-1",
            "candidates": [
                {
                    "candidate_id": "aortic_dissection",
                    "supporting_evidence": ["胸痛"],
                    "opposing_evidence": [],
                    "evidence_event_ids": [],
                }
            ],
        },
    )
    assert differential.status_code == 200, differential.text

    treatment = client.post(
        f"/api/sessions/{session_id}/treatment",
        headers=headers,
        json={
            "request_id": "treatment-1",
            "treatment_id": "aspirin",
            "parameters": {},
            "confirm_execution": True,
        },
    )
    assert treatment.status_code == 200, treatment.text
    assert [event.action.value for event in repository.get(session_id, "demo-specialized").events] == [
        "submit_diagnosis",
        "submit_differential",
        "execute_treatment",
    ]


def test_delay_update_is_public_and_persisted(tmp_path, monkeypatch):
    repository = SessionRepository(tmp_path / "delay.db")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(CaseStore(main.ROOT / "cases"), repository))
    client = TestClient(main.app)
    headers = {"X-Owner-Id": "demo-delay"}
    session_id = client.post("/api/sessions?case_id=CASE-001", headers=headers).json()["session_id"]
    order = client.post(
        f"/api/sessions/{session_id}/action",
        headers=headers,
        json={"request_id": "order-delay", "action": "order_exam", "payload": {"exam_id": "ex_ecg"}},
    )
    assert order.status_code == 200
    wait = client.post(
        f"/api/sessions/{session_id}/action",
        headers=headers,
        json={"request_id": "wait-delay", "action": "wait", "payload": {"minutes": 65}},
    )
    assert wait.status_code == 200
    body = wait.json()
    expected = "患者出现血压下降和意识模糊，需要立即重新评估。"
    assert body["response"]["clinical_updates"] == [expected]
    assert body["event"]["result"]["clinical_updates"] == [expected]
    detail = client.get(f"/api/sessions/{session_id}", headers=headers)
    assert detail.json()["state"]["clinical_updates"] == [expected]

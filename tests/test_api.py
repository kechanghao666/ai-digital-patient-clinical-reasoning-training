import pytest
from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)


def session():
    r = client.post("/sessions", headers={"X-Owner": "student-1"})
    assert r.status_code == 201
    return r.json()["session_id"]


def test_public_projection_hides_internal_fields():
    sid = session()
    payload = client.get(f"/sessions/{sid}", headers={"X-Owner": "student-1"}).json()
    assert "owner" not in payload
    assert "replay" not in payload


def test_composite_diagnosis_needs_review():
    sid = session()
    r = client.post(f"/sessions/{sid}/actions", headers={"X-Owner": "student-1"}, json={"request_id": "d1", "action": "diagnose", "text": "STEMI 并多病灶"})
    assert r.status_code == 200
    assert r.json()["score_status"] == "needs_review"
    assert r.json()["status"] == "active"


def test_request_replay_and_conflict():
    sid = session()
    payload = {"request_id": "a1", "action": "ask", "text": "胸痛"}
    first = client.post(f"/sessions/{sid}/actions", headers={"X-Owner": "student-1"}, json=payload)
    second = client.post(f"/sessions/{sid}/actions", headers={"X-Owner": "student-1"}, json=payload)
    assert first.json() == second.json()
    conflict = client.post(f"/sessions/{sid}/actions", headers={"X-Owner": "student-1"}, json={**payload, "text": "过敏"})
    assert conflict.status_code == 409


def test_successful_retry_has_no_error():
    sid = session()
    ok = client.post(f"/sessions/{sid}/actions", headers={"X-Owner": "student-1"}, json={"request_id": "r1", "action": "ask", "text": "胸痛"})
    assert ok.status_code == 200
    assert ok.json()["error"] is None


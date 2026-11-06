from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.storage import SessionStore
from backend.app.models import ActionRequest

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


def test_composite_negated_and_unknown_diagnoses_need_review():
    for value in ("STEMI 并多病灶", "不是 STEMI", "NSTEMI"):
        sid = session()
        r = client.post(f"/sessions/{sid}/actions", headers={"X-Owner": "student-1"}, json={"request_id": value, "action": "diagnose", "text": value})
        assert r.status_code == 200
        assert r.json()["score_status"] == "needs_review"
        assert r.json()["status"] == "active"


def test_only_complete_stemi_match_is_scored():
    sid = session()
    r = client.post(
        f"/sessions/{sid}/actions",
        headers={"X-Owner": "student-1"},
        json={"request_id": "exact-stemi", "action": "diagnose", "text": "STEMI"},
    )
    assert r.status_code == 200
    assert r.json()["score_status"] == "complete"
    assert r.json()["score"] == 100
    assert r.json()["status"] == "sealed"


def test_request_replay_and_conflict():
    sid = session()
    payload = {"request_id": "a1", "action": "ask", "text": "胸痛"}
    first = client.post(f"/sessions/{sid}/actions", headers={"X-Owner": "student-1"}, json=payload)
    second = client.post(f"/sessions/{sid}/actions", headers={"X-Owner": "student-1"}, json=payload)
    assert first.json() == second.json()
    conflict = client.post(f"/sessions/{sid}/actions", headers={"X-Owner": "student-1"}, json={**payload, "text": "过敏"})
    assert conflict.status_code == 409


def test_owner_isolation_and_sealed_rejection():
    sid = session()
    denied = client.get(f"/sessions/{sid}", headers={"X-Owner": "other"})
    assert denied.status_code == 404
    sealed = client.post(f"/sessions/{sid}/actions", headers={"X-Owner": "student-1"}, json={"request_id": "d", "action": "diagnose", "text": "STEMI"})
    assert sealed.json()["status"] == "sealed"
    rejected = client.post(f"/sessions/{sid}/actions", headers={"X-Owner": "student-1"}, json={"request_id": "next", "action": "wait", "text": ""})
    assert rejected.status_code == 409


def test_sqlite_restart_recovery_and_replay():
    with TemporaryDirectory() as directory:
        db = Path(directory) / "patient.sqlite3"
        first = SessionStore(db)
        state = first.create("student")
        request = ActionRequest(request_id="persist-1", action="ask", text="胸痛")
        response = first.apply(state.session_id, request)
        second = SessionStore(db)
        restored = second.get(state.session_id)
        assert restored is not None
        assert restored.round == 1
        assert restored.facts
        assert second.apply(state.session_id, request) == response


def test_successful_retry_has_no_error():
    sid = session()
    ok = client.post(f"/sessions/{sid}/actions", headers={"X-Owner": "student-1"}, json={"request_id": "r1", "action": "ask", "text": "胸痛"})
    assert ok.status_code == 200
    assert ok.json()["error"] is None

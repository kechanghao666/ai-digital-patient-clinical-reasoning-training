"""Teachers can inspect and review only students in their own classes."""
import pytest
from fastapi import HTTPException

from app import main
from app.accounts import AccountStore
from app.roster import RosterStore, roster_for
from app.teacher_scope import require_teacher_student, teacher_member_ids
from test_integration import Env


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    monkeypatch.setenv("APP_AUTH_MODE", "local")
    store = AccountStore(tmp_path / "scope-accounts.db")
    monkeypatch.setattr(main, "ACCOUNTS", store)
    roster = roster_for(store)
    password = "Synthetic-Scope-Test-2026"
    accounts, headers = {}, {}
    for name, role in (("teacher-a", "teacher"), ("teacher-b", "teacher"),
                       ("teacher-empty", "teacher"), ("student-a", "student"), ("student-b", "student")):
        accounts[name] = store.create(name, password, role, name)
        response = env.client.post("/api/auth/login", json={"username": name, "password": password})
        assert response.status_code == 200
        headers[name] = {"Authorization": "Bearer " + response.json()["token"]}
    sessions, classes = {}, {}
    for suffix in ("a", "b"):
        teacher, student = "teacher-" + suffix, "student-" + suffix
        klass = roster.create_class(accounts[teacher], "班级" + suffix)
        # Explicit trusted fixture relationship; production enrollment rejects unowned accounts.
        with store.connect() as db:
            db.execute("INSERT INTO class_members VALUES (?,?,0)", (klass["id"], accounts[student]))
        classes[teacher] = klass["id"]
        env.headers = headers[student]
        sid = env.new_session()
        assert env.client.post(f"/api/sessions/{sid}/seal", headers=env.headers,
                               json={"request_id": "seal"}).status_code == 200
        sessions[student] = env.repository.get(sid, accounts[student])
    return env, accounts, headers, sessions, classes


def requests_for(env, state):
    score = {"owner_id": state.owner_id, "request_id": "score", "expected_revision": 0,
             "reason": "核对合成训练", "confirmed": True,
             "items": [{"item_id": item.item_id, "score": 0.0, "reason": "封存证据不足。",
                        "evidence_event_ids": []} for item in state.score_snapshot.items]}
    case = env.service.cases.get(state.case_id, state.case_version)
    feedback = env.runtime.feedback_service.get_or_generate(state, case, "feedback")
    snapshot = env.runtime.knowledge.snapshot(feedback["items"], case.case_id, case.version)
    review = {"owner_id": state.owner_id, "request_id": "feedback-review", "expected_revision": 0,
              "reason": "核对合成反馈", "items": feedback["items"],
              "basis_hashes": {entry["basis_id"]: entry["content_hash"] for entry in snapshot["entries"]}}
    sid = state.session_id
    basis = {"session_id": sid, "owner_id": state.owner_id, "query": "病史采集"}
    return [
        ("GET", f"/api/teacher/scores/{sid}", {"params": {"owner_id": state.owner_id}}),
        ("POST", f"/api/teacher/scores/{sid}", {"json": score}),
        ("GET", f"/api/teacher/feedback/{sid}", {"params": {"owner_id": state.owner_id}}),
        ("POST", f"/api/teacher/feedback/{sid}", {"json": review}),
        ("POST", "/api/teacher/feedback/basis/search", {"json": basis}),
        ("POST", "/api/teacher/feedback/basis/rerank", {"json": basis}),
    ]


@pytest.mark.parametrize("suffix", ["a", "b"])
def test_teachers_cannot_read_or_write_each_others_students(scoped, suffix):
    env, accounts, headers, sessions, classes = scoped
    own, other = "teacher-" + suffix, "teacher-" + ("b" if suffix == "a" else "a")
    state = sessions["student-" + suffix]
    # A teacher cannot acquire authorization by enrolling the other teacher's known username.
    attack = env.client.post(f"/api/teacher/classes/{classes[other]}/students", headers=headers[other],
                             json={"students": [{"username": "student-" + suffix}]})
    assert attack.status_code == 200 and attack.json()["results"][0]["status"] == "rejected"
    assert "temporary_password" not in attack.json()["results"][0]
    assert env.client.post(f"/api/teacher/students/{state.owner_id}/reset-password",
                           headers=headers[other]).status_code == 404
    before = state.model_dump_json()
    requests = requests_for(env, state)
    for method, path, kwargs in requests:
        denied = env.client.request(method, path, headers=headers[other], **kwargs)
        assert denied.status_code == 404, (path, denied.text)
    assert env.runtime.score_reviews.list(state.session_id, state.owner_id) == []
    assert env.runtime.feedback_store.reviews(state.session_id, state.owner_id) == []
    for method, path, kwargs in requests:
        allowed = env.client.request(method, path, headers=headers[own], **kwargs)
        assert allowed.status_code == 200, (path, allowed.text)
    assert env.repository.get(state.session_id, state.owner_id).model_dump_json() == before
    board = env.client.get("/api/teacher/dashboard", headers=headers[own]).json()
    assert [item["owner_id"] for item in board["trainings"]] == [state.owner_id]
    assert env.client.get("/api/teacher/dashboard", headers=headers[other],
                          params={"class_id": classes[own]}).status_code == 404


def test_teacher_without_classes_has_no_access_even_in_demo_enabled_process(scoped, monkeypatch):
    env, accounts, headers, sessions, _ = scoped
    monkeypatch.setenv("APP_AUTH_MODE", "demo")
    empty = headers["teacher-empty"]
    board = env.client.get("/api/teacher/dashboard", headers=empty)
    assert board.status_code == 200 and board.json()["trainings"] == []
    for method, path, kwargs in requests_for(env, sessions["student-a"]):
        assert env.client.request(method, path, headers=empty, **kwargs).status_code == 404
        # Development header identities have no implicit authorization either.
        assert env.client.request(method, path, headers={"X-Owner-Id": "teacher-demo"},
                                  **kwargs).status_code == 404


def test_roster_failure_refuses_all_session_endpoints_without_writes(scoped, monkeypatch):
    env, _, headers, sessions, classes = scoped
    state = sessions["student-a"]
    requests = requests_for(env, state)

    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic roster failure")

    for method in ("teaches", "classes", "owned_class"):
        monkeypatch.setattr(RosterStore, method, unavailable)
    for method, path, kwargs in requests:
        assert env.client.request(method, path, headers=headers["teacher-a"], **kwargs).status_code == 503
    for params in ({}, {"class_id": classes["teacher-a"]}):
        assert env.client.get("/api/teacher/dashboard", headers=headers["teacher-a"], params=params).status_code == 503
    assert env.runtime.score_reviews.list(state.session_id, state.owner_id) == []
    assert env.runtime.feedback_store.reviews(state.session_id, state.owner_id) == []


def test_unconfigured_roster_is_unavailable():
    for check in (lambda: teacher_member_ids(None, "teacher"),
                  lambda: require_teacher_student(None, "teacher", "student")):
        with pytest.raises(HTTPException) as exc:
            check()
        assert exc.value.status_code == 503

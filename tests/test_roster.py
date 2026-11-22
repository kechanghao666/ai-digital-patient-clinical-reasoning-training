"""Classes, rosters, assignments and self-service passwords in local account mode."""
import pytest

from app import main
from app.accounts import AccountStore
from test_cases_config import Api

PASSWORD = "Teacher-Pass-2026"


@pytest.fixture
def env(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, env="development")
    monkeypatch.setenv("APP_AUTH_MODE", "local")
    store = AccountStore(tmp_path / "accounts.db")
    monkeypatch.setattr(main, "ACCOUNTS", store)
    store.create("teacher", PASSWORD, "teacher", "王老师")
    store.create("other", PASSWORD, "teacher", "李老师")
    return api, store


def login(api, username, password):
    response = api.client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}, response.json()["user"]


def test_teacher_builds_class_students_must_change_password_then_train_assignment(env):
    api, store = env
    teacher, _ = login(api, "teacher", PASSWORD)
    other, _ = login(api, "other", PASSWORD)
    klass = api.client.post("/api/teacher/classes", headers=teacher, json={"name": "临床医学2024级1班"}).json()
    added = api.client.post(f"/api/teacher/classes/{klass['id']}/students", headers=teacher,
                            json={"students": [{"username": "s2024001", "display_name": "张三"},
                                               {"username": "s2024002", "display_name": "李四"},
                                               {"username": "teacher"}]}).json()
    statuses = {r["username"]: r["status"] for r in added["results"]}
    assert statuses == {"s2024001": "created", "s2024002": "created", "teacher": "rejected"}
    temp = next(r["temporary_password"] for r in added["results"] if r["username"] == "s2024001")
    assert len(temp) == 16

    # Another teacher cannot see or change this class.
    assert api.client.get(f"/api/teacher/classes/{klass['id']}", headers=other).status_code == 404
    assert api.client.get("/api/teacher/classes", headers=other).json() == []

    task = api.client.post(f"/api/teacher/classes/{klass['id']}/assignments", headers=teacher,
                           json={"case_public_id": "CASE-002", "due_date": "2026-12-31", "note": "重点练习腹痛问诊"}).json()
    assert task["title"] == "腹痛一天的青年男性模拟患者"
    assert api.client.post(f"/api/teacher/classes/{klass['id']}/assignments", headers=teacher,
                           json={"case_public_id": "CASE-404"}).status_code == 422

    # First login with the issued password: everything except changing it is refused.
    student, user = login(api, "s2024001", temp)
    assert user["must_change_password"] is True
    assert api.client.get("/api/assignments", headers=student).status_code == 403
    weak = api.client.post("/api/auth/password", headers=student, json={"current_password": temp, "new_password": "short"})
    assert weak.status_code == 422
    wrong = api.client.post("/api/auth/password", headers=student,
                            json={"current_password": "Wrong-Pass-0000", "new_password": "Student-Pass-2026"})
    assert wrong.status_code == 403
    changed = api.client.post("/api/auth/password", headers=student,
                              json={"current_password": temp, "new_password": "Student-Pass-2026"}).json()
    assert changed["user"]["must_change_password"] is False
    assert api.client.get("/api/assignments", headers=student).status_code == 401  # old token revoked
    student = {"Authorization": "Bearer " + changed["token"]}

    mine = api.client.get("/api/assignments", headers=student).json()
    assert [(t["id"], t["status"]) for t in mine] == [(task["id"], "not_started")]
    first = api.client.post(f"/api/assignments/{task['id']}/start", headers=student).json()
    again = api.client.post(f"/api/assignments/{task['id']}/start", headers=student).json()
    assert again == {"session_id": first["session_id"], "resumed": True}
    api.client.post(f"/api/sessions/{first['session_id']}/seal", headers=student, json={"request_id": "seal"})
    mine = api.client.get("/api/assignments", headers=student).json()
    assert mine[0]["status"] == "completed" and mine[0]["best_ratio"] is not None

    detail = api.client.get(f"/api/teacher/classes/{klass['id']}", headers=teacher).json()
    assert len(detail["members"]) == 2
    progress = detail["assignments"][0]
    assert progress["completed"] == 1
    board = api.client.get(f"/api/teacher/dashboard?class_id={klass['id']}", headers=teacher).json()
    assert board["overview"]["trainings"] == 1
    assert api.client.get(f"/api/teacher/dashboard?class_id={klass['id']}", headers=other).status_code == 404

    # A student outside the class cannot start the assignment.
    store.create("outsider", "Outsider-Pass-2026", "student", "外班")
    outsider, _ = login(api, "outsider", "Outsider-Pass-2026")
    assert api.client.post(f"/api/assignments/{task['id']}/start", headers=outsider).status_code == 404


def test_reset_disable_only_for_own_students(env):
    api, store = env
    teacher, _ = login(api, "teacher", PASSWORD)
    other, _ = login(api, "other", PASSWORD)
    klass = api.client.post("/api/teacher/classes", headers=teacher, json={"name": "1班"}).json()
    api.client.post(f"/api/teacher/classes/{klass['id']}/students", headers=teacher, json={"students": [{"username": "s001"}, {"username": "x"}]})
    account = store.find("s001")
    assert api.client.post(f"/api/teacher/students/{account['id']}/reset-password", headers=other).status_code == 404
    reset = api.client.post(f"/api/teacher/students/{account['id']}/reset-password", headers=teacher).json()
    _, user = login(api, "s001", reset["temporary_password"])
    assert user["must_change_password"] is True
    assert api.client.post(f"/api/teacher/students/{account['id']}/disable", headers=teacher).json() == {"disabled": True}
    assert api.client.post("/api/auth/login", json={"username": "s001", "password": reset["temporary_password"]}).status_code == 401
    api.client.post(f"/api/teacher/students/{account['id']}/enable", headers=teacher)
    assert api.client.post("/api/auth/login", json={"username": "s001", "password": reset["temporary_password"]}).status_code == 200
    # Removing from the class revokes the teacher's authority over that student.
    api.client.delete(f"/api/teacher/classes/{klass['id']}/students/{account['id']}", headers=teacher)
    assert api.client.post(f"/api/teacher/students/{account['id']}/disable", headers=teacher).status_code == 404


def test_roster_requires_local_accounts(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, env="development")
    monkeypatch.setenv("APP_AUTH_MODE", "demo")
    assert api.client.get("/api/teacher/classes", headers={"X-Owner-Id": "teacher-x"}).status_code == 409
    assert api.client.get("/api/assignments", headers=api.headers).json() == []

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
    assert api.client.post(f"/api/teacher/students/{account['id']}/disable", headers=teacher).json() == {"disabled": True, "changed": True}
    assert api.client.post("/api/auth/login", json={"username": "s001", "password": reset["temporary_password"]}).status_code == 401
    assert api.client.post(f"/api/teacher/students/{account['id']}/enable", headers=teacher).json() == {"disabled": False, "changed": True}
    assert api.client.post("/api/auth/login", json={"username": "s001", "password": reset["temporary_password"]}).status_code == 200
    # Removing from the class revokes the teacher's authority over that student.
    api.client.delete(f"/api/teacher/classes/{klass['id']}/students/{account['id']}", headers=teacher)
    assert api.client.post(f"/api/teacher/students/{account['id']}/disable", headers=teacher).status_code == 404


def test_teacher_account_management_audit_is_scoped_and_idempotent(env):
    api, store = env
    teacher, teacher_user = login(api, "teacher", PASSWORD)
    other, _ = login(api, "other", PASSWORD)
    klass = api.client.post("/api/teacher/classes", headers=teacher, json={"name": "审计测试班"}).json()
    added = api.client.post(
        f"/api/teacher/classes/{klass['id']}/students", headers=teacher,
        json={"students": [{"username": "audit-s001", "display_name": "合成学生"}]},
    )
    assert added.json()["results"][0]["status"] == "created"
    student = store.find("audit-s001")
    account_id = student["id"]
    initial_password = added.json()["results"][0]["temporary_password"]
    created_log = store.account_audit_log()
    assert len(created_log) == 1
    assert created_log[0]["operation"] == "account_created"
    assert created_log[0]["target_account_id"] == account_id
    assert created_log[0]["actor_id"] == teacher_user["id"]
    assert created_log[0]["entry"] == "teacher_api"
    assert set(created_log[0]) == {"occurred_at", "operation", "target_account_id", "actor_id", "entry"}
    assert initial_password not in str(created_log)

    second_class = api.client.post("/api/teacher/classes", headers=teacher, json={"name": "已有学生班"}).json()
    enrolled = api.client.post(
        f"/api/teacher/classes/{second_class['id']}/students", headers=teacher,
        json={"students": [{"username": "audit-s001", "display_name": "不覆盖原姓名"}]},
    )
    assert enrolled.json()["results"][0]["status"] == "enrolled"
    assert store.account_audit_log() == created_log

    student_headers, _ = login(api, "audit-s001", initial_password)
    before = store.get(account_id)
    with store.connect() as db:
        sessions_before = db.execute("SELECT COUNT(*) FROM login_sessions WHERE account_id=?", (account_id,)).fetchone()[0]
    for action in ("reset-password", "disable", "enable"):
        denied = api.client.post(f"/api/teacher/students/{account_id}/{action}", headers=other)
        assert denied.status_code == 404
    assert store.get(account_id) == before
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM login_sessions WHERE account_id=?", (account_id,)).fetchone()[0] == sessions_before
    assert api.client.get("/api/auth/me", headers=student_headers).status_code == 200
    assert api.client.post("/api/auth/login", json={"username": "audit-s001", "password": initial_password}).status_code == 200
    assert store.account_audit_log() == created_log

    assert api.client.get("/api/teacher/account-audit", headers=teacher).status_code == 404
    assert api.client.get("/api/teacher/account-audit", headers=student_headers).status_code == 404

    reset = api.client.post(f"/api/teacher/students/{account_id}/reset-password", headers=teacher)
    assert reset.status_code == 200
    assert api.client.get("/api/auth/me", headers=student_headers).status_code == 401
    reset_password = reset.json()["temporary_password"]
    assert store.account_audit_log()[0]["operation"] == "password_reset"
    assert reset_password not in str(store.account_audit_log())

    new_session, _ = login(api, "audit-s001", reset_password)
    disabled = api.client.post(f"/api/teacher/students/{account_id}/disable", headers=teacher).json()
    assert disabled == {"disabled": True, "changed": True}
    assert api.client.get("/api/auth/me", headers=new_session).status_code == 401
    log_after_disable = store.account_audit_log()
    assert log_after_disable[0]["operation"] == "account_disabled"
    repeated = api.client.post(f"/api/teacher/students/{account_id}/disable", headers=teacher).json()
    assert repeated == {"disabled": True, "changed": False}
    assert store.account_audit_log() == log_after_disable

    enabled = api.client.post(f"/api/teacher/students/{account_id}/enable", headers=teacher).json()
    assert enabled == {"disabled": False, "changed": True}
    assert [row["operation"] for row in reversed(store.account_audit_log())] == [
        "account_created", "password_reset", "account_disabled", "account_enabled",
    ]
    assert all(row["actor_id"] == teacher_user["id"] and row["entry"] == "teacher_api"
               for row in store.account_audit_log())


def test_roster_requires_local_accounts(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, env="development")
    monkeypatch.setenv("APP_AUTH_MODE", "demo")
    assert api.client.get("/api/teacher/classes", headers={"X-Owner-Id": "teacher-x"}).status_code == 409
    assert api.client.get("/api/assignments", headers=api.headers).json() == []

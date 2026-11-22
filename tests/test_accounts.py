import hashlib
import time

import pytest
from fastapi import HTTPException

from app import main
from app.accounts import AccountStore, SESSION_SECONDS
from test_cases_config import Api
from test_frontend_smoke import _page, _route_requests

PASSWORD = "test-only-password-42"


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, env="development")
    monkeypatch.setenv("APP_AUTH_MODE", "local")
    store = AccountStore(tmp_path / "accounts.db")
    monkeypatch.setattr(main, "ACCOUNTS", store)
    for username, role in (("alice", "student"), ("bob", "student"), ("teacher", "teacher")):
        store.create(username, PASSWORD, role, username)
    return api, store


def login(api, username="alice"):
    result = api.client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    return {"Authorization": "Bearer " + result.json()["token"]}, result.json()["user"]


def test_real_login_role_and_ownership_boundaries(accounts):
    api, store = accounts
    assert api.client.get("/api/auth/config").json() == {"mode": "local"}
    assert api.client.get("/api/cases", headers={"X-Owner-Id": "demo-student"}).status_code == 401
    assert api.client.get("/api/teacher/cases", headers={"X-Owner-Id": "teacher-demo"}).status_code == 401
    alice, user = login(api)
    bob, _ = login(api, "bob")
    teacher, _ = login(api, "teacher")
    assert user["role"] == "student" and user["id"].startswith("account-")
    assert set(user) == {"id", "username", "display_name", "role", "must_change_password"}
    assert api.client.get("/api/cases", headers=alice).status_code == 200
    assert api.client.get("/api/teacher/cases", headers=alice).status_code == 403
    assert api.client.get("/api/teacher/cases", headers=teacher).status_code == 200
    sid = api.client.post("/api/sessions?case_id=CASE-001", headers=alice).json()["session_id"]
    assert api.client.get(f"/api/sessions/{sid}", headers=bob).status_code == 404
    assert api.client.get(f"/api/sessions/{sid}", headers={**bob, "X-Owner-Id": user["id"]}).status_code == 404
    assert api.client.get(f"/api/sessions/{sid}", headers=alice).status_code == 200
    assert not api.client.get("/api/sessions", headers=bob).json()
    assert api.client.post("/api/auth/register", json={"role": "teacher"}).status_code == 404


def test_logout_expiry_reset_and_disable_revoke_access(accounts, monkeypatch):
    api, store = accounts
    headers, _ = login(api)
    assert api.client.post("/api/auth/logout", headers=headers).status_code == 200
    assert api.client.get("/api/auth/me", headers=headers).status_code == 401
    headers, _ = login(api)
    real_time = time.time
    with monkeypatch.context() as patch:
        patch.setattr("app.accounts.time.time", lambda: real_time() + SESSION_SECONDS + 1)
        assert api.client.get("/api/cases", headers=headers).status_code == 401
    headers, _ = login(api)
    store.reset_password("alice", "replacement-password-43")
    assert api.client.get("/api/cases", headers=headers).status_code == 401
    assert api.client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 401
    headers, _ = login(api, "bob")
    store.disable("bob")
    assert api.client.get("/api/auth/me", headers=headers).status_code == 401
    assert api.client.post("/api/auth/login", json={"username": "bob", "password": PASSWORD}).status_code == 401


def test_password_storage_validation_and_persistent_limits(accounts):
    api, store = accounts
    headers, _ = login(api)
    with store.connect() as db:
        row = db.execute("SELECT * FROM accounts WHERE username='alice'").fetchone()
        session = db.execute("SELECT token_hash FROM login_sessions").fetchone()
        assert len(row["salt"]) == 32 and len(row["password_hash"]) == 32
        assert session[0] == hashlib.sha256(headers["Authorization"][7:].encode()).hexdigest()
    assert PASSWORD.encode() not in store.path.read_bytes()
    with pytest.raises(ValueError):
        store.create("admin", "short", "teacher", "Admin")
    with pytest.raises(ValueError):
        store.create("ALICE", PASSWORD, "teacher", "Alice")
    for username in ("alice", "unknown"):
        for _ in range(5):
            assert api.client.post("/api/auth/login", json={"username": username, "password": "incorrect"}).status_code == 401
        # Reopening the database must not reset the throttle.
        with pytest.raises(HTTPException) as exc:
            AccountStore(store.path).login(username, PASSWORD, "another-client")
        assert exc.value.status_code == 429
    oversized = api.client.post("/api/auth/login", json={"username": "alice", "password": "x" * 129})
    assert oversized.status_code == 422


def test_invalid_bearer_never_falls_back_to_development_identity(accounts, monkeypatch):
    api, _ = accounts
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_AUTH_MODE", "demo")
    assert api.client.get("/api/cases", headers={"X-Owner-Id": "demo-student"}).status_code == 200
    assert api.client.get("/api/cases", headers={"X-Owner-Id": "demo-student", "Authorization": "Bearer invalid"}).status_code == 401
    monkeypatch.setenv("APP_AUTH_MODE", "local")
    assert api.client.get("/api/cases", headers={"X-Owner-Id": "demo-student"}).status_code == 401
    assert api.client.get("/api/teacher/cases", headers={"X-Owner-Id": "teacher-demo"}).status_code == 401
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_AUTH_MODE", "demo")
    assert api.client.get("/api/auth/config").json() == {"mode": "local"}
    assert api.client.get("/api/teacher/cases", headers={"X-Owner-Id": "teacher-demo"}).status_code == 401
    teacher, _ = login(api, "teacher")
    assert api.client.get("/api/teacher/cases", headers=teacher).status_code == 200
    student, _ = login(api)
    assert api.client.get("/api/cases", headers=student).status_code == 200


def test_management_command_uses_selected_data_and_revokes_sessions(tmp_path, monkeypatch):
    import runpy
    from test_cases_config import ROOT
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("getpass.getpass", lambda _: PASSWORD)
    monkeypatch.setattr("sys.argv", ["manage_accounts.py", "create", "instructor", "--role", "teacher"])
    runpy.run_path(str(ROOT / "scripts/manage_accounts.py"), run_name="__main__")
    store = AccountStore(tmp_path / "accounts.db")
    result = store.login("instructor", PASSWORD, "test")
    assert result["user"]["role"] == "teacher"
    monkeypatch.setattr("sys.argv", ["manage_accounts.py", "disable", "instructor"])
    runpy.run_path(str(ROOT / "scripts/manage_accounts.py"), run_name="__main__")
    with pytest.raises(HTTPException) as exc:
        store.authenticate("Bearer " + result["token"])
    assert exc.value.status_code == 401


def test_frontend_login_roles_logout_and_identity_cache(accounts, monkeypatch):
    api, store = accounts
    _route_requests(monkeypatch, api)
    page = _page(api)
    assert not page.exception
    assert not [b for b in page.button if b.label == "开始训练"]
    page.text_input(key="login_username").set_value("alice")
    page.text_input(key="login_password").set_value(PASSWORD)
    next(b for b in page.button if b.label == "登录工作台").click().run()
    assert not page.exception
    assert page.radio(key="workspace").options == ["学生训练"]
    page.button(key="start_CASE-001").click().run()
    sid = page.session_state["session_id"]
    page.text_area(key="case_notes_input").set_value("只属于 Alice 的未提交病历").run()
    token = page.session_state["account_login"]["token"]
    store.logout("Bearer " + token)
    page.run()
    assert page.text_input(key="login_username")
    assert not any(item.value == "只属于 Alice 的未提交病历" for item in page.text_area)
    page.text_input(key="login_username").set_value("alice")
    page.text_input(key="login_password").set_value(PASSWORD)
    next(b for b in page.button if b.label == "登录工作台").click().run()
    assert page.session_state["session_id"] == sid
    assert page.text_area(key="case_notes_input").value == "只属于 Alice 的未提交病历"
    page.button(key="logout").click().run()
    assert not page.exception
    page.text_input(key="login_username").set_value("teacher")
    page.text_input(key="login_password").set_value(PASSWORD)
    next(b for b in page.button if b.label == "登录工作台").click().run()
    assert not page.exception
    assert page.session_state["session_id"] is None
    page.radio(key="workspace").set_value("教师病例管理").run()
    assert not page.exception
    page.radio(key="teacher_task").set_value("病例编写与发布").run()
    assert any(b.label == "以此病例新建草稿" for b in page.button)

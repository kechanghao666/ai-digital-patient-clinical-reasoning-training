"""Local teaching accounts. Passwords and bearer tokens are never stored in plaintext."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr

ITERATIONS = 600_000
SESSION_SECONDS = 8 * 60 * 60


def auth_mode():
    # Development identities must never be accepted by a production process.
    return "demo" if (os.getenv("APP_ENV", "production").lower() == "development"
                      and os.getenv("APP_AUTH_MODE", "demo") == "demo") else "local"


def normalize_username(value):
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", value):
        raise ValueError("账号须为 3–64 位字母、数字、点、下划线或短横线")
    return value


def password_digest(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)


def check_password(password):
    if not 12 <= len(password) <= 128:
        raise ValueError("密码长度须为 12–128 个字符")


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class AccountStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('student','teacher')),
                    salt BLOB NOT NULL, password_hash BLOB NOT NULL, disabled INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS login_sessions (
                    token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    bucket TEXT PRIMARY KEY, attempts INTEGER NOT NULL, reset_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS account_management_audit (
                    occurred_at TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN (
                        'account_created', 'password_reset', 'account_disabled', 'account_enabled'
                    )),
                    target_account_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    entry TEXT NOT NULL CHECK(entry IN ('teacher_api', 'cli'))
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(accounts)")}
            if "must_change_password" not in columns:
                # Set for passwords issued by a teacher; the holder must replace it before using the API.
                db.execute("ALTER TABLE accounts ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def public(row):
        return {key: row[key] for key in ("id", "username", "display_name", "role")}

    def directory(self, account_ids) -> dict[str, dict]:
        """Public profile (no secrets) for the given account ids; unknown ids are omitted."""
        ids = list(dict.fromkeys(account_ids))
        if not ids:
            return {}
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM accounts WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
        return {row["id"]: self.public(row) for row in rows}

    @staticmethod
    def session_user(row):
        return {**AccountStore.public(row), "must_change_password": bool(row["must_change_password"])}

    def get(self, account_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not row:
            raise KeyError(account_id)
        return {**self.public(row), "disabled": bool(row["disabled"]),
                "must_change_password": bool(row["must_change_password"])}

    def find(self, username):
        with self.connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE username=?", (normalize_username(username),)).fetchone()
        return self.get(row["id"]) if row else None

    def _append_account_audit(self, db, operation, target_account_id, actor_id, entry):
        if operation not in {"account_created", "password_reset", "account_disabled", "account_enabled"}:
            raise ValueError("账号审计操作无效")
        if not actor_id or entry not in {"teacher_api", "cli"}:
            raise ValueError("账号审计操作者或入口无效")
        if entry == "cli" and actor_id != "local-ops":
            raise ValueError("CLI 审计操作者必须标记为 local-ops")
        occurred_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        db.execute("""INSERT INTO account_management_audit
            (occurred_at, operation, target_account_id, actor_id, entry) VALUES (?,?,?,?,?)""",
                   (occurred_at, operation, target_account_id, actor_id, entry))

    def account_audit_log(self, limit=100):
        return self.read_account_audit(self.path, limit)

    @staticmethod
    def read_account_audit(path, limit=100):
        if not 1 <= limit <= 500:
            raise ValueError("审计查询条数须为 1–500")
        database = Path(path)
        if not database.is_file():
            return []
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            exists = connection.execute("""SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='account_management_audit'""").fetchone()
            if not exists:
                return []
            rows = connection.execute("""SELECT occurred_at, operation, target_account_id, actor_id, entry
                FROM account_management_audit ORDER BY rowid DESC LIMIT ?""", (limit,)).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def issue_password(self, account_id, password, must_change=True, *, actor_id=None, entry=None):
        """Set a password and revoke sessions atomically; audit only explicit management entries."""
        check_password(password)
        salt = secrets.token_bytes(32)
        with self.connect() as db:
            updated = db.execute("UPDATE accounts SET salt=?,password_hash=?,must_change_password=? WHERE id=?",
                                 (salt, password_digest(password, salt), int(must_change), account_id)).rowcount
            if not updated:
                raise KeyError(account_id)
            db.execute("DELETE FROM login_sessions WHERE account_id=?", (account_id,))
            if actor_id is not None or entry is not None:
                self._append_account_audit(db, "password_reset", account_id, actor_id, entry)

    def set_disabled(self, account_id, disabled, *, actor_id=None, entry=None):
        desired = int(bool(disabled))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT disabled FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not row:
                raise KeyError(account_id)
            if row["disabled"] == desired:
                return False
            db.execute("UPDATE accounts SET disabled=? WHERE id=?", (desired, account_id))
            if desired:
                db.execute("DELETE FROM login_sessions WHERE account_id=?", (account_id,))
            if actor_id is not None or entry is not None:
                operation = "account_disabled" if desired else "account_enabled"
                self._append_account_audit(db, operation, account_id, actor_id, entry)
            return True

    def change_password(self, authorization, current, new):
        """Self-service change: verifies the current password, ends every login and returns a new one."""
        user = self.authenticate(authorization)
        check_password(new)
        if current == new:
            raise ValueError("新密码不能与当前密码相同")
        with self.connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE id=?", (user["id"],)).fetchone()
        if not hmac.compare_digest(password_digest(current, bytes(row["salt"])), bytes(row["password_hash"])):
            raise PermissionError("当前密码不正确")
        salt = secrets.token_bytes(32)
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.connect() as db:
            db.execute("UPDATE accounts SET salt=?,password_hash=?,must_change_password=0 WHERE id=?",
                       (salt, password_digest(new, salt), user["id"]))
            db.execute("DELETE FROM login_sessions WHERE account_id=?", (user["id"],))
            db.execute("INSERT INTO login_sessions VALUES (?,?,?)", (token_digest(token), user["id"], now + SESSION_SECONDS))
            row = db.execute("SELECT * FROM accounts WHERE id=?", (user["id"],)).fetchone()
        return {"token": token, "expires_at": now + SESSION_SECONDS, "user": self.session_user(row)}

    def create(self, username, password, role, display_name, must_change=False, *, actor_id=None, entry=None):
        username = normalize_username(username)
        check_password(password)
        if role not in {"student", "teacher"} or not 1 <= len(display_name.strip()) <= 80:
            raise ValueError("角色或显示姓名无效")
        salt = secrets.token_bytes(32)
        account_id = "account-" + str(uuid.uuid4())
        try:
            with self.connect() as db:
                db.execute("INSERT INTO accounts (id, username, display_name, role, salt, password_hash, disabled, "
                           "must_change_password) VALUES (?,?,?,?,?,?,0,?)",
                           (account_id, username, display_name.strip(), role, salt, password_digest(password, salt),
                            int(must_change)))
                if actor_id is not None or entry is not None:
                    self._append_account_audit(db, "account_created", account_id, actor_id, entry)
        except sqlite3.IntegrityError as exc:
            raise ValueError("该账号已存在") from exc
        return account_id

    def reset_password(self, username, password, *, actor_id=None, entry=None):
        check_password(password)
        salt = secrets.token_bytes(32)
        with self.connect() as db:
            row = db.execute("SELECT id FROM accounts WHERE username=?", (normalize_username(username),)).fetchone()
            if not row:
                raise ValueError("账号不存在")
            db.execute("UPDATE accounts SET salt=?,password_hash=? WHERE id=?", (salt, password_digest(password, salt), row["id"]))
            db.execute("DELETE FROM login_sessions WHERE account_id=?", (row["id"],))
            if actor_id is not None or entry is not None:
                self._append_account_audit(db, "password_reset", row["id"], actor_id, entry)

    def disable(self, username, *, actor_id=None, entry=None):
        with self.connect() as db:
            row = db.execute("SELECT id FROM accounts WHERE username=?", (normalize_username(username),)).fetchone()
            if not row:
                raise ValueError("账号不存在")
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT disabled FROM accounts WHERE id=?", (row["id"],)).fetchone()
            if current["disabled"]:
                return False
            db.execute("UPDATE accounts SET disabled=1 WHERE id=?", (row["id"],))
            db.execute("DELETE FROM login_sessions WHERE account_id=?", (row["id"],))
            if actor_id is not None or entry is not None:
                self._append_account_audit(db, "account_disabled", row["id"], actor_id, entry)
            return True

    def login(self, username, password, source):
        now = time.time()
        username = username.strip().lower()
        user_bucket = "user:" + token_digest(username)
        # Persist limits across process restarts, including attempts against nonexistent accounts.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE reset_at<=?", (now,))
            # The Streamlit server proxies every user's login from loopback, so a loopback source
            # bucket would let one person's failed guesses lock everyone out; only per-user limits apply.
            limits = [(user_bucket, 5, 300)]
            if source not in LOOPBACK_SOURCES:
                limits.append(("source:" + token_digest(source), 120, 60))
            for bucket, limit, window in limits:
                row = db.execute("SELECT attempts FROM login_attempts WHERE bucket=?", (bucket,)).fetchone()
                if row and row[0] >= limit:
                    raise HTTPException(429, "尝试次数过多，请稍后再试", headers={"Retry-After": str(window)})
            for bucket, _, window in limits:
                db.execute("INSERT INTO login_attempts VALUES (?,1,?) ON CONFLICT(bucket) DO UPDATE SET attempts=attempts+1", (bucket, now + window))
        with self.connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        salt = bytes(row["salt"]) if row else b"\0" * 32
        candidate = password_digest(password, salt)
        if not row or not hmac.compare_digest(candidate, bytes(row["password_hash"])) or row["disabled"]:
            raise HTTPException(401, "账号或密码不正确，或账号已停用")
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # A concurrent password reset or disable must not mint a session from stale credentials.
            fresh = db.execute("SELECT * FROM accounts WHERE id=?", (row["id"],)).fetchone()
            if not fresh or fresh["disabled"] or fresh["password_hash"] != row["password_hash"]:
                raise HTTPException(401, "账号状态已改变，请重新登录")
            db.execute("DELETE FROM login_sessions WHERE expires_at<=?", (now,))
            db.execute("DELETE FROM login_attempts WHERE bucket=?", (user_bucket,))
            db.execute("INSERT INTO login_sessions VALUES (?,?,?)", (token_digest(token), row["id"], now + SESSION_SECONDS))
        return {"token": token, "expires_at": now + SESSION_SECONDS, "user": self.session_user(fresh)}

    def authenticate(self, authorization):
        if not authorization or not authorization.startswith("Bearer ") or not 20 <= len(authorization) <= 200:
            raise HTTPException(401, "请登录后继续", headers={"WWW-Authenticate": "Bearer"})
        with self.connect() as db:
            row = db.execute("""SELECT a.* FROM accounts a JOIN login_sessions s ON a.id=s.account_id
                WHERE s.token_hash=? AND s.expires_at>? AND a.disabled=0""",
                (token_digest(authorization[7:]), time.time())).fetchone()
        if not row:
            raise HTTPException(401, "登录已失效，请重新登录", headers={"WWW-Authenticate": "Bearer"})
        return self.session_user(row)

    def logout(self, authorization):
        if authorization and authorization.startswith("Bearer "):
            with self.connect() as db:
                db.execute("DELETE FROM login_sessions WHERE token_hash=?", (token_digest(authorization[7:]),))


LOOPBACK_SOURCES = frozenset({"127.0.0.1", "::1", "localhost", "local", "testclient"})
PASSWORD_MAX_LENGTH = 128


class PasswordChange(BaseModel):
    current_password: SecretStr = Field(min_length=1)
    new_password: SecretStr = Field(min_length=1)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    # Length is checked in the handler: a schema max_length error would echo the plaintext password.
    password: SecretStr = Field(min_length=1)


def build_router(get_store):
    router = APIRouter(prefix="/api/auth")

    @router.get("/config")
    def config(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {"mode": auth_mode()}

    @router.post("/login")
    def login(body: LoginRequest, request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        password = body.password.get_secret_value()
        if len(password) > PASSWORD_MAX_LENGTH:
            raise HTTPException(422, "密码长度超出限制")
        return get_store().login(body.username, password, request.client.host if request.client else "local")

    @router.get("/me")
    def me(response: Response, authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        return get_store().authenticate(authorization)

    @router.post("/password")
    def change_password(body: PasswordChange, response: Response, authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        current, new = body.current_password.get_secret_value(), body.new_password.get_secret_value()
        if max(len(current), len(new)) > PASSWORD_MAX_LENGTH:
            raise HTTPException(422, "密码长度超出限制")
        try:
            return get_store().change_password(authorization, current, new)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/logout")
    def logout(response: Response, authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        get_store().logout(authorization)
        return {"logged_out": True}

    return router

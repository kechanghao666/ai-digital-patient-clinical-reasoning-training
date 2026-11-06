from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from .models import ActionRequest, ActionResponse, PublicEvent, SessionState


class ConflictError(ValueError):
    pass


class SessionStore:
    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        configured = db_path or os.getenv("AI_PATIENT_DB", "data/ai_patient.sqlite3")
        self.db_path = Path(configured)
        if self.db_path != Path(":memory:"):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    status TEXT NOT NULL,
                    round INTEGER NOT NULL,
                    score_status TEXT NOT NULL,
                    score INTEGER,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS request_replays (
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, request_id)
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, owner: str) -> SessionState:
        from .models import new_session
        state = new_session(owner)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (state.session_id, state.owner, state.status, state.round, state.score_status, state.score, state.updated_at.isoformat()),
            )
        return state

    def get(self, session_id: str) -> SessionState | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if row is None:
                return None
            facts = [item[0] for item in connection.execute("SELECT content FROM facts WHERE session_id = ? ORDER BY id", (session_id,))]
            events = [PublicEvent(type=item[0], message=item[1]) for item in connection.execute("SELECT type, message FROM events WHERE session_id = ? ORDER BY id", (session_id,))]
            return SessionState(
                session_id=row["session_id"], owner=row["owner"], status=row["status"], round=row["round"],
                facts=facts, events=events, score_status=row["score_status"], score=row["score"],
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def apply(self, session_id: str, req: ActionRequest) -> ActionResponse:
        digest = hashlib.sha256(req.model_dump_json().encode()).hexdigest()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(session_id)
            prior = connection.execute("SELECT digest, response_json FROM request_replays WHERE session_id = ? AND request_id = ?", (session_id, req.request_id)).fetchone()
            if prior is not None:
                connection.rollback()
                if prior["digest"] != digest:
                    raise ConflictError("request_id 已被不同请求占用")
                return ActionResponse.model_validate(json.loads(prior["response_json"]))
            if row["status"] == "sealed":
                connection.rollback()
                raise ConflictError("会话已封存，不能继续操作")
            state = self.get(session_id)
            assert state is not None
            state.round += 1
            if req.action == "ask":
                message = self._answer(state, req.text)
            elif req.action == "wait":
                message = "患者病情继续观察，当前未触发新的公开变化。"
                state.events.append(PublicEvent(type="wait", message=message))
            else:
                message = self._diagnose(state, req.text)
            now = self._now()
            connection.execute("UPDATE sessions SET status = ?, round = ?, score_status = ?, score = ?, updated_at = ? WHERE session_id = ?", (state.status, state.round, state.score_status, state.score, now, session_id))
            connection.execute("DELETE FROM facts WHERE session_id = ?", (session_id,))
            connection.executemany("INSERT INTO facts(session_id, content, created_at) VALUES (?, ?, ?)", [(session_id, fact, now) for fact in state.facts])
            connection.execute("DELETE FROM events WHERE session_id = ?", (session_id,))
            connection.executemany("INSERT INTO events(session_id, type, message, created_at) VALUES (?, ?, ?, ?)", [(session_id, event.type, event.message, now) for event in state.events])
            response = ActionResponse(session_id=state.session_id, status=state.status, round=state.round, message=message, score_status=state.score_status, score=state.score, error=None, events=state.events[-5:])
            try:
                connection.execute("INSERT INTO request_replays VALUES (?, ?, ?, ?)", (session_id, req.request_id, digest, response.model_dump_json()))
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ConflictError("request_id 已被并发请求占用") from exc
            connection.commit()
            return response

    @staticmethod
    def _answer(state: SessionState, text: str) -> str:
        normalized = text.strip().lower()
        if "胸痛" in normalized or "pain" in normalized:
            answer = "患者诉持续胸痛，伴出汗和恶心。"
        elif "过敏" in normalized or "allerg" in normalized:
            answer = "患者否认已知药物过敏。"
        else:
            answer = "患者暂未提供与该问题直接对应的新信息。"
        state.facts.append(answer)
        state.events.append(PublicEvent(type="answer", message=answer))
        return answer

    @staticmethod
    def _diagnose(state: SessionState, text: str) -> str:
        value = text.strip().lower()
        positive = value in {"stemi", "急性st段抬高型心肌梗死"}
        uncertain = any(token in value for token in ("可能", "考虑", "疑似", "不确定", "maybe", "possible"))
        composite = any(token in value for token in ("和", "并", "+", "/", "伴", "合并", "多病灶", "multiple"))
        negated = any(token in value for token in ("不是", "否认", "排除", "非", "not", "without"))
        if positive:
            state.score_status, state.score, state.status = "complete", 100, "sealed"
            message = "诊断与当前量表完全匹配，评分完成。"
        else:
            state.score_status, state.score = "needs_review", None
            message = "诊断包含复合、否定、不确定或未覆盖表述，已转人工复核。" if uncertain or composite or negated or value else "诊断内容不足以由量表解释，已转人工复核。"
        state.events.append(PublicEvent(type="diagnosis", message=message))
        return message


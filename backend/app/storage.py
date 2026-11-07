import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import RLock

from .models import SessionState


class ConcurrentUpdateError(RuntimeError):
    pass


class SessionRepository:
    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sessions "
                "(session_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, state_json TEXT NOT NULL)"
            )
            connection.commit()

    @staticmethod
    def hash_payload(payload: object) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def create(self, state: SessionState) -> None:
        with self.lock, closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO sessions(session_id, owner_id, state_json) VALUES (?, ?, ?)",
                (state.session_id, state.owner_id, state.model_dump_json()),
            )
            connection.commit()

    def get(self, session_id: str, owner_id: str) -> SessionState:
        with self.lock, closing(sqlite3.connect(self.database_path)) as connection, connection:
            row = connection.execute(
                "SELECT state_json FROM sessions WHERE session_id=? AND owner_id=?",
                (session_id, owner_id),
            ).fetchone()
        if not row:
            raise KeyError("session not found")
        return SessionState.model_validate_json(row[0])

    def list_for_owner(self, owner_id: str) -> list[SessionState]:
        with self.lock, closing(sqlite3.connect(self.database_path)) as connection, connection:
            rows = connection.execute(
                "SELECT state_json FROM sessions WHERE owner_id=? ORDER BY rowid DESC",
                (owner_id,),
            ).fetchall()
        return [SessionState.model_validate_json(row[0]) for row in rows]

    def save(self, state: SessionState, expected_version: int | None = None) -> None:
        with self.lock, closing(sqlite3.connect(self.database_path)) as connection, connection:
            if expected_version is None:
                expected_version = state.state_version - 1
            current = connection.execute(
                "SELECT state_json FROM sessions WHERE session_id=? AND owner_id=?",
                (state.session_id, state.owner_id),
            ).fetchone()
            if not current:
                raise KeyError("session not found")
            current_state = SessionState.model_validate_json(current[0])
            if current_state.state_version != expected_version:
                raise ConcurrentUpdateError("session changed; retry from the latest state")
            updated = connection.execute(
                "UPDATE sessions SET state_json=? WHERE session_id=? AND owner_id=? AND state_json=?",
                (
                    state.model_dump_json(),
                    state.session_id,
                    state.owner_id,
                    current[0],
                ),
            )
            if updated.rowcount != 1:
                raise ConcurrentUpdateError("session changed; retry from the latest state")
            connection.commit()

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Iterator
from uuid import uuid4

PATIENT = "patient"
FEEDBACK = "feedback"

OPEN = "open"
SUBMITTED = "submitted"
FINALIZED = "finalized"
CANCELLED = "cancelled"
EXPIRED = "expired"


class TurnError(Exception):
    """Raised for any rejected turn credential or transition. Messages are safe to show to a model."""

    def __init__(self, code: str, message: str, turn_id: str | None = None):
        super().__init__(message)
        self.code = code
        # Set only after the token secret has been verified, so audit rows cannot be forged.
        self.turn_id = turn_id


@dataclass(frozen=True)
class Turn:
    turn_id: str
    kind: str
    owner_id: str
    session_id: str
    case_id: str
    case_version: int
    request_id: str
    state_version: int
    question: str
    candidate_fact_ids: tuple[str, ...]
    status: str
    expires_at: float
    submission: Any
    created_at: float
    cancel_reason: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    secret_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    case_version INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    question TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    status TEXT NOT NULL,
    expires_at REAL NOT NULL,
    submission_json TEXT,
    cancel_reason TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, kind, created_at);
CREATE TABLE IF NOT EXISTS turn_calls (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    ok INTEGER NOT NULL,
    detail TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turn_calls_turn ON turn_calls(turn_id);
"""


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class TurnStore:
    """Registry of backend-issued, short-lived, single-purpose turn credentials.

    A turn binds owner, session, case, case version, request id and the backend-computed
    candidate set. The model only ever holds the opaque token; every identity claim is
    resolved here, never taken from tool arguments. Stored in a separate SQLite file so the
    protected session database is never altered.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _row_to_turn(row: sqlite3.Row) -> Turn:
        submission = json.loads(row["submission_json"]) if row["submission_json"] else None
        return Turn(
            turn_id=row["turn_id"],
            kind=row["kind"],
            owner_id=row["owner_id"],
            session_id=row["session_id"],
            case_id=row["case_id"],
            case_version=row["case_version"],
            request_id=row["request_id"],
            state_version=row["state_version"],
            question=row["question"],
            candidate_fact_ids=tuple(json.loads(row["candidates_json"])),
            status=row["status"],
            expires_at=row["expires_at"],
            submission=submission,
            created_at=row["created_at"],
            cancel_reason=row["cancel_reason"],
        )

    def issue(
        self,
        *,
        kind: str,
        owner_id: str,
        session_id: str,
        case_id: str,
        case_version: int,
        request_id: str,
        state_version: int,
        question: str = "",
        candidate_fact_ids: Iterable[str] = (),
        ttl_s: float = 120.0,
        now: float | None = None,
    ) -> tuple[Turn, str]:
        if kind not in {PATIENT, FEEDBACK}:
            raise ValueError("unknown turn kind")
        now = time.time() if now is None else now
        turn_id = uuid4().hex
        secret = secrets.token_urlsafe(24)
        with self.lock, self._connect() as connection:
            # Only one live turn per (session, kind): the previous one can no longer act.
            connection.execute(
                "UPDATE turns SET status=?, cancel_reason=? WHERE session_id=? AND kind=? AND status IN (?, ?)",
                (CANCELLED, "superseded", session_id, kind, OPEN, SUBMITTED),
            )
            connection.execute(
                "INSERT INTO turns(turn_id, secret_hash, kind, owner_id, session_id, case_id, case_version, "
                "request_id, state_version, question, candidates_json, status, expires_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    turn_id, _hash(secret), kind, owner_id, session_id, case_id, case_version,
                    request_id, state_version, question, json.dumps(list(candidate_fact_ids)),
                    OPEN, now + ttl_s, now,
                ),
            )
            row = connection.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
        return self._row_to_turn(row), f"{turn_id}.{secret}"

    def resolve(self, token: str, *, kind: str, allow: Iterable[str] = (OPEN,), now: float | None = None) -> Turn:
        """Return the live turn for a token or raise TurnError with a model-safe message."""
        now = time.time() if now is None else now
        if not isinstance(token, str) or "." not in token or len(token) > 200:
            raise TurnError("invalid_token", "turn token is invalid")
        turn_id, _, secret = token.partition(".")
        with self.lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
            if row is None or not hmac.compare_digest(row["secret_hash"], _hash(secret)):
                raise TurnError("invalid_token", "turn token is invalid")
            if row["kind"] != kind:
                raise TurnError("wrong_kind", "turn token is not valid for this tool", turn_id)
            status = row["status"]
            if status in {OPEN, SUBMITTED} and row["expires_at"] < now:
                connection.execute(
                    "UPDATE turns SET status=?, cancel_reason=? WHERE turn_id=? AND status IN (?, ?)",
                    (EXPIRED, "ttl", turn_id, OPEN, SUBMITTED),
                )
                status = EXPIRED
            if status not in set(allow):
                raise TurnError(f"turn_{status}", f"turn is {status}", turn_id)
            if status == EXPIRED:
                raise TurnError("turn_expired", "turn is expired", turn_id)
            refreshed = connection.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
        return self._row_to_turn(refreshed)

    def submit(self, turn_id: str, submission: Any) -> bool:
        """Atomically move open -> submitted. Returns False if the turn was no longer open."""
        with self.lock, self._connect() as connection:
            updated = connection.execute(
                "UPDATE turns SET status=?, submission_json=? WHERE turn_id=? AND status=? AND expires_at>=?",
                (SUBMITTED, json.dumps(submission, ensure_ascii=False), turn_id, OPEN, time.time()),
            )
            return updated.rowcount == 1

    def finalize(self, turn_id: str) -> bool:
        with self.lock, self._connect() as connection:
            updated = connection.execute(
                "UPDATE turns SET status=? WHERE turn_id=? AND status=?", (FINALIZED, turn_id, SUBMITTED)
            )
            return updated.rowcount == 1

    def cancel(self, turn_id: str, reason: str) -> None:
        with self.lock, self._connect() as connection:
            connection.execute(
                "UPDATE turns SET status=?, cancel_reason=? WHERE turn_id=? AND status IN (?, ?)",
                (CANCELLED, reason, turn_id, OPEN, SUBMITTED),
            )

    def get(self, turn_id: str) -> Turn:
        with self.lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
        if row is None:
            raise KeyError(turn_id)
        return self._row_to_turn(row)

    def record_call(self, turn_id: str, tool: str, ok: bool, detail: str = "") -> None:
        with self.lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO turn_calls(turn_id, tool, ok, detail, at) VALUES (?,?,?,?,?)",
                (turn_id, tool, 1 if ok else 0, detail[:200], time.time()),
            )

    def calls(self, turn_id: str) -> list[dict[str, Any]]:
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT tool, ok, detail, at FROM turn_calls WHERE turn_id=? ORDER BY call_id", (turn_id,)
            ).fetchall()
        return [{"tool": r["tool"], "ok": bool(r["ok"]), "detail": r["detail"], "at": r["at"]} for r in rows]

"""Append-only human scoring decisions; sealed sessions remain the original evidence."""
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from ..storage import ConcurrentUpdateError, SessionRepository


class ScoreReviewStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS score_reviews (
                session_id TEXT NOT NULL, owner_id TEXT NOT NULL, revision INTEGER NOT NULL,
                teacher_id TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
                reason TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at REAL NOT NULL,
                PRIMARY KEY(session_id, owner_id, revision), UNIQUE(session_id, owner_id, request_id)
            )""")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def list(self, session_id: str, owner_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM score_reviews WHERE session_id=? AND owner_id=? ORDER BY revision",
                (session_id, owner_id),
            ).fetchall()
        return [{"revision": row["revision"], "teacher_id": row["teacher_id"], "reason": row["reason"],
                 "snapshot": json.loads(row["snapshot_json"]), "created_at": row["created_at"]} for row in rows]

    def append(self, session_id: str, owner_id: str, teacher_id: str, request_id: str,
               expected_revision: int, reason: str, snapshot: dict[str, Any]) -> int:
        payload_hash = SessionRepository.hash_payload({"teacher_id": teacher_id, "reason": reason,
                                                       "expected_revision": expected_revision, "snapshot": snapshot})
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                previous = connection.execute(
                    "SELECT revision, payload_hash FROM score_reviews WHERE session_id=? AND owner_id=? AND request_id=?",
                    (session_id, owner_id, request_id),
                ).fetchone()
                if previous:
                    if previous["payload_hash"] != payload_hash:
                        raise ConcurrentUpdateError("请求编号已用于其他评分裁定，请重新载入后提交")
                    connection.commit()
                    return previous["revision"]
                current = connection.execute(
                    "SELECT COALESCE(MAX(revision),0) FROM score_reviews WHERE session_id=? AND owner_id=?",
                    (session_id, owner_id),
                ).fetchone()[0]
                if current != expected_revision:
                    raise ConcurrentUpdateError("评分裁定已更新，请重新载入并核对后提交")
                revision = current + 1
                connection.execute("INSERT INTO score_reviews "
                    "(session_id, owner_id, revision, teacher_id, request_id, payload_hash, reason, snapshot_json, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (
                    session_id, owner_id, revision, teacher_id, request_id, payload_hash, reason,
                    json.dumps(snapshot, ensure_ascii=False, allow_nan=False), time.time(),
                ))
                connection.commit()
                return revision
            except Exception:
                connection.rollback()
                raise

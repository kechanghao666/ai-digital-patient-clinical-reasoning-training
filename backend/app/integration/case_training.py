"""Per-version training windows for published cases.

Stopping a version blocks NEW trainings only. It never deletes or rewrites the case file,
never changes the medical review fields, and never touches sealed scores or existing
trainings. Records are append-once per version and idempotent across retries.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from ..storage import ConcurrentUpdateError, SessionRepository


class CaseTrainingStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS case_training_stops (
                case_id TEXT NOT NULL, case_version INTEGER NOT NULL,
                teacher_id TEXT NOT NULL, reason TEXT NOT NULL,
                request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(case_id, case_version),
                UNIQUE(teacher_id, request_id)
            )""")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        return {"case_id": row["case_id"], "case_version": row["case_version"],
                "teacher_id": row["teacher_id"], "reason": row["reason"],
                "request_id": row["request_id"], "created_at": row["created_at"]}

    def get(self, case_id: str, case_version: int) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM case_training_stops WHERE case_id=? AND case_version=?",
                (case_id, case_version),
            ).fetchone()
        return self._record(row) if row else None

    def is_stopped(self, case_id: str, case_version: int) -> bool:
        return self.get(case_id, case_version) is not None

    def all(self) -> dict[tuple[str, int], dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM case_training_stops").fetchall()
        return {(row["case_id"], row["case_version"]): self._record(row) for row in rows}

    def stop(self, case_id: str, case_version: int, teacher_id: str, reason: str,
             request_id: str) -> dict[str, Any]:
        """Stop a version once. A repeat, whether the same request or a new one, returns the
        existing record and writes nothing; the same request id cannot be reused for another
        version."""
        request_hash = SessionRepository.hash_payload({
            "case_id": case_id, "case_version": case_version,
            "teacher_id": teacher_id, "reason": reason,
        })
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM case_training_stops WHERE case_id=? AND case_version=?",
                    (case_id, case_version),
                ).fetchone()
                if existing:
                    if existing["request_id"] == request_id and existing["request_hash"] != request_hash:
                        raise ConcurrentUpdateError("请求编号已用于不同的停止内容，请刷新后重试")
                    connection.commit()
                    return {**self._record(existing), "already_stopped": True}
                reused = connection.execute(
                    "SELECT request_hash FROM case_training_stops WHERE teacher_id=? AND request_id=?",
                    (teacher_id, request_id),
                ).fetchone()
                if reused:
                    raise ConcurrentUpdateError("请求编号已用于其他停止操作，请刷新后重试")
                connection.execute(
                    "INSERT INTO case_training_stops VALUES (?,?,?,?,?,?,?)",
                    (case_id, case_version, teacher_id, reason, request_id, request_hash, time.time()),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ConcurrentUpdateError("请求编号已用于其他停止操作，请刷新后重试") from exc
            except BaseException:
                connection.rollback()
                raise
        record = self.get(case_id, case_version)
        assert record is not None
        return {**record, "already_stopped": False}

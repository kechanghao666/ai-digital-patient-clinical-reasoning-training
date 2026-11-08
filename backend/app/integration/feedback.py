from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

from ..models import CaseDefinition, SessionState
from ..scoring import student_score_snapshot
from ..storage import ConcurrentUpdateError, SessionRepository
from . import turns as turn_states
from .config import IntegrationSettings
from .knowledge import KnowledgeStore
from .nexent_client import NexentClient, NexentError
from .tools import FEEDBACK_STAGES, validate_feedback_items
from .turns import TurnStore


class FeedbackUnavailable(RuntimeError):
    """Feedback could not be produced; nothing was stored and the student may retry."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    source TEXT NOT NULL,
    items_json TEXT NOT NULL,
    basis_status TEXT NOT NULL,
    trace_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback_reviews (
    session_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    teacher_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    reason TEXT NOT NULL,
    items_json TEXT NOT NULL,
    basis_status TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(session_id, owner_id, revision),
    UNIQUE(session_id, owner_id, request_id)
);
"""


class FeedbackStore:
    """Feedback lives outside the sealed session record, so sealing stays write-protected."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
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

    def get(self, session_id: str, owner_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM feedback WHERE session_id=? AND owner_id=?", (session_id, owner_id)
            ).fetchone()
        if row is None:
            return None
        return {
            "source": row["source"],
            "items": json.loads(row["items_json"]),
            "basis_status": row["basis_status"],
            "created_at": row["created_at"],
        }

    def trace(self, session_id: str, owner_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT trace_json FROM feedback WHERE session_id=? AND owner_id=?", (session_id, owner_id)
            ).fetchone()
        return json.loads(row["trace_json"]) if row else None

    def save_once(
        self, session_id: str, owner_id: str, source: str, items: list[dict[str, Any]], basis_status: str, trace: dict[str, Any]
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO feedback(session_id, owner_id, source, items_json, basis_status, trace_json, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    session_id, owner_id, source, json.dumps(items, ensure_ascii=False),
                    basis_status, json.dumps(trace, ensure_ascii=False), time.time(),
                ),
            )
            return cursor.rowcount == 1

    def reviews(self, session_id: str, owner_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT revision, teacher_id, reason, items_json, basis_status, created_at "
                "FROM feedback_reviews WHERE session_id=? AND owner_id=? ORDER BY revision",
                (session_id, owner_id),
            ).fetchall()
        return [{"revision": row["revision"], "teacher_id": row["teacher_id"], "reason": row["reason"],
                 "items": json.loads(row["items_json"]), "basis_status": row["basis_status"],
                 "created_at": row["created_at"]} for row in rows]

    def effective(self, session_id: str, owner_id: str) -> dict[str, Any] | None:
        reviews = self.reviews(session_id, owner_id)
        if not reviews:
            return self.get(session_id, owner_id)
        latest = reviews[-1]
        return {"source": "teacher_review", "items": latest["items"],
                "basis_status": latest["basis_status"], "created_at": latest["created_at"],
                "review_revision": latest["revision"]}

    def append_review(
        self, session_id: str, owner_id: str, teacher_id: str, request_id: str,
        expected_revision: int, reason: str, items: list[dict[str, Any]], basis_status: str,
    ) -> int:
        payload_hash = SessionRepository.hash_payload({
            "teacher_id": teacher_id, "expected_revision": expected_revision,
            "reason": reason, "items": items,
        })
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                previous = connection.execute(
                    "SELECT revision, payload_hash FROM feedback_reviews "
                    "WHERE session_id=? AND owner_id=? AND request_id=?",
                    (session_id, owner_id, request_id),
                ).fetchone()
                if previous:
                    if previous["payload_hash"] != payload_hash:
                        raise ConcurrentUpdateError("请求编号已用于其他复核内容，请重新载入后提交")
                    connection.commit()
                    return previous["revision"]
                current = connection.execute(
                    "SELECT COALESCE(MAX(revision), 0) FROM feedback_reviews WHERE session_id=? AND owner_id=?",
                    (session_id, owner_id),
                ).fetchone()[0]
                if current != expected_revision:
                    raise ConcurrentUpdateError("反馈已被其他复核更新，请重新载入后提交")
                revision = current + 1
                connection.execute(
                    "INSERT INTO feedback_reviews VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (session_id, owner_id, revision, teacher_id, request_id, payload_hash, reason,
                     json.dumps(items, ensure_ascii=False), basis_status, time.time()),
                )
                connection.commit()
                return revision
            except Exception:
                connection.rollback()
                raise


class FeedbackService:
    def __init__(
        self,
        settings: IntegrationSettings,
        turns: TurnStore,
        store: FeedbackStore,
        knowledge: KnowledgeStore,
        client: NexentClient | None = None,
    ):
        self.settings = settings
        self.turns = turns
        self.store = store
        self.knowledge = knowledge
        self._client = client
        self._locks: dict[str, RLock] = defaultdict(RLock)

    def client(self) -> NexentClient:
        if self._client is None:
            self._client = NexentClient(self.settings)
        return self._client

    def get_or_generate(self, state: SessionState, case: CaseDefinition, request_id: str) -> dict[str, Any]:
        if not state.sealed or state.score_snapshot is None:
            raise FeedbackUnavailable("not_sealed", "feedback is only available after sealing")
        with self._locks[state.session_id]:
            existing = self.store.effective(state.session_id, state.owner_id)
            if existing:
                return existing
            if self.settings.uses_nexent:
                items, source, trace = self._from_nexent(state, case, request_id)
            else:
                items, source, trace = self._from_local_rules(state, case)
            self.store.save_once(
                state.session_id, state.owner_id, source, items, self.knowledge.status, trace
            )
            stored = self.store.effective(state.session_id, state.owner_id)
        assert stored is not None
        return stored

    # ------------------------------------------------------------------ real chain
    def _from_nexent(self, state: SessionState, case: CaseDefinition, request_id: str):
        turn, token = self.turns.issue(
            kind=turn_states.FEEDBACK,
            owner_id=state.owner_id,
            session_id=state.session_id,
            case_id=state.case_id,
            case_version=state.case_version,
            request_id=request_id,
            state_version=state.state_version,
            ttl_s=max(self.settings.turn_ttl_s, self.settings.total_timeout_s + 30),
        )
        try:
            run = self.client().run(
                agent_name=self.settings.feedback_agent_name,
                query=(
                    "【系统回合】训练已封存。请依次调用 get_sealed_review、search_teaching_basis，"
                    "再调用 submit_feedback，按病史采集、检查选择、鉴别诊断、诊断、治疗各提交一条评语，"
                    "每条必须有非空改进建议和本环节basis_ids；不得公布参考诊断或编造事件。"
                    f"turn_token 为 {token}。"
                ),
                metadata={"turn_token": token, "role": "feedback"},
                audit_metadata={"turn_id": turn.turn_id, "request_id": request_id},
                idempotency_key=turn.turn_id,
            )
        except NexentError as exc:
            self.turns.cancel(turn.turn_id, "run_failed")
            raise FeedbackUnavailable(exc.kind, "反馈服务暂不可用，请稍后重试") from exc
        current = self.turns.get(turn.turn_id)
        calls = self.turns.calls(turn.turn_id)
        trace = {
            "real_platform": True,
            "turn_id": turn.turn_id,
            "conversation_id": run.conversation_id,
            "event_types": run.event_types,
            "first_event_ms": run.first_event_ms,
            "total_ms": run.total_ms,
            "tool_calls": [{"tool": c["tool"], "ok": c["ok"]} for c in calls],
        }
        if current.status != turn_states.SUBMITTED:
            self.turns.cancel(turn.turn_id, "no_feedback")
            raise FeedbackUnavailable("no_feedback", "反馈未生成，请稍后重试")
        checked = validate_feedback_items((current.submission or {}).get("items"), state, case, self.knowledge)
        if isinstance(checked, str) or not self.turns.finalize(turn.turn_id):
            self.turns.cancel(turn.turn_id, "invalid_feedback")
            raise FeedbackUnavailable("invalid_feedback", "反馈未通过校验，请稍后重试")
        return checked, "nexent", trace

    # ------------------------------------------------------------ deterministic path
    def _from_local_rules(self, state: SessionState, case: CaseDefinition):
        snapshot = state.score_snapshot
        assert snapshot is not None
        snapshot = student_score_snapshot(snapshot, case)
        valid_events = {e.event_id for e in state.events[: state.seal_seq or 0]}
        rubric = {r.item_id: r for r in case.rubric}
        items: list[dict[str, Any]] = []
        for stage in FEEDBACK_STAGES:
            stage_items = [i for i in snapshot.items if i.stage == stage]
            got = sum(i.score for i in stage_items)
            total = sum(i.max_score for i in stage_items)
            misses = [i for i in stage_items if not i.hit]
            lines = [f"本环节得分 {got:g}/{total:g}。"]
            for entry in stage_items:
                desc = ("诊断结论" if stage == "诊断" else rubric[entry.item_id].description) if entry.item_id in rubric else stage
                lines.append(f"{'已完成' if entry.hit else '未完成'}：{desc}（{entry.reason}）")
            query = " ".join(rubric[i.item_id].description for i in misses if i.item_id in rubric)
            basis = self.knowledge.search(f"{query} {stage} 反馈 改进建议", case.case_id, stage, limit=1)
            improvement = (
                "；".join(i.improvement or "请复核本环节缺失的操作与依据。" for i in misses)
                if misses
                else "保持当前做法，并在回放中复核时间点。"
            )
            evidence = [e for i in stage_items for e in i.evidence_event_ids if e in valid_events]
            items.append(
                {
                    "stage": stage,
                    "comment": " ".join(lines)[:600],
                    "improvement": improvement[:300],
                    "evidence_event_ids": list(dict.fromkeys(evidence)),
                    "basis_ids": [e.basis_id for e in basis],
                }
            )
        checked = validate_feedback_items(items, state, case, self.knowledge)
        if isinstance(checked, str):
            raise FeedbackUnavailable("invalid_feedback", "本地反馈未通过校验，请联系教师复核")
        return checked, "local_rules", {"real_platform": False}

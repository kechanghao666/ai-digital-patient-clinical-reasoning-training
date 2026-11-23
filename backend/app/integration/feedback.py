from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

from ..adapters import diagnosis_leak_terms
from ..models import ActionKind, CaseDefinition, SessionState
from ..scoring import student_score_snapshot
from ..storage import ConcurrentUpdateError, SessionRepository
from . import turns as turn_states
from .config import IntegrationSettings
from .knowledge import KnowledgeStore
from .nexent_client import NexentClient, NexentError
from .tools import FEEDBACK_STAGES, validate_feedback_items
from .turns import TurnStore


logger = logging.getLogger(__name__)



def _initialise(connect, schema: str, attempts: int = 50) -> None:
    import time as _time

    for attempt in range(attempts):
        try:
            with connect() as connection:
                if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                    connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(schema)
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == attempts - 1:
                raise
            _time.sleep(0.05)


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
CREATE TABLE IF NOT EXISTS feedback_basis (
    session_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    PRIMARY KEY(session_id, owner_id, revision)
);
"""


class FeedbackStore:
    """Feedback lives outside the sealed session record, so sealing stays write-protected."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # WAL is persistent in the file; switch once here. Switching needs an exclusive lock and does not
        # wait on the busy handler, so doing it on every connection fails under concurrent requests.
        _initialise(self._connect, _SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _load_basis(value: str | None) -> Any:
        if value is None:
            return None
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {"schema_version": 0}
        except (ValueError, TypeError):
            # Corruption must not be mistaken for a legacy record without a snapshot.
            return {"schema_version": 0}

    def get(self, session_id: str, owner_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT f.*, b.snapshot_json FROM feedback f LEFT JOIN feedback_basis b "
                "ON f.session_id=b.session_id AND f.owner_id=b.owner_id AND b.revision=0 "
                "WHERE f.session_id=? AND f.owner_id=?", (session_id, owner_id)
            ).fetchone()
        if row is None:
            return None
        return {
            "source": row["source"],
            "items": json.loads(row["items_json"]),
            "basis_status": row["basis_status"],
            "created_at": row["created_at"],
            "basis_snapshot": self._load_basis(row["snapshot_json"]),
        }

    def trace(self, session_id: str, owner_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT trace_json FROM feedback WHERE session_id=? AND owner_id=?", (session_id, owner_id)
            ).fetchone()
        return json.loads(row["trace_json"]) if row else None

    def save_once(
        self, session_id: str, owner_id: str, source: str, items: list[dict[str, Any]], basis_status: str, trace: dict[str, Any],
        basis_snapshot: dict | None = None,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO feedback(session_id, owner_id, source, items_json, basis_status, trace_json, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (session_id, owner_id, source, json.dumps(items, ensure_ascii=False),
                     basis_status, json.dumps(trace, ensure_ascii=False), time.time()),
                )
                inserted = cursor.rowcount == 1
                if inserted:
                    self._save_basis(connection, session_id, owner_id, 0, basis_snapshot)
                connection.commit()
                return inserted
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _save_basis(connection: sqlite3.Connection, session_id: str, owner_id: str, revision: int, snapshot: dict | None):
        if snapshot is not None:
            connection.execute("INSERT INTO feedback_basis VALUES (?,?,?,?)",
                               (session_id, owner_id, revision, json.dumps(snapshot, ensure_ascii=False)))

    def reviews(self, session_id: str, owner_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT r.*, b.snapshot_json FROM feedback_reviews r LEFT JOIN feedback_basis b "
                "ON r.session_id=b.session_id AND r.owner_id=b.owner_id AND r.revision=b.revision "
                "WHERE r.session_id=? AND r.owner_id=? ORDER BY r.revision",
                (session_id, owner_id),
            ).fetchall()
        return [{"revision": row["revision"], "teacher_id": row["teacher_id"], "reason": row["reason"],
                 "items": json.loads(row["items_json"]), "basis_status": row["basis_status"],
                 "created_at": row["created_at"],
                 "basis_snapshot": self._load_basis(row["snapshot_json"])} for row in rows]

    def effective(self, session_id: str, owner_id: str) -> dict[str, Any] | None:
        reviews = self.reviews(session_id, owner_id)
        if not reviews:
            return self.get(session_id, owner_id)
        latest = reviews[-1]
        return {"source": "teacher_review", "items": latest["items"],
                "basis_status": latest["basis_status"], "created_at": latest["created_at"],
                "review_revision": latest["revision"], "basis_snapshot": latest["basis_snapshot"]}

    def append_review(
        self, session_id: str, owner_id: str, teacher_id: str, request_id: str,
        expected_revision: int, reason: str, items: list[dict[str, Any]], basis_status: str,
        basis_snapshot: dict | None = None,
        request_hash: str | None = None,
    ) -> int:
        payload_hash = request_hash or SessionRepository.hash_payload({
            "teacher_id": teacher_id, "expected_revision": expected_revision,
            "reason": reason, "items": items,
        })
        # Retries bind to the submitted edit, even if the knowledge library has since changed.
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
                self._save_basis(connection, session_id, owner_id, revision, basis_snapshot)
                connection.commit()
                return revision
            except Exception:
                connection.rollback()
                raise

    def replay_revision(self, session_id: str, owner_id: str, request_id: str, request_hash: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision, payload_hash FROM feedback_reviews WHERE session_id=? AND owner_id=? AND request_id=?",
                (session_id, owner_id, request_id),
            ).fetchone()
        if row is None:
            return None
        if row["payload_hash"] != request_hash:
            raise ConcurrentUpdateError("请求编号已用于其他复核内容，请重新载入后提交")
        return row["revision"]


_GAP_LABELS = {
    "病史采集": "未找到采集记录",
    "检查选择": "未找到开立记录",
    "鉴别诊断": "未提交鉴别证据",
    "诊断": "未提交诊断",
    "治疗": "未执行处置",
}

# Neutral per-stage action names used when the rubric has no mapping for an item, or when the
# author's description is not safe to forward (diagnosis/marker content).
_NEUTRAL_ACTIONS = {
    "病史采集": "病史采集",
    "检查选择": "检查选择",
    "鉴别诊断": "鉴别诊断",
    "诊断": "提交诊断",
    "治疗": "治疗措施",
}

_INTERNAL_MARKERS = ("sentinel", "traceback", "api_key", "turn_token", "secret")

# Standard action-name prefixes per evidence type. Only these controlled forms may be forwarded
# as a teaching action; any other authored text (free teacher notes) falls back to the neutral name.
_ACTION_PREFIXES = {
    "ask_event": ("询问",),
    "exam_order": ("开立", "优先开立", "测量"),
    "ddx_evidence": ("提供",),
    "diagnosis": ("提交",),
    "treatment_execution": ("确认",),
}
_ACTION_MAX_LEN = 40


def _controlled_action(rubric_item, case: CaseDefinition | None) -> bool:
    """True only for standard authored action names: controlled verb, bounded length, and no
    diagnosis or internal-marker content. Free teacher text is never forwarded to the model."""
    description = (rubric_item.description or "").strip()
    if not description or len(description) > _ACTION_MAX_LEN:
        return False
    if not description.startswith(_ACTION_PREFIXES.get(rubric_item.evidence_type, ())):
        return False
    lowered = "".join(description.casefold().split())
    if any(marker in lowered for marker in _INTERNAL_MARKERS):
        return False
    if case is not None and any(term in lowered for term in diagnosis_leak_terms(case)):
        return False
    return True


def teaching_gap(item, rubric_item, case: CaseDefinition | None = None) -> str:
    """A de-identified teaching-action gap a sealed score can prove, safe to show the model.

    Uses the authored teaching-action description only when it is a controlled action name;
    otherwise falls back to the stage's neutral name. The diagnosis stage is always neutral. A
    missing record is never phrased as the patient lacking a symptom.
    """
    action = _NEUTRAL_ACTIONS.get(item.stage, item.stage)
    if rubric_item is not None and item.stage != "诊断" and _controlled_action(rubric_item, case):
        action = rubric_item.description
    if "复核" in (item.reason or ""):
        status = "待教师判断"
    elif item.score > 0:
        status = "部分完成"
    else:
        status = _GAP_LABELS.get(item.stage, "未找到记录")
    return f"{action}：{status}"


def _safe_evidence_text(text: str, case: CaseDefinition) -> bool:
    lowered = "".join(text.casefold().split())
    if any(marker in lowered for marker in _INTERNAL_MARKERS):
        return False
    return not any(term in lowered for term in diagnosis_leak_terms(case))


def treatment_evidence(state: SessionState, case: CaseDefinition) -> dict[str, Any]:
    """Sealed records directly relevant to treatment timing, safe to show the model.

    Only exam results that were actually returned (ready before sealing) are listed, each with the
    simulated time it became visible; executed treatments are listed with their execution time.
    Records are omitted when their text is not safe to forward. This proves visibility, not that
    the student read a result, and never asserts causation for any clinical change.
    """
    exam_by_id = {exam.exam_id: exam for exam in case.exams}
    visible_at: dict[str, int] = {}
    for event in state.events:
        for exam_id in event.result.get("ready_exam_ids", []):
            if exam_id not in visible_at:
                visible_at[exam_id] = event.sim_time
    returned: list[dict[str, Any]] = []
    for exam_id, sim_time in sorted(visible_at.items(), key=lambda kv: (kv[1], kv[0])):
        exam = exam_by_id.get(exam_id)
        if exam is None or exam.result is None or not _safe_evidence_text(exam.standard_name + exam.result, case):
            continue
        returned.append({"检查": exam.standard_name, "结果": exam.result, "回报时间分": sim_time})
    treatment_names = {treatment.treatment_id: treatment.name for treatment in case.treatments}
    executed_at: dict[str, int] = {}
    for event in state.events:
        if event.action == ActionKind.EXECUTE_TREATMENT and event.executed:
            treatment_id = str(event.payload.get("treatment_id", ""))
            if treatment_id not in executed_at:
                executed_at[treatment_id] = event.sim_time
    executed: list[dict[str, Any]] = []
    for treatment_id, sim_time in sorted(executed_at.items(), key=lambda kv: (kv[1], kv[0])):
        name = treatment_names.get(treatment_id, treatment_id)
        if _safe_evidence_text(name, case):
            executed.append({"处置": name, "执行时间分": sim_time})
    return {"封存时间分": state.seal_sim_time, "已回报检查": returned, "已执行处置": executed}


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
            elif self.settings.mode == "simulator":
                items, source, trace = self._from_local_rules(state, case)
            else:
                raise FeedbackUnavailable("unavailable", "反馈运行模式无效，请检查服务端 NEXENT_MODE 配置")
            self.store.save_once(
                state.session_id, state.owner_id, source, items, self.knowledge.status, trace,
                self.knowledge.snapshot(items, case.case_id, case.version),
            )
            stored = self.store.effective(state.session_id, state.owner_id)
        assert stored is not None
        return stored

    # ------------------------------------------------------------------ real chain
    def _compact_query(self, state: SessionState, case: CaseDefinition) -> str:
        snapshot = state.score_snapshot
        assert snapshot is not None
        snapshot = student_score_snapshot(snapshot, case)
        rubric = {item.item_id: item for item in case.rubric}
        stage_summary: dict[str, list[dict[str, Any]]] = {}
        for stage in FEEDBACK_STAGES:
            entries: list[dict[str, Any]] = []
            for item in snapshot.items:
                if item.stage != stage:
                    continue
                entry: dict[str, Any] = {"得分": item.score, "满分": item.max_score}
                if item.score < item.max_score:
                    entry["缺口"] = teaching_gap(item, rubric.get(item.item_id), case)
                entries.append(entry)
            stage_summary[stage] = entries
        return (
            "训练已封存。以下各环节依次列出分项得分与满分；未满分项附注一条封存评分能够证明的教学动作缺口，"
            "格式为「教学动作：状态」。缺口只说明记录缺失、部分完成或待教师判断，"
            "不得把「未找到记录」理解为患者没有该症状。仅作为反馈数据。"
            "另附封存时已确定的检查回报与处置执行记录（含模拟时间）：只有「已回报检查」列出的才是已回报、"
            "学生可见的结果，未列出的检查未回报，不得引用其结果；处置执行以「已执行处置」为准，"
            "不得把未见执行写成执行过，也不得写成执行得太晚；评语只陈述已回报与未执行的事实，"
            "不得断言病情变化由未执行处置导致。"
            + json.dumps({"环节": stage_summary, "封存记录": treatment_evidence(state, case)},
                         ensure_ascii=False, separators=(",", ":"))
            + "请根据封存训练与下游可信工具提供的教学依据，每个环节写一句评语和一句改进建议，"
            "评语须准确指出该环节的实际缺口。一次调用 submit_feedback，items 每项只含 stage、comment、improvement，"
            "设置 use_stage_basis=True。只评价得分与完成程度，不凭分数判断诊疗合理性。"
            "不得推断或披露隐藏诊断，不得改分或编造观察结果。"
        )

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
        query = self._compact_query(state, case) if self.settings.feedback_protocol == "compact" else (
            "训练已封存。一次调用 get_sealed_review 读取评分、事件和 stage_basis 中的五环节依据，"
            "调用 search_teaching_basis 检索，"
            "再调用 submit_feedback，按病史采集、检查选择、鉴别诊断、诊断、治疗各提交一条评语，"
            "每条必须有非空改进建议，且引用的教学依据 stages 必须包含该环节；不得公布参考诊断或编造事件。"
        )
        try:
            run = self.client().run(
                agent_name=self.settings.feedback_agent_name,
                query=f"【系统回合】{query} turn_token 为 {token}。",
                metadata={"turn_token": token, "role": "feedback"},
                audit_metadata={"turn_id": turn.turn_id, "request_id": request_id},
                idempotency_key=turn.turn_id,
                trusted_completion=(
                    lambda: self.turns.get(turn.turn_id).status == turn_states.SUBMITTED
                ) if self.settings.feedback_protocol == "compact" else None,
            )
        except NexentError as exc:
            self.turns.cancel(turn.turn_id, "run_failed")
            # De-identified platform failure summary goes to the audit store, never the regular log.
            if exc.detail:
                self.turns.record_call(turn.turn_id, "run", ok=False, detail=exc.detail[:200])
            raise FeedbackUnavailable(exc.kind, "反馈服务暂不可用，请稍后重试") from exc
        current = self.turns.get(turn.turn_id)
        calls = self.turns.calls(turn.turn_id)
        trace = {
            "real_platform": True,
            "turn_id": turn.turn_id,
            "conversation_id": run.conversation_id,
            "event_types": run.event_types,
            "first_event_ms": run.first_event_ms,
            "platform_first_event_ms": run.first_event_ms,
            "platform_first_final_event_ms": run.first_final_event_ms,
            "total_ms": run.total_ms,
            "trusted_completion": run.trusted_completion,
            "tool_calls": [{"tool": c["tool"], "ok": c["ok"]} for c in calls],
        }
        if current.status != turn_states.SUBMITTED:
            self.turns.cancel(turn.turn_id, "no_feedback")
            logger.warning(
                "nexent_submission_missing role=feedback elapsed_ms=%d first_final_event_ms=%s tool_calls=%d successful_tool_calls=%d",
                run.total_ms, run.first_final_event_ms, len(calls), sum(call["ok"] for call in calls),
            )
            raise FeedbackUnavailable("no_feedback", "反馈未生成，请稍后重试")
        checked = validate_feedback_items((current.submission or {}).get("items"), state, case, self.knowledge)
        if isinstance(checked, str):
            self.turns.cancel(turn.turn_id, "invalid_feedback")
            raise FeedbackUnavailable("invalid_feedback", "反馈未通过校验，请稍后重试")
        if (current.submission or {}).get("basis_snapshot") != self.knowledge.snapshot(checked, case.case_id, case.version):
            self.turns.cancel(turn.turn_id, "basis_mismatch")
            raise FeedbackUnavailable("basis_mismatch", "反馈与工具服务的教学依据版本不一致，请同步知识库后重试")
        if not self.turns.finalize(turn.turn_id):
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
            basis = self.knowledge.search(f"{query} {stage} 反馈 改进建议", case.case_id, stage, limit=1, case_version=case.version)
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

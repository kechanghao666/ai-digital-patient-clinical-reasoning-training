from __future__ import annotations

from typing import Any, Callable

from ..adapters import diagnosis_leak_terms
from ..case_store import CaseStore
from ..models import ActionKind, CaseDefinition, Event, SessionState
from ..storage import SessionRepository
from . import turns as turn_states
from .knowledge import KnowledgeStore
from .turns import Turn, TurnError, TurnStore

MAX_FAILED_CALLS = 3
FEEDBACK_STAGES = ("病史采集", "检查选择", "鉴别诊断", "诊断", "治疗")
MAX_COMMENT = 600
MAX_IMPROVEMENT = 300
FORBIDDEN_MARKERS = ("sentinel", "traceback", "api_key", "turn_token", "secret")


def _fail(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


class TrustedToolService:
    """Tool logic behind the MCP server.

    Rules enforced here, never delegated to the caller:
    * identity (owner, session, case, version) is resolved from the backend turn registry via an
      opaque token; no tool accepts a session id, owner id, fact text, score or any authoritative
      state as an argument;
    * patient turns work only while the session is unsealed and unchanged since the turn began;
    * feedback turns work only for sealed sessions and can only add commentary, never scores.
    """

    def __init__(
        self,
        turns: TurnStore,
        repository: SessionRepository,
        cases: CaseStore,
        knowledge: KnowledgeStore,
    ):
        self.turns = turns
        self.repository = repository
        self.cases = cases
        self.knowledge = knowledge

    # ----------------------------------------------------------------- binding helpers
    def _bind(self, token: str, kind: str, allow: tuple[str, ...] = (turn_states.OPEN,)) -> tuple[Turn, SessionState, CaseDefinition]:
        turn = self.turns.resolve(token, kind=kind, allow=allow)
        try:
            state = self.repository.get(turn.session_id, turn.owner_id)
        except KeyError as exc:
            raise TurnError("session_missing", "session is not available") from exc
        if state.case_id != turn.case_id or state.case_version != turn.case_version:
            raise TurnError("case_mismatch", "session case no longer matches the turn")
        self.cases.reload()
        try:
            case = self.cases.get(turn.case_id, turn.case_version)
        except KeyError as exc:
            self.turns.cancel(turn.turn_id, "case_missing")
            raise TurnError("case_missing", "case version is not available", turn.turn_id) from exc
        if kind == turn_states.PATIENT:
            if state.sealed:
                self.turns.cancel(turn.turn_id, "session_sealed")
                raise TurnError("session_sealed", "session is sealed")
            if state.state_version != turn.state_version:
                self.turns.cancel(turn.turn_id, "state_changed")
                raise TurnError("state_changed", "session changed after this turn began")
        else:
            if not state.sealed:
                raise TurnError("not_sealed", "feedback is only available after sealing")
        return turn, state, case

    def _guard(self, token: str, kind: str, tool: str, body: Callable[[Turn, SessionState, CaseDefinition], dict[str, Any]], allow: tuple[str, ...] = (turn_states.OPEN,)) -> dict[str, Any]:
        turn_id: str | None = None
        try:
            turn, state, case = self._bind(token, kind, allow)
            turn_id = turn.turn_id
            failures = sum(1 for c in self.turns.calls(turn.turn_id) if not c["ok"])
            if failures >= MAX_FAILED_CALLS:
                self.turns.cancel(turn.turn_id, "too_many_failures")
                raise TurnError("turn_cancelled", "turn cancelled after repeated invalid calls")
            result = body(turn, state, case)
        except TurnError as exc:
            audited = turn_id or exc.turn_id
            if audited:
                self.turns.record_call(audited, tool, False, exc.code)
            return _fail(exc.code, str(exc))
        self.turns.record_call(turn.turn_id, tool, bool(result.get("ok")), "" if result.get("ok") else str(result.get("error", "")))
        return result

    # ---------------------------------------------------------------- patient tools
    def get_turn_context(self, turn_token: str) -> dict[str, Any]:
        def body(turn: Turn, state: SessionState, case: CaseDefinition) -> dict[str, Any]:
            facts = {fact.fact_id: fact for fact in case.facts}
            candidates = [
                {"fact_id": fid, "content": facts[fid].content}
                for fid in turn.candidate_fact_ids
                if fid in facts
            ]
            return {
                "ok": True,
                "question": turn.question,
                "candidates": candidates,
                "rules": (
                    "只能从 candidates 中选择与本次提问直接相关的事实；"
                    "不得补充 candidates 之外的信息；与提问无关或不确定时提交空列表。"
                ),
            }

        return self._guard(turn_token, turn_states.PATIENT, "get_turn_context", body)

    def submit_fact_selection(self, turn_token: str, fact_ids: list[str]) -> dict[str, Any]:
        def body(turn: Turn, state: SessionState, case: CaseDefinition) -> dict[str, Any]:
            if not isinstance(fact_ids, list) or not all(isinstance(item, str) for item in fact_ids):
                return _fail("invalid_selection", "fact_ids must be a list of strings")
            unique = list(dict.fromkeys(fact_ids))
            allowed = set(turn.candidate_fact_ids)
            if any(item not in allowed for item in unique):
                return _fail("unauthorized_fact", "selection contains a fact that is not a candidate for this turn")
            if not self.turns.submit(turn.turn_id, {"fact_ids": unique}):
                return _fail("turn_not_open", "selection was already submitted or the turn closed")
            return {
                "ok": True,
                "selected": unique,
                "note": "最终的患者回答由系统依据所选事实生成，无需也不得自行复述其他内容。",
            }

        return self._guard(turn_token, turn_states.PATIENT, "submit_fact_selection", body)

    # --------------------------------------------------------------- feedback tools
    def get_sealed_review(self, turn_token: str) -> dict[str, Any]:
        def body(turn: Turn, state: SessionState, case: CaseDefinition) -> dict[str, Any]:
            snapshot = state.score_snapshot
            if snapshot is None:
                return _fail("no_score", "sealed score snapshot is not available")
            rubric = {item.item_id: item for item in case.rubric}
            seal_seq = state.seal_seq if state.seal_seq is not None else len(state.events)
            events = [_event_digest(event, case) for event in state.events[:seal_seq]]
            return {
                "ok": True,
                "case": {"title": case.title, "specialty": case.specialty, "learning_goals": case.learning_goals},
                "score": {
                    "status": snapshot.status,
                    "total": snapshot.total_score,
                    "max": snapshot.max_score,
                    "items": [
                        {
                            "item_id": item.item_id,
                            "stage": item.stage,
                            "description": rubric[item.item_id].description if item.item_id in rubric else "",
                            "hit": item.hit,
                            "score": item.score,
                            "max_score": item.max_score,
                            "reason": item.reason,
                            "evidence_event_ids": item.evidence_event_ids,
                        }
                        for item in snapshot.items
                    ],
                },
                "submissions": {
                    "diagnosis": (state.diagnosis_submission or {}).get("diagnosis"),
                    "differential": (state.differential_submission or {}).get("candidates", []),
                    "treatment": (state.treatment_submission or {}).get("treatment_id"),
                },
                "events": events,
                "note": (
                    "分数由系统给定，不得修改。五环节各提交一条评语和非空改进建议，"
                    "引用本环节教学依据；有事件时引用真实event_id，未操作时说明缺失，不编造事件。"
                    "不公布隐藏参考诊断及其别名。"
                ),
            }

        return self._guard(turn_token, turn_states.FEEDBACK, "get_sealed_review", body)

    def search_teaching_basis(self, turn_token: str, query: str, stage: str | None = None) -> dict[str, Any]:
        def body(turn: Turn, state: SessionState, case: CaseDefinition) -> dict[str, Any]:
            if not isinstance(query, str) or not query.strip() or len(query) > 200:
                return _fail("invalid_query", "query must be a non-empty string of at most 200 characters")
            entries = self.knowledge.search(query, case.case_id, stage)
            return {
                "ok": True,
                "status": self.knowledge.status,
                "results": [
                    {
                        "basis_id": e.basis_id,
                        "title": e.title,
                        "text": e.text,
                        "sources": list(e.sources),
                    }
                    for e in entries
                ],
            }

        return self._guard(turn_token, turn_states.FEEDBACK, "search_teaching_basis", body)

    def submit_feedback(self, turn_token: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        def body(turn: Turn, state: SessionState, case: CaseDefinition) -> dict[str, Any]:
            problem = validate_feedback_items(items, state, case, self.knowledge)
            if isinstance(problem, str):
                return _fail("invalid_feedback", problem)
            if not self.turns.submit(turn.turn_id, {"items": problem}):
                return _fail("turn_not_open", "feedback was already submitted or the turn closed")
            return {"ok": True, "accepted_items": len(problem)}

        return self._guard(turn_token, turn_states.FEEDBACK, "submit_feedback", body)


def validate_feedback_items(
    items: Any, state: SessionState, case: CaseDefinition, knowledge: KnowledgeStore
) -> list[dict[str, Any]] | str:
    """Return normalized items or a model-safe error string. Scores are never accepted."""
    if not isinstance(items, list) or len(items) != len(FEEDBACK_STAGES):
        return "items must contain exactly one entry for each of the five stages"
    seen: set[str] = set()
    seal_seq = state.seal_seq if state.seal_seq is not None else len(state.events)
    valid_events = {event.event_id for event in state.events[:seal_seq]}
    forbidden = diagnosis_leak_terms(case) | {"".join((case.hidden_diagnosis or "").casefold().split())}
    forbidden.discard("")
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            return "each item must be an object"
        extra = set(item) - {"stage", "comment", "improvement", "evidence_event_ids", "basis_ids"}
        if extra:
            return f"unknown fields are not allowed: {sorted(extra)}"
        stage, comment = item.get("stage"), item.get("comment")
        improvement = item.get("improvement", "")
        if not isinstance(stage, str) or stage not in FEEDBACK_STAGES or stage in seen:
            return "each of the five stages must appear exactly once"
        seen.add(stage)
        if not isinstance(comment, str) or not comment.strip() or len(comment) > MAX_COMMENT:
            return f"comment must be 1 to {MAX_COMMENT} characters"
        if not isinstance(improvement, str) or not improvement.strip() or len(improvement) > MAX_IMPROVEMENT:
            return f"improvement must be 1 to {MAX_IMPROVEMENT} characters"
        evidence = item.get("evidence_event_ids", [])
        basis = item.get("basis_ids", [])
        if not isinstance(evidence, list) or not all(isinstance(v, str) for v in evidence):
            return "evidence_event_ids must be a list of strings"
        if not isinstance(basis, list) or not basis or not all(isinstance(v, str) for v in basis):
            return "basis_ids must be a non-empty list of strings"
        if any(v not in valid_events for v in evidence):
            return "evidence_event_ids must reference events before sealing"
        if any(knowledge.get(v, case.case_id) is None for v in basis):
            return "basis_ids must reference teaching-basis entries available for this case"
        if any(stage not in knowledge.get(v, case.case_id).stages for v in basis):
            return "basis_ids must apply to the feedback stage"
        lowered = "".join((comment + " " + improvement).casefold().split())
        if any(marker in lowered for marker in FORBIDDEN_MARKERS) or any(term in lowered for term in forbidden):
            return "text contains content that is not allowed in student feedback"
        normalized.append(
            {
                "stage": stage,
                "comment": comment.strip(),
                "improvement": improvement.strip(),
                "evidence_event_ids": list(dict.fromkeys(evidence)),
                "basis_ids": list(dict.fromkeys(basis)),
            }
        )
    return sorted(normalized, key=lambda item: FEEDBACK_STAGES.index(item["stage"]))


def _event_digest(event: Event, case: CaseDefinition) -> dict[str, Any]:
    digest: dict[str, Any] = {"event_id": event.event_id, "seq": event.seq, "action": event.action.value, "sim_time": event.sim_time}
    exam_names = {exam.exam_id: exam.standard_name for exam in case.exams}
    if event.action == ActionKind.ASK:
        digest["question"] = str(event.payload.get("question", ""))
        digest["answer"] = str(event.result.get("answer", ""))
    elif event.action == ActionKind.ORDER_EXAM:
        digest["exam"] = exam_names.get(str(event.payload.get("exam_id")), "未命名检查")
        if event.result.get("state") == "ready":
            digest["result"] = next((exam.result for exam in case.exams if exam.exam_id == event.payload.get("exam_id")), None)
    elif event.action == ActionKind.WAIT:
        digest["minutes"] = event.payload.get("minutes")
        digest["ready_exams"] = [exam_names.get(i, "未命名检查") for i in event.result.get("ready_exam_ids", [])]
        digest["exam_results"] = [{"name": exam.standard_name, "result": exam.result}
                                  for exam in case.exams if exam.exam_id in event.result.get("ready_exam_ids", [])]
        digest["clinical_updates"] = event.result.get("clinical_updates", [])
    elif event.action == ActionKind.SUBMIT_DIAGNOSIS:
        digest["diagnosis"] = str(event.payload.get("diagnosis", ""))
    elif event.action == ActionKind.SUBMIT_DIFFERENTIAL:
        digest["candidates"] = event.payload.get("candidates", [])
    elif event.action in {ActionKind.PLAN_TREATMENT, ActionKind.EXECUTE_TREATMENT}:
        digest["treatment_id"] = str(event.payload.get("treatment_id", ""))
        digest["executed"] = event.executed and event.action == ActionKind.EXECUTE_TREATMENT
        digest["clinical_updates"] = event.result.get("clinical_updates", [])
    return digest

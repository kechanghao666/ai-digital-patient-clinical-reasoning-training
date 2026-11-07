from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .case_store import CaseStore
from .models import (
    ActionKind,
    ClinicalState,
    Event,
    ExamState,
    OrderInstance,
    SessionState,
    Workflow,
)
from .storage import SessionRepository


@dataclass
class ActionResult:
    state: SessionState
    event: Event
    response: dict[str, Any]


class StateMachine:
    def __init__(self, cases: CaseStore, repository: SessionRepository):
        self.cases = cases
        self.repository = repository

    def _case(self, state: SessionState):
        return self.cases.get(state.case_id, state.case_version)

    def _facts(self, state: SessionState, fact_ids: list[str]):
        case = self._case(state)
        return [fact for fact in case.facts if fact.fact_id in fact_ids]

    def _append(
        self,
        state: SessionState,
        request_id: str,
        action: ActionKind,
        payload: dict,
        response: dict,
        *,
        intent: str | None = None,
        executed: bool = True,
        fact_ids: list[str] | None = None,
        rule_id: str | None = None,
        before: dict | None = None,
    ) -> Event:
        event = Event(
            request_id=request_id,
            seq=len(state.events) + 1,
            actor="student",
            action=action,
            intent=intent,
            payload=payload,
            executed=executed,
            result=response,
            sim_time=state.sim_time_minutes,
            state_before=before or {},
            state_after={
                "workflow": state.workflow.value,
                "clinical_state": state.clinical_state.value,
                "sim_time_minutes": state.sim_time_minutes,
                "state_version": state.state_version,
            },
            rule_id=rule_id,
            fact_ids_revealed=fact_ids or [],
        )
        state.events.append(event)
        state.state_version += 1
        return event

    def _check_request(self, state: SessionState, request_id: str, payload: object) -> dict | None:
        request_hash = self.repository.hash_payload(payload)
        if request_id in state.pending_request_hashes:
            if state.pending_request_hashes[request_id] != request_hash:
                raise ValueError("request_id was already used with different content")
            for event in state.events:
                if event.request_id == request_id:
                    return event.result
        state.pending_request_hashes[request_id] = request_hash
        return None

    def _advance(self, state: SessionState, minutes: int) -> list[str]:
        if minutes < 0:
            raise ValueError("minutes must not be negative")
        state.sim_time_minutes += minutes
        newly_ready: list[str] = []
        for order in state.orders:
            if order.state == ExamState.PENDING and state.sim_time_minutes >= order.return_sim_time:
                exam = next(e for e in self._case(state).exams if e.exam_id == order.exam_id)
                order.state = ExamState.READY
                order.result = exam.result
                newly_ready.append(order.exam_id)
        return newly_ready

    def _apply_delayed_rules(self, state: SessionState) -> tuple[str | None, list[str]]:
        for rule in self._case(state).rules:
            if rule.delay_anchor_exam is None or rule.from_state != state.clinical_state:
                continue
            for order in state.orders:
                if order.exam_id != rule.delay_anchor_exam or order.state != ExamState.READY:
                    continue
                if state.sim_time_minutes < order.return_sim_time + rule.delay_minutes:
                    continue
                if not rule.repeatable and any(event.rule_id == rule.rule_id for event in state.events):
                    continue
                if any(
                    event.action == ActionKind.EXECUTE_TREATMENT
                    and event.payload.get("treatment_id") in rule.delay_exclusion_treatments
                    and event.executed
                    for event in state.events
                ):
                    continue
                state.clinical_state = rule.effect.to_state
                revealed_fact_ids: list[str] = []
                for fact_id in rule.effect.new_fact_ids:
                    if fact_id not in state.disclosed_fact_ids:
                        state.disclosed_fact_ids.append(fact_id)
                    revealed_fact_ids.append(fact_id)
                return rule.rule_id, revealed_fact_ids
        return None, []

    def _apply_rules(self, state: SessionState, trigger: str) -> tuple[str | None, list[str]]:
        for rule in self._case(state).rules:
            if rule.delay_anchor_exam is not None:
                continue
            if rule.from_state != state.clinical_state or rule.anchor_event != trigger:
                continue
            if not rule.repeatable and any(event.rule_id == rule.rule_id for event in state.events):
                continue
            if any(
                self._event_matches_exclusion(state, event, exclusion)
                for event in state.events
                for exclusion in rule.exclusions
            ):
                continue
            state.clinical_state = rule.effect.to_state
            for fact_id in rule.effect.new_fact_ids:
                if fact_id not in state.disclosed_fact_ids:
                    state.disclosed_fact_ids.append(fact_id)
            return rule.rule_id, list(rule.effect.new_fact_ids)
        return None, []

    def _record_clinical_updates(self, state: SessionState, rule_id: str | None, fact_ids: list[str]) -> list[str]:
        if rule_id is None:
            return []
        updates = [fact.content for fact in self._facts(state, fact_ids)]
        if not updates:
            updates = [next(rule.effect.message for rule in self._case(state).rules if rule.rule_id == rule_id)]
        state.clinical_updates.extend(text for text in updates if text not in state.clinical_updates)
        return updates

    def _trigger_for(self, state: SessionState, treatment_id: str) -> str:
        treatment = next((t for t in self._case(state).treatments if t.treatment_id == treatment_id), None)
        return treatment.trigger if treatment else "treatment_executed"

    def _event_matches_exclusion(self, state: SessionState, event: Event, exclusion: str) -> bool:
        if exclusion in {event.action.value, event.intent, event.rule_id}:
            return True
        if event.action == ActionKind.EXECUTE_TREATMENT and event.executed:
            if exclusion == self._trigger_for(state, str(event.payload.get("treatment_id", ""))):
                return True
        return exclusion in {
            event.payload.get("anchor_event"),
            event.payload.get("treatment_id"),
            event.result.get("trigger") if isinstance(event.result, dict) else None,
        }

    def _validate_action_payload(self, action: ActionKind, payload: dict) -> dict:
        if action == ActionKind.ORDER_EXAM:
            if set(payload) != {"exam_id"}:
                raise ValueError("order_exam payload must contain only exam_id")
            exam_id = payload.get("exam_id")
            if not isinstance(exam_id, str) or not exam_id:
                raise ValueError("exam_id is required")
            return {"exam_id": exam_id}
        if action == ActionKind.WAIT:
            if set(payload) != {"minutes"}:
                raise ValueError("wait payload must contain only minutes")
            minutes = payload.get("minutes")
            if not isinstance(minutes, int):
                raise ValueError("wait minutes must be an integer")
            if minutes < 0 or minutes > 240:
                raise ValueError("wait minutes must be between 0 and 240")
            return {"minutes": minutes}
        if action in {ActionKind.PLAN_TREATMENT, ActionKind.EXECUTE_TREATMENT}:
            allowed = {"treatment_id", "parameters", "confirm_execution"}
            if set(payload) - allowed:
                raise ValueError("treatment payload contains unknown fields")
            treatment_id = payload.get("treatment_id")
            if not isinstance(treatment_id, str) or not treatment_id:
                raise ValueError("treatment_id is required")
            confirm = payload.get("confirm_execution", False)
            parameters = payload.get("parameters", {})
            if not isinstance(parameters, dict):
                raise ValueError("treatment parameters must be an object")
            if action == ActionKind.EXECUTE_TREATMENT and not isinstance(confirm, bool):
                raise ValueError("confirm_execution must be boolean")
            if action == ActionKind.EXECUTE_TREATMENT and not confirm:
                raise ValueError("treatment execution requires explicit confirmation")
            return {"treatment_id": treatment_id, "confirm_execution": bool(confirm), "parameters": parameters}
        if action == ActionKind.SUBMIT_DIAGNOSIS:
            if set(payload) != {"diagnosis"}:
                raise ValueError("diagnosis payload must contain only diagnosis")
            diagnosis = payload.get("diagnosis")
            if not isinstance(diagnosis, str) or not diagnosis.strip():
                raise ValueError("diagnosis is required")
            return {"diagnosis": diagnosis.strip()}
        if action == ActionKind.SUBMIT_DIFFERENTIAL:
            if set(payload) != {"candidates"}:
                raise ValueError("differential payload must contain only candidates")
            candidates = payload.get("candidates")
            if not isinstance(candidates, list):
                raise ValueError("candidates must be a list")
            for candidate in candidates:
                if not isinstance(candidate, dict) or not isinstance(candidate.get("candidate_id"), str):
                    raise ValueError("each differential candidate must have a candidate_id")
                if set(candidate) - {"candidate_id", "supporting_evidence", "opposing_evidence", "evidence_event_ids"}:
                    raise ValueError("differential candidate contains unknown fields")
                for key in ("supporting_evidence", "opposing_evidence", "evidence_event_ids"):
                    if not isinstance(candidate.get(key, []), list) or not all(
                        isinstance(value, str) for value in candidate.get(key, [])
                    ):
                        raise ValueError(f"{key} must be a list of strings")
            return {"candidates": candidates}
        if action == ActionKind.SEAL:
            if payload:
                raise ValueError("seal payload must be empty")
            return {"sealed": True}
        if action == ActionKind.UPDATE_NOTES:
            if set(payload) != {"notes"}:
                raise ValueError("notes payload must contain only notes")
            notes = payload.get("notes")
            if not isinstance(notes, str) or len(notes) > 10000:
                raise ValueError("notes must be a string up to 10000 characters")
            return {"notes": notes}
        return payload

    def _require_known_treatment(self, state: SessionState, treatment_id: object) -> None:
        if treatment_id not in {t.treatment_id for t in self._case(state).treatments}:
            raise ValueError("unsupported treatment action")

    def apply(self, state: SessionState, request_id: str, action: ActionKind, payload: dict) -> ActionResult:
        payload = self._validate_action_payload(action, payload)
        replay = self._check_request(state, request_id, {"action": action.value, "payload": payload})
        if replay is not None:
            event = next(e for e in state.events if e.request_id == request_id)
            return ActionResult(state=state, event=event, response=replay)
        if state.sealed:
            raise ValueError("session is sealed")
        before = {
            "workflow": state.workflow.value,
            "clinical_state": state.clinical_state.value,
            "sim_time_minutes": state.sim_time_minutes,
            "state_version": state.state_version,
        }
        response: dict[str, Any] = {}
        rule_id = None
        revealed_fact_ids: list[str] = []
        if action == ActionKind.ORDER_EXAM:
            exam_id = payload.get("exam_id")
            exam = next((e for e in self._case(state).exams if e.exam_id == exam_id), None)
            if exam is None or not exam.configured:
                raise ValueError("exam is not configured")
            if any(order.exam_id == exam_id and order.state in {ExamState.PENDING, ExamState.READY} for order in state.orders):
                raise ValueError("exam already ordered; use a new order only for an explicit recheck")
            order = OrderInstance(
                exam_id=exam_id,
                ordered_sim_time=state.sim_time_minutes,
                sample_sim_time=state.sim_time_minutes,
                return_sim_time=state.sim_time_minutes + exam.return_after_minutes,
            )
            state.orders.append(order)
            state.workflow = Workflow.EXAMS
            response = {"order_id": order.order_id, "exam_id": exam_id, "state": order.state.value}
            if exam.return_after_minutes == 0:
                order.state = ExamState.READY
                order.result = exam.result
                response["state"] = order.state.value
                response["ready_exam_ids"] = [exam_id]
        elif action == ActionKind.WAIT:
            minutes = int(payload.get("minutes", 0))
            if minutes < 0 or minutes > 240:
                raise ValueError("wait minutes must be between 0 and 240")
            ready = self._advance(state, minutes)
            rule_id, revealed_fact_ids = self._apply_delayed_rules(state)
            update_texts = self._record_clinical_updates(state, rule_id, revealed_fact_ids)
            response = {
                "advanced_minutes": minutes,
                "ready_exam_ids": ready,
                "clinical_state": state.clinical_state.value,
                "clinical_updates": update_texts,
            }
        elif action == ActionKind.PLAN_TREATMENT:
            self._require_known_treatment(state, payload.get("treatment_id"))
            state.workflow = Workflow.TREATMENT
            state.treatment_submission = payload
            response = {"planned": True, "executed": False, "treatment_id": payload.get("treatment_id")}
        elif action == ActionKind.EXECUTE_TREATMENT:
            treatment_id = payload.get("treatment_id")
            if not payload.get("confirm_execution"):
                raise ValueError("treatment execution requires explicit confirmation")
            self._require_known_treatment(state, treatment_id)
            state.workflow = Workflow.TREATMENT
            state.treatment_submission = payload
            rule_id, revealed_fact_ids = self._apply_rules(state, self._trigger_for(state, treatment_id))
            response = {"executed": True, "treatment_id": treatment_id, "clinical_state": state.clinical_state.value,
                        "clinical_updates": self._record_clinical_updates(state, rule_id, revealed_fact_ids)}
        elif action == ActionKind.SUBMIT_DIAGNOSIS:
            state.workflow = Workflow.DIAGNOSIS
            state.diagnosis_submission = payload
            response = {"submitted": True}
        elif action == ActionKind.SUBMIT_DIFFERENTIAL:
            state.workflow = Workflow.DIFFERENTIAL
            state.differential_submission = payload
            response = {"submitted": True}
        elif action == ActionKind.UPDATE_NOTES:
            state.case_notes = payload["notes"]
            response = {"submitted": True}
        elif action == ActionKind.SEAL:
            state.seal_seq = len(state.events)
            state.seal_sim_time = state.sim_time_minutes
            state.sealed = True
            state.workflow = Workflow.SUBMITTED
            response = {"sealed": True, "seal_seq": state.seal_seq}
        else:
            raise ValueError(f"unsupported action: {action.value}")
        if action == ActionKind.SEAL:
            from .scoring import score_session

            state.score_snapshot = score_session(state, self._case(state))
        event = self._append(
            state,
            request_id,
            action,
            payload,
            response,
            rule_id=rule_id,
            before=before,
            fact_ids=revealed_fact_ids,
        )
        self.repository.save(state, expected_version=before["state_version"])
        return ActionResult(state=state, event=event, response=response)

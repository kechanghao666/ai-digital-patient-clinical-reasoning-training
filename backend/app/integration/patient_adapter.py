from __future__ import annotations

import logging
import time

from ..adapters import PatientSelection, PatientTurnRequest
from .config import IntegrationSettings
from .nexent_client import NexentClient, NexentError
from . import turns as turn_states
from .turns import TurnStore


logger = logging.getLogger(__name__)


class NexentPatientAdapter:
    """Runs the Nexent patient agent for one question and reads back its tool-recorded selection.

    The agent receives the student's question plus an opaque turn token. It must call the trusted
    MCP tools to read the backend-authorized candidate facts and to submit its selection. The
    selection is read from the backend turn registry, never parsed from model text.
    """

    def __init__(self, settings: IntegrationSettings, turns: TurnStore, client: NexentClient | None = None):
        self.settings = settings
        self.turns = turns
        self.client = client or NexentClient(settings)

    def select_facts(self, request: PatientTurnRequest) -> PatientSelection:
        started = time.monotonic()
        turn, token = self.turns.issue(
            kind=turn_states.PATIENT,
            owner_id=request.owner_id,
            session_id=request.session_id,
            case_id=request.case_id,
            case_version=request.case_version,
            request_id=request.request_id,
            state_version=request.state_version,
            question=request.question,
            candidate_fact_ids=[fact.fact_id for fact in request.candidates],
            ttl_s=self.settings.turn_ttl_s,
            mode=self.settings.mode,
        )
        if self.settings.patient_protocol == "single":
            agent_name = self.settings.patient_agent_single or self.settings.patient_agent_name
            query = _build_single_query(request.question, token, request.candidates)
        else:
            agent_name = self.settings.patient_agent_name
            query = _build_query(request.question, token)
        try:
            run = self.client.run(
                agent_name=agent_name,
                query=query,
                metadata={"turn_token": token, "role": "patient"},
                audit_metadata={"turn_id": turn.turn_id, "request_id": request.request_id},
                idempotency_key=f"{turn.turn_id}",
                trusted_completion=lambda: self.turns.get(turn.turn_id).status == turn_states.SUBMITTED,
            )
        except NexentError as exc:
            self.turns.cancel(turn.turn_id, "run_failed")
            # De-identified platform failure summary goes to the audit store, never the regular log.
            if exc.detail:
                self.turns.record_call(turn.turn_id, "run", ok=False, detail=exc.detail[:200])
            raise
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
            "trusted_completion_ms": run.trusted_completion_ms,
            "tool_calls": [{"tool": c["tool"], "ok": c["ok"]} for c in calls],
        }
        if current.status == turn_states.SUBMITTED and self.turns.finalize(turn.turn_id):
            trace["finalize_ms"] = int((time.monotonic() - started) * 1000)
            ids = list((current.submission or {}).get("fact_ids", []))
            return PatientSelection(fact_ids=ids, source="nexent", trace=trace)
        self.turns.cancel(turn.turn_id, "no_selection")
        if current.cancel_reason == "state_changed":
            raise NexentError("state_changed", "Session changed during the model run")
        logger.warning(
            "nexent_submission_missing role=patient elapsed_ms=%d first_final_event_ms=%s tool_calls=%d successful_tool_calls=%d",
            run.total_ms, run.first_final_event_ms, len(calls), sum(call["ok"] for call in calls),
        )
        raise NexentError("protocol", "Nexent did not submit a fact selection")


def _build_query(question: str, token: str) -> str:
    return (
        "【系统回合】你正在扮演模拟患者。必须先调用工具 get_turn_context，再调用 submit_fact_selection，"
        f"turn_token 为 {token}。\n【学生提问】{question}"
    )


def _build_single_query(question: str, token: str, candidates: tuple) -> str:
    """Single trusted submission: the turn's authorised candidates are provided inline.

    Only the facts the backend authorised for this turn are sent; the selection is still validated
    by the trusted MCP tool and the answer is rendered by the backend, never from model text.
    """
    lines = "\n".join(f"{fact.fact_id}：{fact.content}" for fact in candidates) or "（本轮无候选）"
    return (
        f"【系统回合】你正在扮演模拟患者。turn_token 为 {token}。\n"
        "本轮已授权的候选事实如下（只能从中选择，不得补充候选之外的内容）：\n"
        f"{lines}\n"
        f"【学生提问】{question}\n"
        "只调用一次 submit_fact_selection(turn_token, fact_ids)，提交与提问直接相关的事实ID列表（可为空列表），随后立即结束。"
    )

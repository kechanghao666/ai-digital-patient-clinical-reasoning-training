from __future__ import annotations

from .context import check_session, require_context


def get_visible_facts(session_id: str, facts: list[dict]) -> list[dict]:
    check_session(session_id)
    return facts


def disclose(session_id: str, fact_id: str, authorized_fact_ids: set[str], facts: list[dict]) -> dict:
    check_session(session_id)
    if fact_id not in authorized_fact_ids:
        return {"allowed": False, "content": None}
    fact = next((fact for fact in facts if fact.get("fact_id") == fact_id), None)
    return {"allowed": fact is not None, "content": fact.get("content") if fact else None}


def apply_action(session_id: str, action: str) -> dict:
    context = check_session(session_id)
    if action not in context.allowed_actions:
        raise PermissionError("action is not allowed for this run")
    return {"accepted": True, "action": action}


def check_contradiction(session_id: str, candidate_answer: str, allowed_strings: set[str]) -> dict:
    check_session(session_id)
    return {
        "pass": any(value in candidate_answer for value in allowed_strings),
        "reason": "candidate contains an allowed reviewed value",
    }

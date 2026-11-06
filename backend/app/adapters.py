from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import CaseDefinition, Fact


class PatientAdapter(Protocol):
    def answer(self, question: str, facts: list[str], visible_state: dict) -> str: ...


@dataclass
class SimulatorPatientAdapter:
    """Deterministic local adapter; it is not evidence of real Nexent integration."""

    def answer(self, question: str, facts: list[str], visible_state: dict) -> str:
        if facts:
            return "，".join(facts)
        return "我不太确定您的问题，能否换一种方式询问？"


@dataclass
class NexentPatientAdapter:
    base_url: str
    api_key: str
    agent_name: str

    def answer(self, question: str, facts: list[str], visible_state: dict) -> str:
        raise RuntimeError("真实 Nexent 适配器需要在目标环境配置并完成联调")


def _normalize(text: str) -> str:
    return "".join(text.casefold().split())


def authorized_answer_or_fallback(
    answer: str,
    authorized_facts: list[Fact],
    case: CaseDefinition,
) -> tuple[str, list[str], bool]:
    """Gate adapter output before persistence and before returning it to a student."""
    fallback = "我不太确定您的问题，能否换一种方式询问？"
    candidate = answer.strip()
    if not candidate:
        return fallback, [], True
    normalized = _normalize(candidate)
    hidden = _normalize(case.hidden_diagnosis or "")
    if hidden and hidden in normalized:
        return fallback, [], True
    hidden_terms = []
    if "stemi" in hidden or "心肌梗死" in hidden:
        hidden_terms.extend(("stemi", "心肌梗死"))
    if any(term in normalized for term in hidden_terms):
        return fallback, [], True
    if any(marker in normalized for marker in ("sentinel", "内部错误", "traceback", "api_key")):
        return fallback, [], True
    if not authorized_facts:
        return (candidate, [], False) if normalized == _normalize(fallback) else (fallback, [], True)
    expected = _normalize("，".join(fact.content for fact in authorized_facts))
    if normalized != expected:
        return fallback, [], True
    fact_ids = [
        fact.fact_id
        for fact in authorized_facts
        if _normalize(fact.content) in normalized
    ]
    return candidate, fact_ids, False

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import CaseDefinition, Fact

FALLBACK_ANSWER = "我不太确定您的问题，能否换一种方式询问？"


@dataclass(frozen=True)
class PatientTurnRequest:
    """Built only by the trusted backend; the adapter never receives student-controlled identity."""

    owner_id: str
    session_id: str
    case_id: str
    case_version: int
    request_id: str
    state_version: int
    question: str
    candidates: tuple[Fact, ...]


@dataclass
class PatientSelection:
    """Which backend-authorized facts the patient agent chose to answer with.

    The student-visible utterance is rendered by the backend from these facts; free model text
    is never shown. ``source`` and ``trace`` are audit data and are not part of the public projection.
    """

    fact_ids: list[str]
    source: str
    trace: dict[str, Any] = field(default_factory=dict)


class PatientAdapter(Protocol):
    def select_facts(self, turn: PatientTurnRequest) -> PatientSelection: ...


@dataclass
class SimulatorPatientAdapter:
    """Deterministic local adapter; it is not evidence of real Nexent integration."""

    def answer(self, question: str, facts: list[str], visible_state: dict) -> str:
        if facts:
            return "，".join(facts)
        return FALLBACK_ANSWER

    def select_facts(self, turn: PatientTurnRequest) -> PatientSelection:
        return PatientSelection(
            fact_ids=[fact.fact_id for fact in turn.candidates],
            source="simulator",
            trace={"real_platform": False},
        )


def render_selected_answer(candidates: list[Fact], selected_ids: list[str]) -> tuple[list[Fact], str]:
    """Render the student-visible utterance from selected facts in authorized order."""
    wanted = set(selected_ids)
    selected = [fact for fact in candidates if fact.fact_id in wanted]
    if not selected:
        return [], FALLBACK_ANSWER
    return selected, "，".join(fact.content for fact in selected)


def _normalize(text: str) -> str:
    return "".join(text.casefold().split())


def diagnosis_leak_terms(case: CaseDefinition) -> set[str]:
    """Diagnosis words that must never appear in patient-visible text, from the case's own config."""
    terms: set[str] = set()
    for rubric in case.rubric:
        if rubric.diagnosis is not None:
            terms.update(_normalize(name) for name in rubric.diagnosis.names)
            terms.update(_normalize(term) for term in rubric.diagnosis.leak_terms)
    return {term for term in terms if term}


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
    if any(term in normalized for term in diagnosis_leak_terms(case)):
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

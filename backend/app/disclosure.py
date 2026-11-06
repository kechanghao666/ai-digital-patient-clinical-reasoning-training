from __future__ import annotations

import re

from .models import Fact, SessionState


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def classify_and_authorize(state: SessionState, question: str, facts: list[Fact]) -> list[Fact]:
    query = normalize(question)
    matches: list[Fact] = []
    for fact in facts:
        if not fact.current:
            continue
        if fact.status_valid_from > state.sim_time_minutes:
            continue
        if fact.status_valid_to is not None and state.sim_time_minutes > fact.status_valid_to:
            continue
        if fact.patient_known is False:
            continue
        triggers = [normalize(t) for t in fact.disclosure_triggers]
        if any(trigger and trigger in query for trigger in triggers):
            matches.append(fact)
    return matches

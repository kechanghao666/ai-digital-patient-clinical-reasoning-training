"""De-identified regression: the feedback validator must reject diagnosis and marker leaks
with distinct, actionable messages (no full case/model payload is ever compared here)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.case_store import CaseStore
from app.integration.knowledge import KnowledgeStore
from app.integration.tools import FEEDBACK_STAGES, feedback_stage_basis, validate_feedback_items
from app.models import SessionState

ROOT = Path(__file__).parents[1]


def _case_state():
    case = CaseStore(ROOT / "cases").get("STEMI-001")
    state = SessionState(owner_id="o", case_id=case.case_id, case_version=case.version, sealed=True)
    knowledge = KnowledgeStore(ROOT / "knowledge")
    basis = feedback_stage_basis(knowledge, case)
    return case, state, knowledge, {s: basis[s]["basis_id"] for s in FEEDBACK_STAGES}


def _items(basis_ids: dict[str, str], comment_overrides: dict[str, str] | None = None):
    out = []
    for stage in FEEDBACK_STAGES:
        comment = (comment_overrides or {}).get(stage, "本环节完成情况良好。")
        out.append({"stage": stage, "comment": comment, "improvement": "保持并核对时间点。",
                    "evidence_event_ids": [], "basis_ids": [basis_ids[stage]]})
    return out


def test_clean_feedback_has_no_text_violation():
    case, state, knowledge, basis = _case_state()
    result = validate_feedback_items(_items(basis), state, case, knowledge)
    assert not isinstance(result, str)


def test_diagnosis_leak_is_rejected_with_distinct_message():
    case, state, knowledge, basis = _case_state()
    result = validate_feedback_items(_items(basis, {"诊断": "符合心肌梗死的表现。"}), state, case, knowledge)
    assert result == "text contains a diagnosis term that is not allowed"


def test_marker_leak_is_rejected_with_distinct_message():
    case, state, knowledge, basis = _case_state()
    result = validate_feedback_items(_items(basis, {"病史采集": "turn_token 为 x。"}), state, case, knowledge)
    assert result == "text contains an internal marker that is not allowed"

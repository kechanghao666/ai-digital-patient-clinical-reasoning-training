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


# --- de-identified teaching-action gaps -------------------------------------

def test_teaching_gap_names_the_missing_history_action():
    from app.integration.feedback import teaching_gap
    from app.models import ScoreItem

    case = CaseStore(ROOT / "cases").get("STEMI-001")
    rubric = {r.item_id: r for r in case.rubric}
    item = ScoreItem(item_id="f_pain_radiation", stage="病史采集", hit=False, score=0.0, max_score=1.0,
                     reason="未找到满足时点和证据类型的事件")
    assert teaching_gap(item, rubric["f_pain_radiation"], case) == "询问疼痛放射：未找到采集记录"


def test_teaching_gap_never_leaks_a_diagnosis():
    from app.integration.feedback import teaching_gap
    from app.models import ScoreItem

    store = CaseStore(ROOT / "cases")
    for public_id in ("CASE-001", "CASE-002", "CASE-003", "CASE-004", "CASE-005"):
        case = store.resolve_for_student(public_id, include_unpublished=True)
        rubric = {r.item_id: r for r in case.rubric}
        for r in case.rubric:
            item = ScoreItem(item_id=r.item_id, stage=r.stage, hit=False, score=0.0, max_score=1.0,
                             reason="未找到满足时点和证据类型的事件")
            gap = teaching_gap(item, rubric[r.item_id], case)
            # The reference diagnosis must never appear in a teaching-action gap.
            for term in {t for t in (case.hidden_diagnosis or "").casefold().split("，") if t}:
                assert term not in gap.casefold(), (public_id, r.item_id, gap)
            assert "心肌梗死" not in gap and "stemi" not in gap.casefold()


def test_teaching_gap_falls_back_to_neutral_on_unsafe_description():
    """A teacher-custom description containing answer text must not be forwarded."""
    from app.integration.feedback import teaching_gap
    from app.models import RubricItem, ScoreItem

    case = CaseStore(ROOT / "cases").get("STEMI-001")
    custom = RubricItem(item_id="f_custom", stage="病史采集", description="询问胸痛（答案：心肌梗死）",
                        evidence_type="ask_event", weight=1)
    item = ScoreItem(item_id="f_custom", stage="病史采集", hit=False, score=0.0, max_score=1.0,
                     reason="未找到满足时点和证据类型的事件")
    assert teaching_gap(item, custom, case) == "病史采集：未找到采集记录"


def test_teaching_gap_missing_rubric_mapping_uses_stage_neutral_name():
    from app.integration.feedback import teaching_gap
    from app.models import ScoreItem

    case = CaseStore(ROOT / "cases").get("STEMI-001")
    item = ScoreItem(item_id="exam_stewardship", stage="检查选择", hit=False, score=0.0, max_score=1.0,
                     reason="未开立任何检查")
    assert teaching_gap(item, None, case) == "检查选择：未找到开立记录"


def test_teaching_gap_partial_with_review_is_pending_judgment():
    from app.integration.feedback import teaching_gap
    from app.models import ScoreItem

    case = CaseStore(ROOT / "cases").get("STEMI-001")
    rubric = {r.item_id: r for r in case.rubric}
    item = ScoreItem(item_id="aortic_dissection", stage="鉴别诊断", hit=False, score=0.5, max_score=1.0,
                     reason="部分证据文字无法与病例规则自动对应，需教师复核")
    assert teaching_gap(item, rubric["aortic_dissection"], case) == "提供主动脉夹层的支持/反对证据：待教师判断"


def test_compact_query_content_is_what_reaches_the_model(tmp_path, monkeypatch):
    """The assembled compact query (the actual model input) names the gap and never leaks."""
    from app.integration.feedback import FeedbackService
    from test_cases_config import Api

    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    api.ask(sid, "疼痛是什么性质")
    api.ask(sid, "当时在干什么")  # deliberately omit radiation
    api.seal_and_score(sid)
    state = api.repository.get(sid, "demo-cfg")
    case = api.store.get(state.case_id, state.case_version)
    query = FeedbackService(None, None, None, None)._compact_query(state, case)
    assert "询问疼痛放射" in query and "未找到采集记录" in query
    assert "心肌梗死" not in query and "stemi" not in query.casefold()

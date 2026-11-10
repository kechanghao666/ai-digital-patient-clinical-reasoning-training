"""Synthetic authoring rules verify timing, without approving clinical prerequisites."""
import pytest
from pydantic import ValidationError

from app.models import CaseDefinition
from test_cases_config import Api, item
from test_teacher_cases import TEACHER, new_case, put


def configured_case():
    raw = new_case(public_id="CASE-T100")
    rubric = next(r for r in raw["rubric"] if r["evidence_type"] == "treatment_execution")
    rubric["required_prior_exam_ids"] = [raw["exams"][0]["exam_id"]]
    return raw, rubric


@pytest.mark.parametrize("path,credit", [
    ("missing", False), ("pending", False), ("immediate", True),
    ("ready", True), ("late_repeat", False), ("plan_then_ready", True),
    ("partial", False), ("unrelated", False), ("plan_then_missing", False),
    ("failed_result", False),
])
def test_only_results_before_first_execution_earn_credit(tmp_path, monkeypatch, path, credit):
    api = Api(tmp_path, monkeypatch)
    raw, rubric = configured_case()
    exam = raw["exams"][0]
    if path == "immediate":
        exam["return_after_minutes"] = 0
    if path == "partial":
        rubric["required_prior_exam_ids"].append(raw["exams"][1]["exam_id"])
    saved = put(api, raw)
    assert saved.status_code == 200, saved.text
    sid = api.start(saved.json()["public_id"])
    treatment = {"treatment_id": rubric["item_id"], "confirm_execution": True}
    if path.startswith("plan_then"):
        api.act(sid, "plan_treatment", {"treatment_id": rubric["item_id"]})
    if path == "late_repeat":
        api.act(sid, "execute_treatment", treatment)
    result_event = None
    if path not in {"missing", "plan_then_missing"}:
        selected = raw["exams"][1] if path == "unrelated" else exam
        ordered = api.act(sid, "order_exam", {"exam_id": selected["exam_id"]})
        if path == "immediate":
            result_event = ordered["event"]["event_id"]
        elif path != "pending":
            result_event = api.wait(sid, selected["return_after_minutes"])
    if path == "failed_result":
        state = api.repository.get(sid, "demo-cfg")
        next(e for e in state.events if e.event_id == result_event).executed = False
        api.repository.save(state, expected_version=state.state_version)
    execution = api.act(sid, "execute_treatment", treatment)
    snapshot = api.seal_and_score(sid)
    scored = item(snapshot, rubric["item_id"])
    assert scored["hit"] is credit
    assert scored["score"] == (rubric["weight"] if credit else 0)
    assert execution["event"]["event_id"] in scored["evidence_event_ids"]
    if credit:
        assert result_event in scored["evidence_event_ids"]
    else:
        assert snapshot["status"] == "needs_review"
        assert "首次执行" in scored["reason"]
    # Repeated reads preserve the sealed score rather than recalculating against new state.
    assert api.client.get(f"/api/sessions/{sid}/score", headers=api.headers).json() == snapshot


@pytest.mark.parametrize("invalid", ["unknown", "duplicate", "wrong_type", "disabled", "blank_result"])
def test_invalid_prerequisites_rejected_before_saving(tmp_path, monkeypatch, invalid):
    api = Api(tmp_path, monkeypatch)
    raw, rubric = configured_case()
    raw["rules"] = []
    if invalid == "unknown":
        rubric["required_prior_exam_ids"] = ["missing_exam"]
    elif invalid == "duplicate":
        rubric["required_prior_exam_ids"] *= 2
    elif invalid == "wrong_type":
        raw["rubric"][0]["required_prior_exam_ids"] = rubric["required_prior_exam_ids"]
    elif invalid == "disabled":
        raw["exams"][0]["configured"] = False
    else:
        raw["exams"][0].update(result=" ", allowed_values=[" "])
    with pytest.raises(ValidationError, match="required_prior_exam_ids"):
        CaseDefinition.model_validate(raw)
    assert put(api, raw).status_code == 422
    assert api.client.post("/api/teacher/cases/validate", headers=TEACHER, json=raw).status_code == 422
    assert not api.store.versions(raw["case_id"])


def test_new_requirement_does_not_change_existing_versions_or_sealed_scores(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    raw, rubric = configured_case()
    required = rubric.pop("required_prior_exam_ids")
    saved = put(api, raw)
    assert saved.status_code == 200
    old_sid = api.start(saved.json()["public_id"])
    resumed_sid = api.start(saved.json()["public_id"])
    api.act(old_sid, "execute_treatment", {"treatment_id": rubric["item_id"], "confirm_execution": True})
    original = api.seal_and_score(old_sid)
    assert item(original, rubric["item_id"])["score"] == rubric["weight"]
    rubric["required_prior_exam_ids"] = required
    assert put(api, raw).status_code == 200
    assert api.client.get(f"/api/sessions/{old_sid}/score", headers=api.headers).json() == original
    api.act(resumed_sid, "execute_treatment", {"treatment_id": rubric["item_id"], "confirm_execution": True})
    assert item(api.seal_and_score(resumed_sid), rubric["item_id"])["score"] == rubric["weight"]
    new_sid = api.start(saved.json()["public_id"])
    api.act(new_sid, "execute_treatment", {"treatment_id": rubric["item_id"], "confirm_execution": True})
    assert item(api.seal_and_score(new_sid), rubric["item_id"])["score"] == 0


def test_unexecuted_plan_keeps_its_existing_explanation(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    raw, rubric = configured_case()
    saved = put(api, raw)
    assert saved.status_code == 200
    sid = api.start(saved.json()["public_id"])
    api.act(sid, "plan_treatment", {"treatment_id": rubric["item_id"]})
    scored = item(api.seal_and_score(sid), rubric["item_id"])
    assert not scored["hit"] and scored["score"] == 0
    assert "仅提交了计划" in scored["reason"]

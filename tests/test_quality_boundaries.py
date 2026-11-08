"""Regressions for the isolated 2026-11-08 audit; all writes use temporary stores."""
import gc

import pytest

from app.integration.config import IntegrationSettings, ROOT
from app.models import SessionState
from app.storage import SessionRepository
from test_cases_config import Api, item
from test_frontend_smoke import _page, _route_requests
from test_teacher_cases import TEACHER, new_case, put


@pytest.mark.parametrize("text", ["。", "热", "  ", "气短", "发热气短", "并不存在的前缀活动后气短"])
def test_uninformative_or_unmatched_text_cannot_earn_full_credit(tmp_path, monkeypatch, text):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-004")
    event = api.ask(sid, "发热多少度，有没有气短和呼吸困难")
    api.differential(sid, [{"candidate_id": "pulmonary_embolism", "supporting_evidence": [text],
                           "opposing_evidence": [text], "evidence_event_ids": [event]}])
    snapshot = api.seal_and_score(sid)
    result = item(snapshot, "pulmonary_embolism")
    assert result["score"] == 0 and not result["hit"]
    assert snapshot["status"] == "needs_review"


def test_invalid_reference_is_not_silently_reported_as_full_success(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-004")
    event = api.ask(sid, "发热多少度，有没有气短和呼吸困难")
    api.differential(sid, [{"candidate_id": "pulmonary_embolism", "supporting_evidence": ["活动后气短"],
        "opposing_evidence": ["最高体温39℃"], "evidence_event_ids": [event, "missing-event"]}])
    snapshot = api.seal_and_score(sid)
    result = item(snapshot, "pulmonary_embolism")
    assert not result["hit"] and "不存在" in result["reason"]
    assert "复核" in result["reason"] and snapshot["status"] == "needs_review"
    assert "missing-event" not in result["evidence_event_ids"]


@pytest.mark.parametrize("field,value", [
    ("evidence_type", "ask_evnet"), ("content", ""), ("content", " \n "),
    ("precondition", "clinical_state == stabilized"), ("order_required", False),
    ("latest_workflow", "history_taking"),
])
def test_invalid_and_unsupported_teacher_settings_are_rejected(tmp_path, monkeypatch, field, value):
    api = Api(tmp_path, monkeypatch)
    raw = new_case(public_id="CASE-Q001")
    target = raw["facts"][1] if field == "content" else raw["exams"][0] if field in {"precondition", "order_required"} else raw["rubric"][0]
    target[field] = value
    assert api.client.post("/api/teacher/cases/validate", headers=TEACHER, json=raw).status_code == 422
    assert put(api, raw).status_code == 422
    assert api.store.versions(raw["case_id"]) == []


@pytest.mark.parametrize("problem", ["contradictory_keys", "unanchored_delay"])
def test_ambiguous_or_unsupported_evolution_and_evidence_rules_are_rejected(tmp_path, monkeypatch, problem):
    api = Api(tmp_path, monkeypatch)
    raw = new_case(public_id="CASE-Q002")
    if problem == "unanchored_delay":
        raw["rules"][0]["delay_anchor_exam"] = None
    else:
        rule = next(rubric["differential"] for rubric in raw["rubric"]
                    if rubric.get("differential", {}).get("supporting_keys"))
        rule["opposing_keys"] = rule["supporting_keys"]
    assert api.client.post("/api/teacher/cases/validate", headers=TEACHER, json=raw).status_code == 422
    assert put(api, raw).status_code == 422


def test_immediate_results_are_selectable_and_scoreable(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, owner="demo-student")
    raw = new_case()
    next(exam for exam in raw["exams"] if exam["exam_id"] == "ex_us")["return_after_minutes"] = 0
    public_id = put(api, raw).json()["public_id"]
    sid = api.start(public_id)
    history = api.ask(sid, "有没有恶心呕吐")
    ordered = api.act(sid, "order_exam", {"exam_id": "ex_us"})
    event = ordered["event"]
    assert event["result"]["exam_results"] and event["result"]["state"] == "ready"
    _route_requests(monkeypatch, api)
    page = _page(api)
    _click(page, "恢复训练")
    page.multiselect(key="differential_choice").set_value(["gastroenteritis"]).run()
    page.text_input(key="ddx_support_gastroenteritis").set_value("有恶心，吐过一次")
    page.text_input(key="ddx_oppose_gastroenteritis").set_value("阑尾增粗并周围渗出")
    page.multiselect(key="ddx_events_gastroenteritis").set_value([history, event["event_id"]])
    _click(page, "提交鉴别诊断")
    candidate = api.repository.get(sid, "demo-student").differential_submission["candidates"][0]
    assert candidate["evidence_event_ids"] == [history, event["event_id"]]
    assert item(api.seal_and_score(sid), "gastroenteritis")["hit"]


@pytest.mark.parametrize("has_facts", [True, False])
def test_treatment_evolution_has_public_updates_and_replay(tmp_path, monkeypatch, has_facts):
    api = Api(tmp_path, monkeypatch)
    raw = new_case()
    rule = raw["rules"][0]
    rule.update(anchor_event="surgical_started", delay_anchor_exam=None, delay_minutes=0, delay_exclusion_treatments=[])
    if not has_facts:
        rule["effect"]["new_fact_ids"] = []
    public = put(api, raw).json()["public_id"]
    sid = api.start(public)
    result = api.act(sid, "execute_treatment", {"treatment_id": "surgical_consult", "confirm_execution": True})
    assert result["state"]["clinical_state"] == "deteriorating"
    assert result["response"]["clinical_updates"]
    assert result["event"]["result"]["clinical_updates"] == result["state"]["clinical_updates"]
    stored = api.repository.get(sid, "demo-cfg")
    assert stored.events[-1].fact_ids_revealed == rule["effect"]["new_fact_ids"]
    replay = api.client.get(f"/api/sessions/{sid}", headers=api.headers).json()["state"]
    assert replay["events"][-1]["result"]["clinical_updates"] == result["response"]["clinical_updates"]


def _click(page, label):
    next(button for button in page.button if button.label == label).click().run()
    assert not page.exception, page.exception


def _student_page(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, owner="demo-student")
    sid = api.start("CASE-001")
    _route_requests(monkeypatch, api)
    page = _page(api)
    _click(page, "恢复训练")
    return api, sid, page


def test_switching_treatment_and_successful_execution_clear_confirmation(tmp_path, monkeypatch):
    api, sid, page = _student_page(tmp_path, monkeypatch)
    page.checkbox(key="treatment_confirmation").check().run()
    page.selectbox(key="treatment_choice").set_value("reperfusion_pathway").run()
    assert not page.checkbox(key="treatment_confirmation").value
    assert next(button for button in page.button if button.label == "提交治疗").disabled
    page.checkbox(key="treatment_confirmation").check().run()
    _click(page, "提交治疗")
    assert not page.checkbox(key="treatment_confirmation").value
    executions = [event for event in api.repository.get(sid, "demo-student").events if event.action.value == "execute_treatment"]
    assert len(executions) == 1 and executions[0].payload["treatment_id"] == "reperfusion_pathway"


@pytest.mark.parametrize("decision", ["继续编辑", "保存病历并返回", "放弃未保存内容并返回"])
def test_unsaved_notes_require_a_navigation_decision(tmp_path, monkeypatch, decision):
    api, sid, page = _student_page(tmp_path, monkeypatch)
    page.text_area(key="case_notes_input").set_value("尚未保存的病历草稿").run()
    _click(page, "返回病例库")
    assert page.session_state["session_id"] == sid
    assert page.text_area(key="case_notes_input").value == "尚未保存的病历草稿"
    _click(page, decision)
    stored = api.repository.get(sid, "demo-student")
    if decision == "继续编辑":
        assert page.session_state["session_id"] == sid
        assert page.text_area(key="case_notes_input").value == "尚未保存的病历草稿"
    else:
        assert page.session_state["session_id"] is None
        assert stored.case_notes == ("尚未保存的病历草稿" if decision == "保存病历并返回" else "")


def test_storage_releases_connections_without_garbage_collection(tmp_path):
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        database = tmp_path / "connections.db"
        repository = SessionRepository(database)
        state = SessionState(owner_id="demo-test", case_id="TEST-001", case_version=1)
        repository.create(state)
        assert repository.get(state.session_id, state.owner_id).session_id == state.session_id
        assert repository.list_for_owner(state.owner_id)
        state.state_version += 1
        repository.save(state, expected_version=0)
        database.unlink()
        assert not database.exists()
    finally:
        if was_enabled:
            gc.enable()


def test_relative_integration_paths_ignore_launch_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("INTEGRATION_DB_PATH", "backend/data/integration.db")
    monkeypatch.setenv("KNOWLEDGE_DIR", "knowledge")
    first = IntegrationSettings.from_env()
    monkeypatch.chdir(tmp_path)
    second = IntegrationSettings.from_env()
    assert first.integration_db_path == second.integration_db_path == ROOT / "backend/data/integration.db"
    assert first.knowledge_dir == second.knowledge_dir == ROOT / "knowledge"

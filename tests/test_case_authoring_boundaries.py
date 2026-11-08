"""Teacher identifier and question-scoring boundaries, using temporary stores only."""
import json

import pytest
from streamlit.testing.v1 import AppTest

from app.case_store import CaseStore, CaseStoreError
from app.integration.knowledge import KnowledgeStore
from app.integration.tools import TrustedToolService
from app.integration.turns import CANCELLED, TurnStore
from app.models import CaseDefinition

from test_cases_config import Api, ROOT, item
from test_frontend_smoke import _route_requests
from test_teacher_cases import TEACHER, new_case, put, step
from test_teacher_frontend import button, teacher_page, text


@pytest.mark.parametrize("case_id,public_id", [
    ("CASE-001", "CASE-ALIAS-A"), ("ALIAS-B", "STEMI-001"),
])
def test_cross_namespace_conflicts_are_reported_without_saving(tmp_path, monkeypatch, case_id, public_id):
    api = Api(tmp_path, monkeypatch)
    raw = new_case(case_id, public_id=public_id)
    checked = api.client.post("/api/teacher/cases/validate", headers=TEACHER, json=raw)
    assert checked.status_code == 200 and not checked.json()["valid"]
    assert any(i["code"] == "identifier_conflict" for i in checked.json()["issues"])
    assert put(api, raw).status_code == 409
    assert not (tmp_path / "teacher_cases").exists()
    sid = api.start("CASE-001")
    assert api.repository.get(sid, "demo-cfg").case_id == "STEMI-001"


def test_generated_public_id_skips_existing_internal_id(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    assert put(api, new_case("CASE-T100", public_id="CASE-OTHER")).status_code == 200
    saved = put(api, new_case("TEACH-NEXT")).json()
    assert saved["public_id"] == "CASE-T101"
    assert api.store.resolve_for_student("CASE-T100", True).case_id == "CASE-T100"


@pytest.mark.parametrize("location", ["fixture", "teacher"])
def test_conflicting_reload_retains_cache_and_can_recover(tmp_path, location):
    fixtures, teacher = tmp_path / "cases", tmp_path / "teacher"
    fixtures.mkdir()
    teacher.mkdir()
    original = CaseStore(ROOT / "cases").get("STEMI-001", 2)
    (fixtures / "original.json").write_text(original.model_dump_json(), encoding="utf-8")
    store = CaseStore(fixtures, teacher)
    invalid = teacher if location == "teacher" else fixtures
    path = invalid / "clash.json"
    path.write_text(CaseDefinition.model_validate(new_case("CASE-001", public_id="CASE-ALIAS")).model_dump_json(), encoding="utf-8")
    with pytest.raises(CaseStoreError):
        store.reload()
    assert store.get("STEMI-001", 2) == original
    assert store.list_all()[0]["source"] == "fixture" and len(store.list_all()) == 1
    with pytest.raises(CaseStoreError):
        CaseStore(fixtures, teacher)
    path.unlink()
    store.reload()
    assert store.resolve_for_student("CASE-001", True) == original


def test_historical_public_alias_remains_reserved_for_same_case(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    first = put(api, new_case(public_id="CASE-OLD")).json()
    sid = api.start(first["public_id"])
    assert put(api, new_case(public_id="CASE-NEW")).json()["version"] == 2
    assert put(api, new_case("OTHER-CASE", public_id="CASE-OLD")).status_code == 409
    restarted = CaseStore(ROOT / "cases", tmp_path / "teacher_cases")
    assert restarted.resolve_for_student("CASE-OLD", True).version == 2
    assert restarted.get("TEACH-001", 1).public_id == "CASE-OLD"
    assert api.repository.get(sid, "demo-cfg").case_version == 1


@pytest.mark.parametrize("invalid", ["collision", "json", "schema"])
def test_mcp_rejects_invalid_library_without_serving_cached_context(tmp_path, monkeypatch, invalid):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    service = TrustedToolService(TurnStore(tmp_path / "integration.db"), api.repository,
                               api.store, KnowledgeStore(ROOT / "knowledge"))

    def issue(request_id):
        state = api.repository.get(sid, "demo-cfg")
        return service.turns.issue(kind="patient", owner_id=state.owner_id, session_id=sid,
            case_id=state.case_id, case_version=state.case_version, request_id=request_id,
            state_version=state.state_version, candidate_fact_ids=["f_hx_htn"])

    turn, token = issue("before-conflict")
    teacher = tmp_path / "teacher_cases"
    teacher.mkdir()
    path = teacher / "conflict.json"
    path.write_text(CaseDefinition.model_validate(new_case("CASE-001", public_id="CASE-ALIAS")).model_dump_json(), encoding="utf-8")
    if invalid != "collision":
        path.write_text("{invalid" if invalid == "json" else "{}", encoding="utf-8")
    result = service.get_turn_context(token)
    assert result == {"ok": False, "error": "case_invalid", "message": "case library is not available"}
    assert service.turns.resolve(token, kind="patient", allow=(CANCELLED,)).status == CANCELLED
    assert service.turns.calls(turn.turn_id)[-1]["detail"] == "case_invalid"
    path.unlink()
    _, next_token = issue("after-recovery")
    assert service.get_turn_context(next_token)["ok"]
    assert service.get_turn_context(token)["error"] == "turn_cancelled"


@pytest.mark.parametrize("change,field", [
    ({"patient_known": False}, "patient_known"), ({"current": False}, "current"),
    ({"disclosure_triggers": [" \t\u3000\n"]}, "disclosure_triggers"),
    ({"status_valid_from": -10, "status_valid_to": -1}, "status_valid_to"),
])
def test_unobtainable_scored_facts_fail_validate_and_save(tmp_path, monkeypatch, change, field):
    api = Api(tmp_path, monkeypatch)
    raw = new_case(public_id="CASE-BOUNDARY")
    raw["facts"][1].update(change)
    checked = api.client.post("/api/teacher/cases/validate", headers=TEACHER, json=raw)
    assert checked.status_code == 200 and not checked.json()["valid"]
    assert any(i["code"] == "unreachable_rubric_fact" and field in i["message"] for i in checked.json()["issues"])
    assert put(api, raw).status_code == 422
    assert not (tmp_path / "teacher_cases").exists()


def test_stored_invalid_validated_case_cannot_be_submitted_but_can_be_withdrawn(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    assert put(api, new_case(public_id="CASE-BOUNDARY")).status_code == 200
    assert step(api, "TEACH-001", 1, "validate").status_code == 200
    path = tmp_path / "teacher_cases/TEACH-001__v1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["facts"][1]["patient_known"] = False
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    api.store.reload()
    assert step(api, "TEACH-001", 1, "submit-review").status_code == 422
    assert api.store.get("TEACH-001", 1).review_status.value == "validated"
    assert step(api, "TEACH-001", 1, "withdraw").status_code == 200


def test_future_window_is_askable_at_boundary_and_non_scored_hidden_fact_is_allowed(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    raw = new_case(public_id="CASE-BOUNDARY")
    raw["facts"][1].update(status_valid_from=5, status_valid_to=5, disclosure_triggers=[" 哪 里 痛 "])
    raw["facts"][0].update(patient_known=False, current=False, auto_volunteer=False)
    checked = api.client.post("/api/teacher/cases/validate", headers=TEACHER, json=raw)
    assert checked.json()["valid"] is True
    assert put(api, raw).status_code == 200
    sid = api.start("CASE-BOUNDARY")
    api.ask(sid, "哪里痛")
    assert "f_pain_migration" not in api.repository.get(sid, "demo-cfg").disclosed_fact_ids
    api.wait(sid, 5)
    api.ask(sid, "哪里痛")
    assert "f_pain_migration" in api.repository.get(sid, "demo-cfg").disclosed_fact_ids
    assert item(api.seal_and_score(sid), "f_pain_migration")["hit"]


def test_student_page_starts_the_selected_teacher_case(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, owner="demo-student")
    assert put(api, new_case("TEACH-SELECT", public_id="CASE-SELECT")).status_code == 200
    _route_requests(monkeypatch, api)
    page = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=30).run()
    select = next(s for s in page.selectbox if s.label == "病例")
    select.set_value(next(o for o in select.options if o.startswith("CASE-SELECT ·")))
    button(page, "开始训练").click().run()
    assert not page.exception, page.exception
    state = api.repository.get(page.session_state["session_id"], "demo-student")
    assert state.case_id == "TEACH-SELECT" and state.case_version == 1


def test_teacher_page_shows_unreachable_fact_reason_and_does_not_save(tmp_path, monkeypatch):
    api, page = teacher_page(tmp_path, monkeypatch)
    text(page, "病例编号").set_value("TEACH-INVALID-UI")
    page.checkbox(key="case_editor_advanced").check()
    raw = json.loads(page.text_area(key="case_editor_facts").value)
    scored = next(r["item_id"] for r in page.session_state["teacher_case"]["rubric"] if r["evidence_type"] == "ask_event")
    next(f for f in raw if f["fact_id"] == scored)["patient_known"] = False
    page.text_area(key="case_editor_facts").set_value(json.dumps(raw, ensure_ascii=False))
    button(page, "保存新版本草稿").click().run()
    assert not page.exception, page.exception
    assert any("patient_known" in x.value for x in page.error)
    assert api.store.versions("TEACH-INVALID-UI") == []

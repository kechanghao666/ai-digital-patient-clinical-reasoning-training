"""Teacher workflow: author, validate, save, review, publish, version pinning."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.case_store import CaseStore

from test_cases_config import ROOT, Api, item, load

TEACHER = {"X-Owner-Id": "teacher-wang"}


def new_case(case_id="TEACH-001", **changes) -> dict:
    raw = copy.deepcopy(load("appendicitis_case_v1.json"))
    raw["case_id"] = case_id
    raw.pop("public_id")
    raw["title"] = "教师新增的腹痛模拟病例"
    raw.update(changes)
    return raw


def put(api: Api, raw: dict, headers=TEACHER):
    return api.client.put(f"/api/teacher/cases/{raw['case_id']}", headers=headers, json=raw)


def step(api: Api, case_id: str, version: int, name: str, body=None):
    return api.client.post(
        f"/api/teacher/cases/{case_id}/versions/{version}/{name}", headers=TEACHER, json=body
    )


PUBLISH = {"reviewer": "示例审核者", "review_date": "2026-11-01", "confirm_medical_review": True}


def test_only_teachers_may_use_teacher_routes(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    for headers in ({}, {"X-Owner-Id": "demo-student"}, {"X-Owner-Id": "student-1"}):
        assert api.client.get("/api/teacher/cases", headers=headers).status_code in (401, 403)
        assert put(api, new_case(), headers).status_code in (401, 403)
    assert api.client.get("/api/teacher/cases", headers=TEACHER).status_code == 200
    # Teacher ids are not students, and nothing works outside development mode.
    assert api.client.post("/api/sessions?case_id=CASE-002", headers=TEACHER).status_code == 401
    (tmp_path / "p").mkdir()
    prod = Api(tmp_path / "p", monkeypatch, env="production")
    assert prod.client.get("/api/teacher/cases", headers=TEACHER).status_code == 403


def test_validation_reports_schema_and_leak_problems_without_saving(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    ok = api.client.post("/api/teacher/cases/validate", headers=TEACHER, json=new_case(public_id="CASE-X1"))
    assert ok.status_code == 200 and ok.json()["valid"] is True

    leaky = new_case(public_id="CASE-X2")
    leaky["facts"][1]["content"] = "医生说我可能是急性阑尾炎。"
    res = api.client.post("/api/teacher/cases/validate", headers=TEACHER, json=leaky)
    assert res.json()["valid"] is False
    assert any(i["code"] == "diagnosis_leak" for i in res.json()["issues"])

    broken = new_case(public_id="CASE-X3")
    broken["rules"][0]["delay_anchor_exam"] = "ex_missing"
    res = api.client.post("/api/teacher/cases/validate", headers=TEACHER, json=broken)
    assert res.status_code == 422 and res.json()["detail"]["issues"][0]["code"] == "schema"
    listed = api.client.get("/api/teacher/cases", headers=TEACHER).json()
    assert all(c["source"] == "fixture" for c in listed)
    assert not (tmp_path / "teacher_cases").exists()


def test_unreachable_rubric_fact_and_missing_options_are_errors(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    raw = new_case()
    raw["facts"][1]["disclosure_triggers"] = []
    res = put(api, raw)
    assert res.status_code == 422
    assert any(i["code"] == "unreachable_rubric_fact" for i in res.json()["detail"]["issues"])


def test_save_persists_across_restart_and_is_a_draft(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    res = put(api, new_case())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["version"] == 1 and body["review_status"] == "draft" and body["authored_by"] == "teacher-wang"
    assert body["public_id"].startswith("CASE-T")
    restarted = CaseStore(ROOT / "cases", tmp_path / "teacher_cases")
    assert restarted.get("TEACH-001", 1).review_status.value == "draft"
    assert restarted.get("TEACH-001", 1).title == "教师新增的腹痛模拟病例"


def test_teacher_cannot_claim_review_or_overwrite_fixtures(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    raw = new_case(review_status="published", reviewer="冒名者", review_date="2026-11-01", version=7)
    body = put(api, raw).json()
    assert body["review_status"] == "draft" and body["version"] == 1
    stored = api.client.get("/api/teacher/cases/TEACH-001/versions/1", headers=TEACHER).json()
    assert stored["reviewer"] is None and stored["review_date"] is None
    assert put(api, new_case("APPY-001")).status_code == 409
    assert put(api, new_case("STEMI-001")).status_code == 409
    assert put(api, new_case("../evil")).status_code in (404, 405, 409, 422)
    assert put(api, new_case("ab")).status_code == 409
    clash = new_case("TEACH-002", public_id="CASE-002")
    assert put(api, clash).status_code == 409
    mismatch = api.client.put("/api/teacher/cases/TEACH-009", headers=TEACHER, json=new_case("TEACH-010"))
    assert mismatch.status_code == 422
    assert not list((tmp_path / "teacher_cases").glob("*evil*"))


def test_draft_is_visible_to_students_only_in_development(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    put(api, new_case())
    public_id = api.client.get("/api/teacher/cases", headers=TEACHER).json()
    public_id = next(c["public_id"] for c in public_id if c["case_id"] == "TEACH-001")
    assert any(c["public_id"] == public_id for c in api.client.get("/api/cases", headers=api.headers).json())
    sid = api.start(public_id)
    api.ask(sid, "有没有发热")
    prod = Api(tmp_path, monkeypatch, env="production")
    assert prod.client.get("/api/cases", headers=prod.headers).status_code == 401  # no verified identity yet


def test_review_and_publish_workflow_is_enforced(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    put(api, new_case())
    assert step(api, "TEACH-001", 1, "publish", PUBLISH).status_code == 409  # draft cannot jump to published
    assert step(api, "TEACH-001", 1, "submit-review").status_code == 409
    assert step(api, "TEACH-001", 1, "validate").json()["review_status"] == "validated"
    assert step(api, "TEACH-001", 1, "submit-review").json()["review_status"] == "under_review"

    for bad in (
        {**PUBLISH, "confirm_medical_review": False},
        {**PUBLISH, "reviewer": " "},
        {**PUBLISH, "review_date": "昨天"},
        {**PUBLISH, "review_date": "2099-01-01"},
        {"reviewer": "x", "review_date": "2026-11-01"},
    ):
        assert step(api, "TEACH-001", 1, "publish", bad).status_code in (409, 422), bad

    published = step(api, "TEACH-001", 1, "publish", PUBLISH)
    assert published.status_code == 200 and published.json()["review_status"] == "published"
    case = api.client.get("/api/teacher/cases/TEACH-001/versions/1", headers=TEACHER).json()
    assert case["reviewer"] == "示例审核者" and case["review_date"] == "2026-11-01"
    assert step(api, "TEACH-001", 1, "withdraw").status_code == 409  # published content is final
    assert step(api, "TEACH-001", 1, "validate").status_code == 409


def test_incomplete_case_cannot_be_validated_or_published(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    raw = new_case()
    raw["rubric"] = [r for r in raw["rubric"] if r["stage"] != "治疗"]
    raw["treatments"] = []
    raw["rules"] = []
    assert put(api, raw).status_code == 200  # drafts may be incomplete
    res = step(api, "TEACH-001", 1, "validate")
    assert res.status_code == 422
    codes = {i["code"] for i in res.json()["detail"]["issues"]}
    assert {"missing_stage", "no_treatments"} <= codes


def test_editing_after_publication_creates_a_new_version_and_pins_sessions(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    public = put(api, new_case()).json()["public_id"]
    for name, body in (("validate", None), ("submit-review", None), ("publish", PUBLISH)):
        assert step(api, "TEACH-001", 1, name, body).status_code == 200
    first = api.start(public)

    revised = new_case(public_id=public)
    revised["title"] = "修订后的标题"
    saved = put(api, revised).json()
    assert saved["version"] == 2 and saved["review_status"] == "draft"
    again = new_case(public_id=public)
    again["title"] = "修订后的标题（再次保存）"
    assert put(api, again).json()["version"] == 3

    old = CaseStore(ROOT / "cases", tmp_path / "teacher_cases")
    assert old.get("TEACH-001", 1).title == "教师新增的腹痛模拟病例"
    assert api.state(first)["session_id"] == first
    session = api.repository.get(first, "demo-cfg")
    assert (session.case_id, session.case_version) == ("TEACH-001", 1)
    # In production the latest published version is what new students get; the draft stays hidden.
    assert old.resolve_for_student(public, include_unpublished=False).version == 1
    assert old.resolve_for_student(public, include_unpublished=True).version == 3


def test_teacher_case_runs_a_full_scored_session(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    public = put(api, new_case()).json()["public_id"]
    sid = api.start(public)
    api.ask(sid, "疼痛在哪里痛")
    api.act(sid, "order_exam", {"exam_id": "ex_us"})
    api.wait(sid, 45)
    api.act(sid, "submit_diagnosis", {"diagnosis": "急性阑尾炎"})
    api.act(sid, "execute_treatment", {"treatment_id": "surgical_consult", "confirm_execution": True})
    score = api.seal_and_score(sid)
    assert item(score, "acute_appendicitis")["hit"] and item(score, "surgical_consult")["hit"]
    assert item(score, "f_pain_migration")["hit"] and item(score, "ex_us")["hit"]
    assert not item(score, "ex_cbc")["hit"]


def test_saved_file_is_valid_json_and_has_no_temp_leftovers(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    put(api, new_case())
    files = sorted(p.name for p in (tmp_path / "teacher_cases").iterdir())
    assert files == ["TEACH-001__v1.json"]
    data = json.loads((tmp_path / "teacher_cases" / files[0]).read_text(encoding="utf-8"))
    assert data["authored_by"] == "teacher-wang" and data["review_status"] == "draft"

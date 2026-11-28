"""Students choose exams from a hospital-wide catalogue instead of picking from the case's answers."""
from __future__ import annotations

import json

from app import exam_catalog
from app.case_authoring import check_case, has_errors
from app.case_store import CaseStore
from app.scoring import STEWARDSHIP_ITEM

from test_cases_config import ROOT, Api, item, load
from app.models import CaseDefinition


def _id(name: str) -> str:
    return next(e.catalog_id for e in exam_catalog.CATALOG if e.name == name)


def test_every_case_sees_the_identical_catalogue_and_every_case_exam_is_reachable(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    menus = []
    for public_id in ("CASE-001", "CASE-002", "CASE-003", "CASE-004", "CASE-005"):
        sid = api.start(public_id)
        menus.append(api.client.get(f"/api/sessions/{sid}", headers=api.headers).json()["options"]["exams"])
    assert all(menu == menus[0] for menu in menus)
    assert all(set(e) == {"exam_id", "name", "category", "cost", "minutes", "baseline"} for e in menus[0])
    store = CaseStore(ROOT / "cases")
    for case in store._cases.values():
        assert exam_catalog.unmapped_case_exams(case) == [], case.case_id


def test_catalogue_order_returns_the_case_result_by_alias(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-002")  # appendicitis: case calls it 腹部超声
    api.act(sid, "order_exam", {"exam_id": _id("腹部超声")})
    api.wait(sid, 60)  # the case's own turnaround (45 min) applies, not the catalogue default
    order = api.state(sid)["orders"][0]
    assert order["exam_name"] == "腹部超声" and order["state"] == "ready"
    assert order["result"] != exam_catalog.GENERIC_RESULT
    assert order["category"] == "影像" and order["cost"] > 0


def test_unrelated_order_returns_a_neutral_result_and_leaks_nothing(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    api.act(sid, "order_exam", {"exam_id": _id("肠镜")})
    api.wait(sid, 120)
    state = api.state(sid)
    assert state["orders"][0]["result"] == exam_catalog.GENERIC_RESULT
    text = json.dumps(state, ensure_ascii=False)
    assert "心肌梗死" not in text and "心梗" not in text


def test_catalogue_order_is_recorded_under_the_case_exam_id_and_replays(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    body = {"request_id": "same", "action": "order_exam", "payload": {"exam_id": _id("心电图")}}
    first = api.client.post(f"/api/sessions/{sid}/action", headers=api.headers, json=body)
    again = api.client.post(f"/api/sessions/{sid}/action", headers=api.headers, json=body)
    assert first.status_code == again.status_code == 200
    assert len(api.state(sid)["orders"]) == 1
    # Ordering the same exam twice while pending is refused, whichever id the student used.
    api.act(sid, "order_exam", {"exam_id": "ex_ecg"}, expect=400)


def test_unknown_exam_id_is_rejected(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    api.act(sid, "order_exam", {"exam_id": "cat_999"}, expect=400)


def test_case_rubric_still_credits_catalogue_orders(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    api.act(sid, "order_exam", {"exam_id": _id("心电图")})
    api.act(sid, "order_exam", {"exam_id": _id("肌钙蛋白")})
    score = api.seal_and_score(sid)
    assert item(score, "ex_ecg")["hit"] and item(score, "ex_troponin")["hit"]
    stewardship = item(score, STEWARDSHIP_ITEM)
    assert stewardship["score"] == 1.0 and stewardship["stage"] == "检查选择"


def test_unnecessary_orders_lose_stewardship_points_and_show_cost(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    for name in ("心电图", "头颅MRI", "肠镜", "肿瘤标志物"):
        api.act(sid, "order_exam", {"exam_id": _id(name)})
    stewardship = item(api.seal_and_score(sid), STEWARDSHIP_ITEM)
    assert stewardship["score"] == 0.25
    assert "头颅MRI" in stewardship["reason"] and "元" in stewardship["reason"]
    assert stewardship["improvement"]


def test_routine_baseline_work_up_is_costed_but_not_penalised(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    for name in ("心电图", "血常规", "电解质", "肾功能"):
        api.act(sid, "order_exam", {"exam_id": _id(name)})
    assert api.state(sid)["exam_cost_total"] == sum(e.cost for e in exam_catalog.CATALOG if e.name in {"心电图", "血常规", "电解质", "肾功能"})
    assert item(api.seal_and_score(sid), STEWARDSHIP_ITEM)["score"] == 1.0


def test_case_can_declare_reasonable_but_unrequired_exams(tmp_path, monkeypatch):
    raw = load("stemi_case_v3.json")
    raw["acceptable_exams"] = ["胸部X线", "D-二聚体"]
    case = CaseDefinition.model_validate(raw)
    from app.scoring import _exam_stewardship
    from app.models import ActionKind, Event

    def order(name, seq):
        return Event(request_id=f"r{seq}", seq=seq, actor="student", action=ActionKind.ORDER_EXAM,
                     payload={"exam_id": _id(name)}, result={}, sim_time=0,
                     state_before={}, state_after={})
    assert _exam_stewardship([order("胸部X线", 1), order("D-二聚体", 2)], case).score == 1.0
    assert _exam_stewardship([order("头颅MRI", 1)], case).score == 0.75


def test_no_orders_earns_no_stewardship_credit(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    assert item(api.seal_and_score(sid), STEWARDSHIP_ITEM)["score"] == 0.0


def test_authoring_rejects_case_exams_outside_the_catalogue():
    raw = load("appendicitis_case_v2.json")
    raw["exams"][1]["standard_name"] = "某种不存在的检查"
    issues = check_case(CaseDefinition.model_validate(raw))
    assert has_errors(issues)
    assert any(i.code == "exam_not_in_catalog" for i in issues)

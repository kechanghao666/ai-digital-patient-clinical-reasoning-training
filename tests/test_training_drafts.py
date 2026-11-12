"""Unsubmitted reasoning must not disappear when sealing or leaving training."""
import pytest
import httpx
import requests

from test_cases_config import Api
from test_frontend_smoke import _page, _route_requests


def button(page, label):
    return next(b for b in page.button if b.label == label)


@pytest.mark.parametrize("public_id", [f"CASE-00{i}" for i in range(1, 6)])
@pytest.mark.parametrize("field", ["diagnosis", "differential"])
def test_reasoning_draft_blocks_sealing_and_accidental_return(tmp_path, monkeypatch, public_id, field):
    api = Api(tmp_path, monkeypatch, owner="demo-student")
    sid = api.start(public_id)
    _route_requests(monkeypatch, api)
    page = _page(api)
    button(page, "恢复训练").click().run()
    if field == "diagnosis":
        page.text_input(key="diagnosis_input").set_value("待核对的判断").run()
        submit = "提交诊断"
    else:
        case = api.store.resolve_for_student(public_id, include_unpublished=True)
        page.multiselect(key="differential_choice").set_value([case.differential_options[0].candidate_id]).run()
        submit = "提交鉴别诊断"
    assert button(page, "提交训练并封存").disabled
    button(page, "返回病例库").click().run()
    assert page.session_state["session_id"] == sid
    assert button(page, "保存病历并返回").disabled
    button(page, "继续编辑").click().run()
    button(page, submit).click().run()
    assert not page.exception
    assert not button(page, "提交训练并封存").disabled
    button(page, "提交训练并封存").click().run()
    assert api.repository.get(sid, "demo-student").sealed


def test_restored_multiple_evidence_is_clean_but_edits_are_protected(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, owner="demo-student")
    sid = api.start("CASE-001")
    api.differential(sid, [{"candidate_id": "pulmonary_embolism",
        "supporting_evidence": ["已记录的第一项", "已记录的第二项"],
        "opposing_evidence": [], "evidence_event_ids": []}])
    _route_requests(monkeypatch, api)
    page = _page(api)
    button(page, "恢复训练").click().run()
    assert not button(page, "提交训练并封存").disabled
    page.text_input(key="ddx_support_pulmonary_embolism").set_value("尚未提交的修订").run()
    assert button(page, "提交训练并封存").disabled
    button(page, "返回病例库").click().run()
    button(page, "放弃未保存内容并返回").click().run()
    button(page, "恢复训练").click().run()
    assert page.text_input(key="ddx_support_pulmonary_embolism").value == "已记录的第一项、已记录的第二项"
    assert not button(page, "提交训练并封存").disabled
    page.multiselect(key="differential_choice").set_value([]).run()
    assert button(page, "提交训练并封存").disabled
    assert not api.repository.get(sid, "demo-student").sealed


@pytest.mark.parametrize("field", ["diagnosis", "differential"])
def test_failed_reasoning_submission_keeps_draft_protection(tmp_path, monkeypatch, field):
    api = Api(tmp_path, monkeypatch, owner="demo-student")
    sid = api.start("CASE-001")
    _route_requests(monkeypatch, api)
    page = _page(api)
    button(page, "恢复训练").click().run()
    if field == "diagnosis":
        page.text_input(key="diagnosis_input").set_value("待核对的判断").run()
        label = "提交诊断"
    else:
        page.multiselect(key="differential_choice").set_value(["pulmonary_embolism"]).run()
        label = "提交鉴别诊断"
    routed = requests.request

    def unavailable(method, url, **kwargs):
        if method == "POST" and url.endswith("/" + field):
            return httpx.Response(503, json={"detail": "暂时不可用"})
        return routed(method, url, **kwargs)

    monkeypatch.setattr(requests, "request", unavailable)
    button(page, label).click().run()
    assert not page.exception
    assert button(page, "提交训练并封存").disabled
    assert not api.repository.get(sid, "demo-student").sealed
    assert not api.repository.get(sid, "demo-student").events
    _route_requests(monkeypatch, api)
    button(page, label).click().run()
    assert not button(page, "提交训练并封存").disabled

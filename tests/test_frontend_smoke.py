"""Render the Streamlit page against the API (routed in-process) for both specialties."""
from __future__ import annotations

import requests
import pytest
from streamlit.testing.v1 import AppTest

from test_cases_config import ROOT, Api


@pytest.mark.parametrize("submission", ["diagnosis", "differential"])
def test_submission_immediately_refreshes_count_and_replay(tmp_path, monkeypatch, submission):
    from test_integration import Env

    env = Env(tmp_path, monkeypatch, mode="simulator")
    sid = env.new_session()
    _route_requests(monkeypatch, env)
    page = _page(env)
    page.text_input(key="student_identity").set_value("demo-integration").run()
    next(b for b in page.button if b.label == "恢复训练").click().run()
    if submission == "diagnosis":
        page.text_input(key="diagnosis_input").set_value("待进一步核对").run()
        label = "提交诊断"
    else:
        page.multiselect(key="differential_choice").set_value(["pulmonary_embolism"]).run()
        label = "提交鉴别诊断"
    next(b for b in page.button if b.label == label).click().run()
    assert not page.exception
    assert len(env.stored_events(sid)) == 1
    assert next(m for m in page.metric if m.label == "已记录操作").value == "1"
    assert len(page.session_state["session_snapshot"]["state"]["events"]) == 1


def _route_requests(monkeypatch, api: Api):
    def fake_request(method, url, headers=None, timeout=None, **kwargs):
        path = url.split("127.0.0.1:8000", 1)[1]
        return api.client.request(method, path, headers=headers, **kwargs)

    monkeypatch.setattr(requests, "request", fake_request)


def test_unsaved_notes_must_be_saved_before_sealing(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, owner="demo-student")
    sid = api.start("CASE-001")
    _route_requests(monkeypatch, api)
    page = _page(api)
    next(b for b in page.button if b.label == "恢复训练").click().run()
    page.text_area(key="case_notes_input").set_value("本轮已采集病史，待核对检查。").run()
    assert next(b for b in page.button if b.label == "提交训练并封存").disabled
    next(b for b in page.button if b.label == "保存病历记录").click().run()
    assert not next(b for b in page.button if b.label == "提交训练并封存").disabled
    next(b for b in page.button if b.label == "提交训练并封存").click().run()
    assert not page.exception
    state = api.client.get(f"/api/sessions/{sid}", headers=api.headers).json()["state"]
    assert state["sealed"] and state["case_notes"] == "本轮已采集病史，待核对检查。"


def _page(api: Api):
    app = AppTest.from_file(str(ROOT / "frontend" / "app.py"), default_timeout=30)
    app.run()
    return app


def test_page_offers_case_configured_options_for_each_specialty(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, owner="demo-student")
    _route_requests(monkeypatch, api)
    for public_id, exams, treatments in (
        ("CASE-001", ["心电图", "肌钙蛋白"], ["阿司匹林", "再灌注路径"]),
        ("CASE-002", ["血常规", "腹部超声", "尿常规"], ["禁食、补液并使用抗生素", "请普外科会诊并准备手术"]),
    ):
        sid = api.start(public_id)
        app = _page(api)
        assert not app.exception, app.exception
        # Resume the session created above.
        resume = [b for b in app.button if b.label == "恢复训练"][0]
        resume.click().run()
        assert not app.exception, app.exception
        labels = {s.label: s for s in app.selectbox}
        assert [labels["检查项目"].format_func(o) for o in labels["检查项目"].options] == exams
        assert [labels["治疗动作"].format_func(o) for o in labels["治疗动作"].options] == treatments
        assert any(m.label == "候选诊断" for m in app.multiselect)


def test_sealed_page_restores_feedback_and_shows_readable_replay(tmp_path, monkeypatch):
    from test_integration import Env, _play_and_seal

    env = Env(tmp_path, monkeypatch, mode="simulator")
    sid = _play_and_seal(env)
    response = env.client.post(f"/api/sessions/{sid}/feedback", headers=env.headers,
                              json={"request_id": "feedback"})
    assert response.status_code == 200, response.text
    _route_requests(monkeypatch, env)
    page = _page(env)
    page.text_input(key="student_identity").set_value("demo-integration").run()
    next(b for b in page.button if b.label == "恢复训练").click().run()
    assert not page.exception, page.exception
    assert len(page.session_state["feedback_by_session"][sid]["items"]) == 5
    assert not page.json
    assert any("提交诊断：" in item.value for item in page.markdown)
    assert all(b.disabled for b in page.button if b.label in {"提交问题", "提交诊断", "提交治疗"})


def test_specialty_filter_starts_the_visible_case(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, owner="demo-student")
    _route_requests(monkeypatch, api)
    page = _page(api)
    target = next(case for case in api.client.get('/api/cases', headers=api.headers).json() if case['public_id'] == 'CASE-002')
    page.selectbox(key="library_specialty").set_value(target['specialty']).run()
    choices = next(s for s in page.selectbox if s.label == "病例")
    assert len(choices.options) == 1 and "CASE-002" in choices.options[0]
    next(b for b in page.button if b.label == "开始训练").click().run()
    assert not page.exception
    assert page.session_state['session_snapshot']['case']['public_id'] == 'CASE-002'


def test_replay_filter_preserves_event_numbers_and_does_not_mutate(tmp_path, monkeypatch):
    from test_integration import Env, _play_and_seal
    env = Env(tmp_path, monkeypatch, mode="simulator")
    sid = _play_and_seal(env)
    before = env.stored_events(sid)
    _route_requests(monkeypatch, env)
    page = _page(env)
    page.text_input(key="student_identity").set_value("demo-integration").run()
    next(b for b in page.button if b.label == "恢复训练").click().run()
    page.selectbox(key=f"replay_action_{sid}").set_value("submit_diagnosis").run()
    assert not page.exception
    headings = [item.label for item in page.expander if item.label.startswith('#')]
    expected = [f"#{e.seq} · 提交诊断 · 模拟时间 {e.sim_time} 分钟" for e in before if e.action == 'submit_diagnosis']
    assert headings == expected and headings
    assert env.stored_events(sid) == before
    page.selectbox(key=f"replay_action_{sid}").set_value("all").run()
    assert len([item for item in page.expander if item.label.startswith('#')]) == len(before)

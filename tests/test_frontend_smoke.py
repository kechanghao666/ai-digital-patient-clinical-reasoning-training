"""Render the Streamlit page against the API (routed in-process) for both specialties."""
from __future__ import annotations

import requests
from streamlit.testing.v1 import AppTest

from test_cases_config import ROOT, Api


def _route_requests(monkeypatch, api: Api):
    def fake_request(method, url, headers=None, timeout=None, **kwargs):
        path = url.split("127.0.0.1:8000", 1)[1]
        return api.client.request(method, path, headers=headers, **kwargs)

    monkeypatch.setattr(requests, "request", fake_request)


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

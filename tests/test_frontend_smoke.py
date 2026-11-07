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

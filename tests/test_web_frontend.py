"""The built React front end is served by the API process without shadowing API routes."""
from fastapi.testclient import TestClient

from app import main
from app.disclosure import detect_intents
from test_cases_config import ROOT


def test_root_serves_built_spa_and_api_routes_still_win():
    assert (ROOT / "web/dist/index.html").is_file(), "run `node build.mjs` in web/ and commit dist/"
    client = TestClient(main.app)
    page = client.get("/")
    assert page.status_code == 200 and '<div id="root">' in page.text
    assert client.get("/assets/main.js").status_code == 200
    assert client.get("/health").json()["status"] == "ok"
    # API errors stay JSON, never the SPA shell.
    missing = client.get("/api/sessions/does-not-exist", headers={"X-Owner-Id": "demo-web"})
    assert missing.headers["content-type"].startswith("application/json")


def test_built_bundle_has_no_hidden_case_data():
    bundle = (ROOT / "web/dist/assets/main.js").read_text(encoding="utf-8")
    for term in ("hidden_diagnosis\":", "阑尾炎", "心肌梗死", "酮症酸中毒", "缺血性卒中", "社区获得性肺炎"):
        assert term not in bundle


def test_self_introduction_before_opening_question_counts_as_opening():
    assert detect_intents("您好，我是今天接诊的医生，请问哪里不舒服？")[0] is True
    assert detect_intents("您好，我是今天接诊的医生")[0] is False

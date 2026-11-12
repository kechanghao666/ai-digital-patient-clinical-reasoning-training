"""Model drafts remain teacher-only until an audited review is saved."""
import copy

import pytest
from streamlit.testing.v1 import AppTest

from test_feedback_contract import sealed
from test_feedback_reviews import payload, read, save
from test_frontend_smoke import _route_requests
from test_integration import ROOT


@pytest.mark.parametrize("legacy", [False, True])
def test_model_draft_requires_review_for_both_student_endpoints(sealed, legacy):
    env, state, case, items = sealed
    draft = copy.deepcopy(items)
    draft[0]["comment"] = "诊断基本准确，治疗选择合理。"
    store = env.runtime.feedback_store
    snapshot = None if legacy else env.runtime.knowledge.snapshot(draft, case.case_id, case.version)
    store.save_once(state.session_id, state.owner_id, "nexent", draft, "draft_unreviewed", {}, snapshot)
    original = store.get(state.session_id, state.owner_id)
    before = env.repository.get(state.session_id, state.owner_id)
    endpoint = f"/api/sessions/{state.session_id}/feedback"
    for method in ("GET", "POST"):
        kwargs = {"json": {"request_id": "repeat"}} if method == "POST" else {}
        r = env.client.request(method, endpoint, headers=env.headers, **kwargs)
        assert r.status_code == 200
        assert r.json()["review_status"] == "pending_teacher_review"
        assert r.json()["items"] == [] and r.json()["teaching_basis"] == []
        assert "诊断基本准确" not in r.text and "治疗选择合理" not in r.text
    assert env.client.get(endpoint, headers={"X-Owner-Id": "demo-other"}).status_code == 404
    assert read(env, state).json()["editable_items"] == draft
    assert save(env, state, payload(state, items)).status_code == 200
    shown = env.client.get(endpoint, headers=env.headers).json()
    assert shown["review_status"] == "teacher_reviewed"
    assert shown["items"] == items and shown["source"] == "teacher_review"
    assert store.get(state.session_id, state.owner_id) == original
    assert env.repository.get(state.session_id, state.owner_id) == before
    assert not env.runs


def test_student_page_hides_old_cached_model_draft_then_shows_review(sealed, monkeypatch):
    env, state, _, items = sealed
    draft = copy.deepcopy(items)
    draft[0]["comment"] = "诊断基本准确，治疗选择合理。"
    env.runtime.feedback_store.save_once(state.session_id, state.owner_id, "nexent", draft, "draft_unreviewed", {})
    _route_requests(monkeypatch, env)
    page = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=30).run()
    page.text_input(key="student_identity").set_value(state.owner_id).run()
    next(b for b in page.button if b.label == "恢复训练").click().run()
    page.session_state["feedback_by_session"][state.session_id] = {
        "source": "nexent", "items": draft, "review_revision": 0, "teaching_basis": [],
    }
    page.run()
    assert not page.exception
    assert any("待教师复核" in w.value for w in page.warning)
    assert all("诊断基本准确" not in m.value for m in page.markdown)
    assert save(env, state, payload(state, items)).status_code == 200
    next(b for b in page.button if b.label == "刷新已存反馈与教师复核").click().run()
    assert not page.exception
    assert any(items[0]["comment"] in m.value for m in page.markdown)

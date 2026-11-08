"""Teacher commentary revisions are audited without rewriting sealed clinical evidence."""
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from streamlit.testing.v1 import AppTest

from app.integration.feedback import FeedbackStore
from app.integration.knowledge import KnowledgeStore
from app.integration.tools import FEEDBACK_STAGES
from app.storage import ConcurrentUpdateError
from test_feedback_contract import sealed
from test_frontend_smoke import _route_requests
from test_integration import Env, ROOT

TEACHER = {"X-Owner-Id": "teacher-reviewer"}


def payload(state, items, **changes):
    snapshot = KnowledgeStore(ROOT / "knowledge").snapshot(items, state.case_id, state.case_version)
    return {"owner_id": state.owner_id, "request_id": "review-1", "expected_revision": 0,
            "reason": "补齐五环节评语并核对引用。", "items": copy.deepcopy(items),
            "basis_hashes": {entry["basis_id"]: entry["content_hash"] for entry in snapshot["entries"]}, **changes}


def read(env, state, headers=TEACHER):
    return env.client.get(f"/api/teacher/feedback/{state.session_id}", headers=headers,
                          params={"owner_id": state.owner_id})


def save(env, state, body, headers=TEACHER):
    return env.client.post(f"/api/teacher/feedback/{state.session_id}", headers=headers, json=body)


def test_teacher_repairs_blocked_feedback_preserving_original_and_session(sealed):
    env, state, _, items = sealed
    old = copy.deepcopy(items)
    old[0]["comment"] = "参考答案为心肌梗死"
    store = env.runtime.feedback_store
    store.save_once(state.session_id, state.owner_id, "local_rules", old, "draft_unreviewed", {"secret": "private-trace"})
    original = store.get(state.session_id, state.owner_id)
    before = env.repository.get(state.session_id, state.owner_id).model_dump(mode="json")
    assert read(env, state).json()["original_validation_error"]
    assert env.client.get(f"/api/sessions/{state.session_id}/feedback", headers=env.headers).status_code == 409

    response = save(env, state, payload(state, items, reason="教师内部说明含参考诊断心肌梗死"))
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    for method in ("GET", "POST"):
        kwargs = {"json": {"request_id": "regenerate"}} if method == "POST" else {}
        shown = env.client.request(method, f"/api/sessions/{state.session_id}/feedback", headers=env.headers, **kwargs)
        assert shown.status_code == 200, shown.text
        assert shown.json()["source"] == "teacher_review" and shown.json()["review_revision"] == 1
        assert not any(text in shown.text for text in ("心肌梗死", "teacher-reviewer", "private-trace", "教师内部说明"))
    record = read(env, state).json()
    assert record["validation_error"] is None and record["original_validation_error"]
    assert record["reviews"][0]["teacher_id"] == "teacher-reviewer"
    assert record["reviews"][0]["created_at"] > 0
    assert "private-trace" not in str(record)
    assert store.get(state.session_id, state.owner_id) == original
    assert store.trace(state.session_id, state.owner_id) == {"secret": "private-trace"}
    assert env.repository.get(state.session_id, state.owner_id).model_dump(mode="json") == before
    # The independent store can reopen the append-only revisions after a process restart.
    assert FeedbackStore(store.path).effective(state.session_id, state.owner_id)["review_revision"] == 1


@pytest.mark.parametrize("headers", [{}, {"X-Owner-Id": "demo-integration"}, {"X-Owner-Id": "student-other"}])
def test_review_api_requires_teacher_for_reads_and_writes(sealed, headers):
    env, state, _, items = sealed
    assert read(env, state, headers).status_code in (401, 403)
    assert save(env, state, payload(state, items), headers).status_code in (401, 403)
    assert env.runtime.feedback_store.reviews(state.session_id, state.owner_id) == []


def test_development_teacher_is_rejected_in_production(sealed, monkeypatch):
    env, state, _, items = sealed
    monkeypatch.setenv("APP_ENV", "production")
    assert read(env, state).status_code == 403
    assert save(env, state, payload(state, items)).status_code == 403


def test_review_requires_matching_owner_and_sealed_session(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    sid = env.new_session()
    state = env.repository.get(sid, "demo-integration")
    items = [{"stage": stage, "comment": "待复核", "improvement": "补充依据", "basis_ids": ["kb_feedback_principles"]}
             for stage in FEEDBACK_STAGES]
    assert read(env, state).status_code == 409
    assert save(env, state, payload(state, items)).status_code == 409
    assert save(env, state, payload(state, items, owner_id="demo-other")).status_code == 404
    assert env.client.get(f"/api/teacher/feedback/{sid}", headers=TEACHER,
                          params={"owner_id": "demo-other"}).status_code == 404


@pytest.mark.parametrize("change", [
    lambda body: body.update(reason="  "),
    lambda body: body.update(expected_revision=-1),
    lambda body: body.update(expected_revision=True),
    lambda body: body.update(teacher_id="forged"),
    lambda body: body.update(total_score=100),
    lambda body: body["items"].pop(),
    lambda body: body["items"][0].update(score=100),
    lambda body: body["items"][0].update(comment="心肌梗死是参考答案"),
    lambda body: body["items"][0].update(evidence_event_ids=["unknown"]),
    lambda body: body["items"][0].update(basis_ids=["kb_ecg_first"]),
])
def test_invalid_review_is_not_saved_or_released(sealed, change):
    env, state, _, items = sealed
    body = payload(state, items)
    change(body)
    assert save(env, state, body).status_code == 422
    assert env.runtime.feedback_store.reviews(state.session_id, state.owner_id) == []
    assert env.client.get(f"/api/sessions/{state.session_id}/feedback", headers=env.headers).status_code == 404


def test_review_retries_are_idempotent_and_stale_edits_conflict(sealed):
    env, state, _, items = sealed
    body = payload(state, items)
    assert save(env, state, body).json()["revision"] == 1
    assert save(env, state, body).json()["revision"] == 1
    assert save(env, state, {**body, "reason": "修改原因"}).status_code == 409
    assert save(env, state, {**body, "request_id": "stale-request"}).status_code == 409
    assert save(env, state, {**body, "request_id": "review-2", "expected_revision": 1}).json()["revision"] == 2
    assert save(env, state, body).json()["revision"] == 1
    assert [r["revision"] for r in read(env, state).json()["reviews"]] == [1, 2]
    assert env.runtime.feedback_store.effective(state.session_id, state.owner_id)["review_revision"] == 2


def test_review_store_serializes_competing_writers(sealed):
    env, state, _, items = sealed
    store = env.runtime.feedback_store

    def append(index):
        try:
            return FeedbackStore(store.path).append_review(
                state.session_id, state.owner_id, f"teacher-{index}", f"request-{index}",
                0, "并发复核", items, "draft_unreviewed",
            )
        except ConcurrentUpdateError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(append, (1, 2)))
    assert results.count(1) == results.count("conflict") == 1
    assert len(store.reviews(state.session_id, state.owner_id)) == 1


def test_feedback_review_does_not_adjudicate_needs_review_score(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    sid = env.new_session()
    assert env.client.post(f"/api/sessions/{sid}/differential", headers=env.headers, json={
        "request_id": "ddx", "candidates": [{"candidate_id": "aortic_dissection",
        "supporting_evidence": ["含糊依据"], "evidence_event_ids": ["invalid"]}],
    }).status_code == 200
    env.client.post(f"/api/sessions/{sid}/seal", headers=env.headers, json={"request_id": "seal"})
    state = env.repository.get(sid, "demo-integration")
    assert state.score_snapshot.status == "needs_review"
    items = env.runtime.feedback_service.get_or_generate(state, env.service.cases.get(state.case_id, state.case_version), "f")["items"]
    assert save(env, state, payload(state, items)).json()["score_status"] == "needs_review"
    assert env.client.get(f"/api/sessions/{sid}/score", headers=env.headers).json()["status"] == "needs_review"


@pytest.mark.parametrize("lose_response", [False, True])
def test_teacher_ui_repairs_feedback_and_student_refreshes_cached_version(sealed, monkeypatch, lose_response):
    env, state, _, items = sealed
    env.runtime.feedback_store.save_once(state.session_id, state.owner_id, "local_rules", items, "draft_unreviewed", {})
    _route_requests(monkeypatch, env)
    page = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=30).run()
    page.text_input(key="student_identity").set_value(state.owner_id).run()
    next(b for b in page.button if b.label == "恢复训练").click().run()
    assert page.session_state["feedback_by_session"][state.session_id]["review_revision"] == 0

    teacher = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=30).run()
    teacher.radio(key="workspace").set_value("教师病例管理").run()
    teacher.radio(key="teacher_task").set_value("训练反馈复核").run()
    teacher.text_input(key="feedback_review_owner").set_value(state.owner_id)
    teacher.text_input(key="feedback_review_session").set_value(state.session_id)
    next(b for b in teacher.button if b.label == "载入或刷新训练反馈").click().run()
    assert not teacher.exception, teacher.exception
    teacher.text_area(key="review_editor_病史采集_comment").set_value("复核后的病史评语。")
    teacher.text_area(key="review_editor_reason").set_value("校正病史建议。")
    routed_request = requests.request
    lost = []

    def request_with_lost_response(method, url, **kwargs):
        response = routed_request(method, url, **kwargs)
        if lose_response and method == "POST" and "/api/teacher/feedback/" in url and not lost:
            lost.append(kwargs["json"]["request_id"])
            raise requests.Timeout("response lost after commit")
        return response

    monkeypatch.setattr(requests, "request", request_with_lost_response)
    next(b for b in teacher.button if b.label == "保存反馈复核版本").click().run()
    assert not teacher.exception, teacher.exception
    if lose_response:
        assert teacher.session_state["feedback_review_pending"]["request_id"] == lost[0]
        assert len(env.runtime.feedback_store.reviews(state.session_id, state.owner_id)) == 1
        next(b for b in teacher.button if b.label == "保存反馈复核版本").click().run()
        assert not teacher.exception, teacher.exception
    assert any("已保存反馈复核第1版" in entry.value for entry in teacher.success)
    assert len(env.runtime.feedback_store.reviews(state.session_id, state.owner_id)) == 1
    next(b for b in page.button if b.label == "刷新已存反馈与教师复核").click().run()
    assert not page.exception, page.exception
    assert page.session_state["feedback_by_session"][state.session_id]["review_revision"] == 1
    assert any("复核后的病史评语" in entry.value for entry in page.markdown)
    assert env.runtime.feedback_store.get(state.session_id, state.owner_id)["items"] == items


def test_current_knowledge_is_rechecked_for_reviewed_feedback(sealed):
    env, state, _, items = sealed
    assert save(env, state, payload(state, items)).status_code == 200
    env.runtime.knowledge.entries.pop("kb_feedback_principles")
    response = env.client.get(f"/api/sessions/{state.session_id}/feedback", headers=env.headers)
    assert response.status_code == 409
    assert len(env.runtime.feedback_store.reviews(state.session_id, state.owner_id)) == 1

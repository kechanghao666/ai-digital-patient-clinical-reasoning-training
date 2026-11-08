"""Independent teacher adjudication for complete or needs_review sealed scores."""
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from streamlit.testing.v1 import AppTest

from app.integration.score_reviews import ScoreReviewStore
from app.storage import ConcurrentUpdateError
from test_integration import Env, ROOT
from test_frontend_smoke import _route_requests

TEACHER = {"X-Owner-Id": "teacher-score"}


def needs_review_env(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    sid = env.new_session()
    submitted = env.client.post(f"/api/sessions/{sid}/differential", headers=env.headers, json={
        "request_id": "ddx", "candidates": [{"candidate_id": "pulmonary_embolism",
        "supporting_evidence": ["不明确"], "opposing_evidence": ["不明确"], "evidence_event_ids": ["missing"]}],
    })
    assert submitted.status_code == 200
    sealed = env.client.post(f"/api/sessions/{sid}/seal", headers=env.headers, json={"request_id": "seal"})
    assert sealed.status_code == 200
    state = env.repository.get(sid, "demo-integration")
    assert state.score_snapshot.status == "needs_review"
    return env, sid, state


def completed_env(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    sid = env.client.post('/api/sessions?case_id=CASE-005', headers=env.headers).json()['session_id']
    assert env.client.post(f'/api/sessions/{sid}/treatment', headers=env.headers,
                          json={'request_id': 'treat', 'treatment_id': 'fluids_insulin', 'confirm_execution': True}).status_code == 200
    assert env.client.post(f'/api/sessions/{sid}/seal', headers=env.headers, json={'request_id': 'seal'}).status_code == 200
    state = env.repository.get(sid, 'demo-integration')
    assert state.score_snapshot.status == 'complete' and state.score_snapshot.total_score == 2
    return env, sid, state


def test_complete_disputed_score_can_be_reviewed_without_overwriting_original(tmp_path, monkeypatch):
    env, sid, state = completed_env(tmp_path, monkeypatch)
    before = state.model_dump_json()
    original = read(env, sid, state)
    assert original.status_code == 200, original.text
    payload = body(state, original.json()['original_score'], reason='本合成测试复核执行证据，原分独立保留。')
    path = f'/api/teacher/scores/{sid}'
    assert env.client.post(path, headers=TEACHER, json={**payload, 'reason': '  '}).status_code == 422
    assert env.client.post(path, headers=TEACHER, json={**payload, 'confirmed': False}).status_code == 422
    for _ in range(2):
        result = env.client.post(path, headers=TEACHER, json=payload)
        assert result.status_code == 200 and result.json()['revision'] == 1
    assert env.repository.get(sid, state.owner_id).model_dump_json() == before
    student = env.client.get(f'/api/sessions/{sid}/score-review', headers=env.headers)
    assert student.status_code == 200 and student.json()['reviewed_score']['total_score'] == 0
    assert '本合成测试' not in student.text
    assert len(env.runtime.score_reviews.list(sid, state.owner_id)) == 1


@pytest.mark.parametrize('status', [None, 'failed'])
def test_missing_or_failed_snapshot_is_not_adjudicated(tmp_path, monkeypatch, status):
    env, sid, state = completed_env(tmp_path, monkeypatch)
    if status is None:
        state.score_snapshot = None
    else:
        state.score_snapshot.status = status
    env.repository.save(state, expected_version=state.state_version)
    assert read(env, sid, state).status_code == 409


def read(env, sid, state, headers=TEACHER):
    return env.client.get(f"/api/teacher/scores/{sid}", headers=headers, params={"owner_id": state.owner_id})


def body(state, original, **changes):
    return {"owner_id": state.owner_id, "request_id": "score-review-1", "expected_revision": 0,
            "reason": "逐项核对封存事件并裁定不确定项。", "confirmed": True, "items": [
                {"item_id": item["item_id"], "score": 0.0, "reason": "封存证据不足，保留零分。",
                 "evidence_event_ids": []} for item in original["items"]
            ], **changes}


def test_score_review_keeps_original_and_exposes_separate_student_projection(tmp_path, monkeypatch):
    env, sid, state = needs_review_env(tmp_path, monkeypatch)
    record = read(env, sid, state).json()
    original = copy.deepcopy(record["original_score"])
    before = state.model_dump_json()
    response = env.client.post(f"/api/teacher/scores/{sid}", headers=TEACHER,
                               json=body(state, original))
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    persisted = env.repository.get(sid, state.owner_id)
    assert persisted.score_snapshot.model_dump(mode="json") == original
    assert persisted.model_dump_json() == before
    student = env.client.get(f"/api/sessions/{sid}/score-review", headers=env.headers)
    assert student.status_code == 200
    assert student.json()["revision"] == 1
    assert student.json()["reviewed_score"]["status"] == "complete"
    assert env.client.get(f"/api/sessions/{sid}/score", headers=env.headers).json()["status"] == "needs_review"
    assert ScoreReviewStore(env.runtime.score_reviews.path).list(sid, state.owner_id)[0]["snapshot"] == student.json()["reviewed_score"]


@pytest.mark.parametrize("headers", [{}, {"X-Owner-Id": "demo-integration"}, {"X-Owner-Id": "student-other"}])
def test_score_review_requires_teacher(tmp_path, monkeypatch, headers):
    env, sid, state = needs_review_env(tmp_path, monkeypatch)
    original = state.score_snapshot.model_dump(mode="json")
    assert read(env, sid, state, headers).status_code in (401, 403)
    assert env.client.post(f"/api/teacher/scores/{sid}", headers=headers,
                           json=body(state, original)).status_code in (401, 403)
    assert env.runtime.score_reviews.list(sid, state.owner_id) == []


@pytest.mark.parametrize("change", [
    lambda payload: payload.update(items=payload["items"][:-1]),
    lambda payload: payload["items"].__setitem__(0, {**payload["items"][0], "score": 1.0}),
    lambda payload: payload["items"].__setitem__(0, {**payload["items"][0], "evidence_event_ids": ["fake"]}),
    lambda payload: payload.update(extra="forbidden"),
    lambda payload: payload.update(expected_revision=True),
    lambda payload: payload.update(reason="  "),
    lambda payload: payload.update(confirmed=False),
    lambda payload: payload.update(confirmed=1),
    lambda payload: payload.pop("confirmed"),
    lambda payload: payload.update(items=payload["items"] + payload["items"][:1]),
    lambda payload: payload["items"][0].update(item_id="unknown"),
    lambda payload: payload["items"][0].update(score=-1),
    lambda payload: payload["items"][0].update(score="NaN"),
    lambda payload: payload["items"][0].update(score=True),
    lambda payload: payload["items"][0].update(score="0"),
    lambda payload: payload["items"][0].update(score=999),
    lambda payload: payload["items"][0].update(reason="STEMI为正确答案"),
    lambda payload: payload["items"][0].update(reason="a p i _ k e y 为内部字段"),
    lambda payload: payload["items"][0].update(reason=" "),
])
def test_invalid_score_review_is_not_saved(tmp_path, monkeypatch, change):
    env, sid, state = needs_review_env(tmp_path, monkeypatch)
    original = read(env, sid, state).json()["original_score"]
    payload = body(state, original)
    change(payload)
    assert env.client.post(f"/api/teacher/scores/{sid}", headers=TEACHER, json=payload).status_code == 422
    assert env.runtime.score_reviews.list(sid, state.owner_id) == []
    assert env.client.get(f"/api/sessions/{sid}/score-review", headers=env.headers).status_code == 404


def test_score_review_is_idempotent_and_stale_revision_conflicts(tmp_path, monkeypatch):
    env, sid, state = needs_review_env(tmp_path, monkeypatch)
    original = read(env, sid, state).json()["original_score"]
    payload = body(state, original)
    assert env.client.post(f"/api/teacher/scores/{sid}", headers=TEACHER, json=payload).json()["revision"] == 1
    assert env.client.post(f"/api/teacher/scores/{sid}", headers=TEACHER, json=payload).json()["revision"] == 1
    assert env.client.post(f"/api/teacher/scores/{sid}", headers=TEACHER,
                           json={**payload, "reason": "不同原因"}).status_code == 409
    assert env.client.post(f"/api/teacher/scores/{sid}", headers=TEACHER,
                           json={**payload, "request_id": "stale"}).status_code == 409
    assert env.client.post(f"/api/teacher/scores/{sid}", headers=TEACHER,
                           json={**payload, "request_id": "stale", "expected_revision": 1}).json()["revision"] == 2
    assert env.client.post(f"/api/teacher/scores/{sid}", headers=TEACHER, json=payload).json()["revision"] == 1
    assert env.client.get(f"/api/sessions/{sid}/score-review", headers=env.headers).json()["revision"] == 2


def test_score_review_store_serializes_competing_writers(tmp_path):
    store = ScoreReviewStore(tmp_path / "integration.db")
    snapshot = {"session_id": "s", "items": [], "total_score": 0, "max_score": 1, "status": "complete",
                "seal_seq": 1, "generated_at": "2026-11-08T00:00:00Z"}

    def append(index):
        try:
            return store.append("s", "student", f"teacher-{index}", f"request-{index}", 0, "并发", snapshot)
        except ConcurrentUpdateError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(append, (1, 2)))
    assert results.count(1) == results.count("conflict") == 1


def test_score_review_rejects_production_identity_and_mismatching_student(tmp_path, monkeypatch):
    env, sid, state = needs_review_env(tmp_path, monkeypatch)
    original = state.score_snapshot.model_dump(mode="json")
    assert env.client.post(f"/api/teacher/scores/{sid}", headers=TEACHER,
                           json=body(state, original, owner_id="demo-other")).status_code == 404
    assert env.client.get(f"/api/teacher/scores/{sid}", headers=TEACHER,
                          params={"owner_id": "demo-other"}).status_code == 404
    monkeypatch.setenv("APP_ENV", "production")
    assert read(env, sid, state).status_code == 403
    assert env.client.post(f"/api/teacher/scores/{sid}", headers=TEACHER, json=body(state, original)).status_code == 403


def test_unsealed_training_cannot_be_adjudicated(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    sid = env.new_session()
    path = f"/api/teacher/scores/{sid}"
    assert env.client.get(path, headers=TEACHER, params={"owner_id": "demo-integration"}).status_code == 409
    assert env.client.get(f"/api/sessions/{sid}/score-review", headers=env.headers).status_code == 409


def test_partial_score_uses_real_evidence_and_internal_reasons_stay_private(tmp_path, monkeypatch):
    env, sid, state = needs_review_env(tmp_path, monkeypatch)
    payload = body(state, state.score_snapshot.model_dump(mode="json"), reason="内部复核原因，不向学生展示STEMI")
    decision = next(item for item in payload["items"] if item["item_id"] == "pulmonary_embolism")
    decision.update(score=0.5, evidence_event_ids=[state.events[0].event_id] * 2)
    response = env.client.post(f"/api/teacher/scores/{sid}", headers=TEACHER, json=payload)
    assert response.status_code == 200, response.text
    shown = env.client.get(f"/api/sessions/{sid}/score-review", headers=env.headers)
    snapshot = shown.json()["reviewed_score"]
    assert snapshot["total_score"] == 0.5
    partial = next(item for item in snapshot["items"] if item["item_id"] == "pulmonary_embolism")
    assert not partial["hit"] and partial["evidence_event_ids"] == [state.events[0].event_id]
    assert "内部复核原因" not in shown.text and "teacher-score" not in shown.text
    assert "STEMI" not in shown.text
    assert env.client.get(f"/api/sessions/{sid}/score-review", headers={"X-Owner-Id": "demo-other"}).status_code == 404


def test_sealing_event_cannot_be_claimed_as_clinical_evidence(tmp_path, monkeypatch):
    env, sid, state = needs_review_env(tmp_path, monkeypatch)
    payload = body(state, state.score_snapshot.model_dump(mode="json"))
    payload["items"][0]["evidence_event_ids"] = [state.events[-1].event_id]
    assert env.client.post(f"/api/teacher/scores/{sid}", headers=TEACHER, json=payload).status_code == 422


@pytest.mark.parametrize("lose_response", [False, True])
@pytest.mark.parametrize("complete", [False, True])
def test_teacher_ui_adjudicates_and_student_reads_separate_result(tmp_path, monkeypatch, lose_response, complete):
    env, sid, state = (completed_env if complete else needs_review_env)(tmp_path, monkeypatch)
    _route_requests(monkeypatch, env)
    page = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=30).run()
    page.radio(key="workspace").set_value("教师病例管理").run()
    page.radio(key="teacher_task").set_value("评分复核与裁定").run()
    page.text_input(key="score_review_owner").set_value(state.owner_id)
    page.text_input(key="score_review_session").set_value(sid)
    next(button for button in page.button if button.label == "载入或刷新评分").click().run()
    assert not page.exception, page.exception
    assert any(("状态：已完成自动评分" if complete else "状态：待人工复核") in value.value for value in page.markdown)
    # Deliberate human choices; this synthetic zero-score decision is not expert validation.
    for item in state.score_snapshot.items:
        page.number_input(key=f"score_editor_{item.item_id}_score").set_value(0.0)
        page.text_area(key=f"score_editor_{item.item_id}_reason").set_value("证据不足，教学裁定为零分。")
    page.text_area(key="score_editor_reason").set_value("核对全部事件。")
    save_button = lambda: next(button for button in page.button if button.label == "保存独立评分裁定")
    save_button().click().run()
    assert not env.runtime.score_reviews.list(sid, state.owner_id)
    page.checkbox(key="score_editor_confirmed").check()
    route = requests.request
    lost = []

    def request(method, url, **kwargs):
        response = route(method, url, **kwargs)
        if lose_response and method == "POST" and "/api/teacher/scores/" in url and not lost:
            lost.append(kwargs["json"]["request_id"])
            raise requests.Timeout("response lost after commit")
        return response

    monkeypatch.setattr(requests, "request", request)
    save_button().click().run()
    if lose_response:
        assert page.session_state["score_review_pending"]["request_id"] == lost[0]
        save_button().click().run()
    assert not page.exception, page.exception
    assert any("已保存教师评分裁定第1版" in result.value for result in page.success)
    assert len(env.runtime.score_reviews.list(sid, state.owner_id)) == 1
    student = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=30).run()
    student.text_input(key="student_identity").set_value(state.owner_id).run()
    next(button for button in student.button if button.label == "恢复训练").click().run()
    assert not student.exception, student.exception
    assert any("教师评分裁定第1版" in result.value for result in student.info)
    if complete:
        assert any("已完成自动评分" in result.value for result in student.success)
    else:
        assert any("待人工复核" in result.value for result in student.warning)
    # Identity changes must discard the hidden rubric and internal review records.
    page.text_input(key="teacher_identity").set_value("teacher-another").run()
    with pytest.raises(KeyError):
        page.session_state["score_review_record"]

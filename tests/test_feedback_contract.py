"""Feedback is complete, attributable, and never releases a hidden reference answer."""
import copy

import pytest

from app.integration.tools import FEEDBACK_STAGES, validate_feedback_items
from test_integration import Env, _play_and_seal


@pytest.fixture
def sealed(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    sid = _play_and_seal(env)
    state = env.repository.get(sid, "demo-integration")
    case = env.service.cases.get(state.case_id, state.case_version)
    items = [{
        "stage": stage, "comment": "请根据已有记录复核本环节。",
        "improvement": "补齐本环节缺少的训练依据。",
        "evidence_event_ids": [], "basis_ids": ["kb_feedback_principles"],
    } for stage in FEEDBACK_STAGES]
    return env, state, case, items


@pytest.mark.parametrize("change", [
    lambda items: items.pop(),
    lambda items: items.append(copy.deepcopy(items[0])),
    lambda items: items[0].update(stage=items[1]["stage"]),
    lambda items: items[0].update(stage=[]),
    lambda items: items[0].update(improvement="  "),
    lambda items: items[0].update(basis_ids=[]),
    lambda items: items[0].update(basis_ids=["kb_ecg_first"]),
    lambda items: items[0].update(comment="参考答案为 S T E M I"),
    lambda items: items[0].update(comment="本例应诊断心肌梗死"),
    lambda items: items[0].update(evidence_event_ids=["made-up-event"]),
])
def test_incomplete_or_unsafe_feedback_is_rejected(sealed, change):
    env, state, case, items = sealed
    assert isinstance(validate_feedback_items(items, state, case, env.service.knowledge), list)
    change(items)
    assert isinstance(validate_feedback_items(items, state, case, env.service.knowledge), str)


def test_feedback_without_actions_does_not_need_fabricated_events(sealed):
    env, state, case, items = sealed
    assert all(not item["evidence_event_ids"] for item in items)
    checked = validate_feedback_items(list(reversed(items)), state, case, env.service.knowledge)
    assert [item["stage"] for item in checked] == list(FEEDBACK_STAGES)


def test_local_feedback_covers_five_stages_without_reference_diagnosis(sealed):
    env, state, case, _ = sealed
    body = env.runtime.feedback_service.get_or_generate(state, case, "feedback")
    assert len(body["items"]) == 5
    assert isinstance(validate_feedback_items(body["items"], state, case, env.service.knowledge), list)


@pytest.mark.parametrize("public_id", ["CASE-001", "CASE-002", "CASE-003", "CASE-004", "CASE-005"])
def test_empty_training_has_valid_feedback_in_every_specialty(tmp_path, monkeypatch, public_id):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    started = env.client.post(f"/api/sessions?case_id={public_id}", headers=env.headers)
    assert started.status_code == 200
    sid = started.json()["session_id"]
    assert env.client.post(f"/api/sessions/{sid}/seal", headers=env.headers,
                           json={"request_id": "seal"}).status_code == 200
    response = env.client.post(f"/api/sessions/{sid}/feedback", headers=env.headers,
                               json={"request_id": "feedback"})
    assert response.status_code == 200, response.text
    assert [item["stage"] for item in response.json()["items"]] == list(FEEDBACK_STAGES)


def test_old_score_advice_is_filtered_without_changing_sealed_snapshot(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    sid = env.new_session()
    env.client.post(f"/api/sessions/{sid}/seal", headers=env.headers, json={"request_id": "seal"})
    before = env.repository.get(sid, "demo-integration").score_snapshot.model_dump(mode="json")
    assert "心肌梗死" in str(before)
    response = env.client.get(f"/api/sessions/{sid}/score", headers=env.headers)
    assert response.status_code == 200
    assert "心肌梗死" not in response.text
    assert response.json()["total_score"] == before["total_score"]
    assert env.repository.get(sid, "demo-integration").score_snapshot.model_dump(mode="json") == before


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_old_incompatible_feedback_is_not_released(sealed, method):
    env, state, case, items = sealed
    items[0]["comment"] = "参考答案为心肌梗死"
    env.runtime.feedback_store.save_once(state.session_id, state.owner_id, "local_rules", items,
                                         "draft_unreviewed", {"real_platform": False})
    kwargs = {"json": {"request_id": "feedback"}} if method == "POST" else {}
    response = env.client.request(method, f"/api/sessions/{state.session_id}/feedback", headers=env.headers, **kwargs)
    assert response.status_code == 409
    assert "参考答案" not in response.text
    assert env.runtime.feedback_store.get(state.session_id, state.owner_id)["items"] == items


def test_feedback_exposes_attributable_sources_without_hidden_diagnosis(sealed):
    from dataclasses import replace

    env, state, case, items = sealed
    library = env.runtime.knowledge
    entry = library.entries["kb_feedback_principles"]
    library.entries[entry.basis_id] = replace(entry, title="含心肌梗死的参考说明",
                                             sources=("项目自拟的反馈撰写规范", "STEMI参考题名"))
    env.runtime.feedback_store.save_once(state.session_id, state.owner_id, "local_rules", items,
                                         "draft_unreviewed", {"real_platform": False},
                                         library.snapshot(items, case.case_id, case.version))
    response = env.client.get(f"/api/sessions/{state.session_id}/feedback", headers=env.headers)
    assert response.status_code == 200
    basis = response.json()["teaching_basis"][0]
    assert basis["sources"] == ["项目自拟的反馈撰写规范"] and basis["restricted"]
    assert "心肌梗死" not in response.text and "STEMI" not in response.text
    assert library.entries[entry.basis_id].title == "含心肌梗死的参考说明"

"""Feedback is complete, attributable, and never releases a hidden reference answer."""
import copy

import pytest

from app.integration.tools import FEEDBACK_STAGES, validate_feedback_items
from test_integration import Env, _play_and_seal, ok_response, token_of

# A unique token used as the name of an unknown field, so the audit store can be searched for it.
MARKER = "synth-marker-2f9c"


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


# --------------------------------------------------- rejection reason is auditable, content is not
def _sealed_appendix(env: Env) -> str:
    """A sealed APPENDICITIS session; the STEMI-only clinical basis never applies to it."""
    started = env.client.post("/api/sessions?case_id=CASE-002", headers=env.headers)
    assert started.status_code == 200, started.text
    sid = started.json()["session_id"]
    sealed = env.client.post(f"/api/sessions/{sid}/seal", headers=env.headers, json={"request_id": "seal"})
    assert sealed.status_code == 200, sealed.text
    return sid


def _valid_items() -> list[dict]:
    return [
        {
            "stage": stage,
            "comment": "请结合已记录的操作复核本环节。",
            "improvement": "回看本环节事件，补充尚未记录的依据。",
            "evidence_event_ids": [],
            "basis_ids": ["kb_feedback_principles"],
        }
        for stage in FEEDBACK_STAGES
    ]


def _run_feedback(env: Env, sid: str, mutate, record: dict):
    def agent(request, body, service):
        token = token_of(body)
        items = _valid_items()
        mutate(items)
        record["result"] = service.submit_feedback(token, items)
        return ok_response("反馈已提交")

    env.agent = agent
    return env.client.post(f"/api/sessions/{sid}/feedback", headers=env.headers, json={"request_id": "f"})


def _feedback_audit(db_path, session_id: str):
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            "SELECT c.tool, c.ok, c.detail FROM turn_calls c JOIN turns t ON c.turn_id=t.turn_id "
            "WHERE t.session_id=? AND t.kind='feedback' ORDER BY c.call_id",
            (session_id,),
        ).fetchall()
    finally:
        connection.close()


def _integration_text(db_path) -> str:
    """Every stored value across all tables, so a synthetic marker can be searched for anywhere."""
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        rows = [" ".join("" if value is None else str(value) for value in row)
                for table in tables for row in connection.execute(f"SELECT * FROM {table}")]
    finally:
        connection.close()
    return "\n".join(rows)


@pytest.mark.parametrize(
    ("mutate", "category"),
    [
        (lambda items: items[4].update(stage=items[3]["stage"]),
         "each of the five stages must appear exactly once"),
        (lambda items: items[0].update(basis_ids=["kb_ecg_first"]),
         "basis_ids must reference teaching-basis entries available for this case"),
        (lambda items: items[0].update({MARKER: "x"}),
         "unknown fields are not allowed"),
    ],
)
def test_rejected_feedback_keeps_a_fixed_audit_category_without_the_payload(tmp_path, monkeypatch, mutate, category):
    env = Env(tmp_path, monkeypatch)
    sid = _sealed_appendix(env)
    before = env.repository.get(sid, "demo-integration").model_dump_json()
    record: dict = {}

    response = _run_feedback(env, sid, mutate, record)

    assert record["result"] == {"ok": False, "error": "invalid_feedback", "message": category}
    assert response.status_code == 502
    # Nothing is persisted and the sealed score snapshot is untouched.
    assert env.runtime.feedback_store.get(sid, "demo-integration") is None
    assert env.client.get(f"/api/sessions/{sid}/feedback", headers=env.headers).status_code == 404
    assert env.repository.get(sid, "demo-integration").model_dump_json() == before
    # The audit distinguishes the three reasons by a fixed category and stores no caller content.
    audit = _feedback_audit(env.settings.integration_db_path, sid)
    assert audit and all(tool == "submit_feedback" for tool, _, _ in audit)
    assert [row[2] for row in audit] == [f"invalid_feedback: {category}"]
    assert MARKER not in _integration_text(env.settings.integration_db_path)


def test_valid_feedback_control_is_accepted_and_persisted_once(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    sid = _sealed_appendix(env)
    before = env.repository.get(sid, "demo-integration").model_dump_json()
    record: dict = {}

    response = _run_feedback(env, sid, lambda items: None, record)

    assert record["result"] == {"ok": True, "accepted_items": 5}
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "nexent"
    stored = env.runtime.feedback_store.get(sid, "demo-integration")
    assert stored is not None and [item["stage"] for item in stored["items"]] == list(FEEDBACK_STAGES)
    # The sealed score snapshot is unchanged by feedback generation.
    assert env.repository.get(sid, "demo-integration").model_dump_json() == before
    assert all(ok for _, ok, _ in _feedback_audit(env.settings.integration_db_path, sid))
    assert MARKER not in _integration_text(env.settings.integration_db_path)

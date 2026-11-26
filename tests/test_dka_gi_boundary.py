"""DKA-001 f_gi: the answer and the sealed score follow the question, not the bundled fact.

The rubric "询问恶心呕吐和腹痛" names two symptoms of one fact that also carries an intake clause.
Before the fix a single abdominal-pain question answered the whole fact and scored full credit,
while a compound nausea+pain question answered only the nausea and scored nothing. The fix is in
the shared question lexicon (the symptom is recognised on both the student side and the stored
fact side) and in the rubric declaring the two atoms it actually requires; nothing here is keyed
to a case id or to one wording.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.case_store import CaseStore
from app.disclosure import detect_intents
from app.models import SessionState
from app.patient_answers import fact_fragments
from test_cases_config import Api, item as score_item

ROOT = Path(__file__).parents[1]
CASES = CaseStore(ROOT / "cases")
OWNER = "demo-cfg"
REQUIRED = ["f_gi#a1", "f_gi#a2"]


def _dka(version: int):
    return CASES.get("DKA-001", version)


def _start(api: Api, version: int) -> str:
    if version == 2:
        return api.start("DKA-001")
    state = SessionState(owner_id=OWNER, case_id="DKA-001", case_version=1)
    api.repository.create(state)
    return state.session_id


def _answers(api: Api, sid: str) -> list[str]:
    stored = api.repository.get(sid, OWNER)
    return [event.result["answer"] for event in stored.events if event.action.value == "ask"]


def _atoms(api: Api, sid: str) -> list[str]:
    stored = api.repository.get(sid, OWNER)
    return [atom for event in stored.events if event.action.value == "ask"
            for atom in event.result.get("answer_atom_ids", [])]


# --- the declaration itself ---------------------------------------------------

@pytest.mark.parametrize("version", [1, 2])
def test_rubric_requires_the_nausea_and_pain_atoms_not_the_intake_clause(version):
    rubric = next(r for r in _dka(version).rubric if r.item_id == "f_gi")
    atoms = {f.atom_id: f.content for f in fact_fragments(
        next(f for f in _dka(version).facts if f.fact_id == "f_gi"))}
    assert rubric.required_atom_ids == REQUIRED
    assert atoms["f_gi#a1"] == "恶心呕吐"
    assert atoms["f_gi#a2"] == "肚子隐隐作痛"
    assert atoms["f_gi#a3"] == "吃不下东西"  # bundled into the fact but outside the rubric


def test_abdominal_pain_is_recognised_on_the_student_side_and_on_the_stored_fact_side():
    # The lexicon is shared: the student wording and the case wording reach the same intent.
    for question in ["肚子疼吗？", "有没有腹痛？"]:
        _, intents, _ = detect_intents(question)
        assert "abdominal_pain" in intents
    _, fragment_intents, _ = detect_intents("肚子隐隐作痛")
    assert "abdominal_pain" in fragment_intents


# --- one symptom asked at a time ---------------------------------------------

@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("question", ["肚子疼吗？", "有没有腹痛？"])
def test_single_abdominal_pain_question_answers_only_the_pain_and_scores_zero(
        version, question, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start(api, version)
    event = api.ask(sid, question)
    assert _answers(api, sid) == ["肚子隐隐作痛。"]
    assert _atoms(api, sid) == ["f_gi#a2"]
    score = score_item(api.seal_and_score(sid), "f_gi")
    assert not score["hit"] and score["score"] == 0.0
    assert score["evidence_event_ids"] == [event]  # the teacher can still see the partial answer
    assert "1/2" in score["reason"]
    assert "恶心呕吐" not in _answers(api, sid)[0] and "吃不下" not in _answers(api, sid)[0]


@pytest.mark.parametrize("version", [1, 2])
def test_single_nausea_question_answers_only_the_nausea_and_scores_zero(version, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start(api, version)
    event = api.ask(sid, "有没有恶心呕吐？")
    assert _answers(api, sid) == ["恶心呕吐。"]
    assert _atoms(api, sid) == ["f_gi#a1"]
    score = score_item(api.seal_and_score(sid), "f_gi")
    assert not score["hit"] and score["score"] == 0.0
    assert score["evidence_event_ids"] == [event]
    assert "1/2" in score["reason"]


@pytest.mark.parametrize("version", [1, 2])
def test_intake_question_answers_only_the_intake_and_does_not_earn_the_rubric(
        version, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start(api, version)
    api.ask(sid, "吃不下东西吗？")
    assert _answers(api, sid) == ["吃不下东西。"]
    assert _atoms(api, sid) == ["f_gi#a3"]
    score = score_item(api.seal_and_score(sid), "f_gi")
    assert not score["hit"] and score["score"] == 0.0


@pytest.mark.parametrize("question", ["拉肚子吗？", "拉肚子吗？头疼吗？", "肚子胀吗？胸口疼吗？"])
def test_other_symptoms_do_not_disclose_abdominal_pain(question, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start(api, 2)
    api.ask(sid, question)
    assert "肚子隐隐作痛" not in _answers(api, sid)[0]
    assert "f_gi#a2" not in _atoms(api, sid)


# --- both symptoms asked ------------------------------------------------------

@pytest.mark.parametrize("version", [1, 2])
def test_compound_question_answers_both_symptoms_and_scores_full(version, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start(api, version)
    event = api.ask(sid, "有没有恶心呕吐和腹痛？")
    assert _answers(api, sid) == ["恶心呕吐，肚子隐隐作痛。"]
    assert _atoms(api, sid) == ["f_gi#a1", "f_gi#a2"]
    score = score_item(api.seal_and_score(sid), "f_gi")
    assert score["hit"] and score["score"] == score["max_score"] == 1.0
    assert score["evidence_event_ids"] == [event]


@pytest.mark.parametrize("version", [1, 2])
def test_two_turns_score_full_and_the_teacher_can_trace_both_answers(version, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start(api, version)
    nausea = api.ask(sid, "有没有恶心呕吐？")
    pain = api.ask(sid, "肚子疼吗？")
    assert _answers(api, sid) == ["恶心呕吐。", "肚子隐隐作痛。"]
    assert _atoms(api, sid) == ["f_gi#a1", "f_gi#a2"]
    score = score_item(api.seal_and_score(sid), "f_gi")
    assert score["hit"] and score["score"] == 1.0
    assert score["evidence_event_ids"] == [nausea, pain]
    # The intake clause was never asked for, so the fact stays undisclosed.
    assert api.state(sid)["disclosed_fact_count"] == 0


@pytest.mark.parametrize("version", [1, 2])
def test_reversed_two_turns_score_full_and_keep_the_event_order(version, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start(api, version)
    pain = api.ask(sid, "肚子疼吗？")
    nausea = api.ask(sid, "有没有恶心呕吐？")
    assert _answers(api, sid) == ["肚子隐隐作痛。", "恶心呕吐。"]
    score = score_item(api.seal_and_score(sid), "f_gi")
    assert score["hit"] and score["evidence_event_ids"] == [pain, nausea]


@pytest.mark.parametrize("version", [1, 2])
def test_intake_is_answered_only_when_asked_and_keeps_the_credit(version, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start(api, version)
    nausea = api.ask(sid, "有没有恶心呕吐？")
    pain = api.ask(sid, "肚子疼吗？")
    api.ask(sid, "吃不下东西吗？")
    assert _answers(api, sid) == ["恶心呕吐。", "肚子隐隐作痛。", "吃不下东西。"]
    score = score_item(api.seal_and_score(sid), "f_gi")
    assert score["hit"] and score["score"] == 1.0
    assert score["evidence_event_ids"] == [nausea, pain]
    assert "f_gi" in api.repository.get(sid, OWNER).disclosed_fact_ids


# --- sealed scores stay sealed ------------------------------------------------

@pytest.mark.parametrize("version", [1, 2])
def test_a_sealed_score_is_not_recalculated_under_a_new_rubric(version, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start(api, version)
    api.ask(sid, "有没有恶心呕吐？")
    api.ask(sid, "肚子疼吗？")
    # Seal under the previous rubric, then read the score back as an application upgrade would.
    original_case = api.store.get("DKA-001", version).model_copy(deep=True)
    next(r for r in original_case.rubric if r.item_id == "f_gi").required_atom_ids = []
    with monkeypatch.context() as old:
        old.setattr(api.store, "get", lambda *args, **kwargs: original_case)
        sealed = api.seal_and_score(sid)
    assert not score_item(sealed, "f_gi")["hit"]
    assert api.client.get(f"/api/sessions/{sid}/score", headers=api.headers).json() == sealed
    stored = api.repository.get(sid, OWNER)
    sealed_item = next(i for i in stored.score_snapshot.items if i.item_id == "f_gi")
    assert not sealed_item.hit  # the sealed snapshot keeps the score it was sealed with


@pytest.mark.parametrize("version", [1, 2])
def test_the_same_request_is_saved_once_and_a_sealed_replay_keeps_the_score(
        version, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start(api, version)
    body = {"request_id": "same-request", "question": "有没有恶心呕吐和腹痛？"}
    first = api.client.post(f"/api/sessions/{sid}/ask", headers=api.headers, json=body)
    second = api.client.post(f"/api/sessions/{sid}/ask", headers=api.headers, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["answer"] == second.json()["answer"]
    assert first.json()["event"]["event_id"] == second.json()["event"]["event_id"]
    assert second.json()["timing"]["replayed"] is True
    stored = api.repository.get(sid, OWNER)
    assert len([e for e in stored.events if e.action.value == "ask"]) == 1
    sealed = api.seal_and_score(sid)
    replay = api.client.post(f"/api/sessions/{sid}/ask", headers=api.headers, json=body)
    assert replay.json()["timing"]["replayed"] is True
    assert replay.json()["answer"] == first.json()["answer"]
    assert len([e for e in api.repository.get(sid, OWNER).events if e.action.value == "ask"]) == 1
    assert api.client.get(f"/api/sessions/{sid}/score", headers=api.headers).json() == sealed

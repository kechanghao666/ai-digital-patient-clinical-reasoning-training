"""Ask_event rubrics that declare required atoms score from actual Q&A, not whole-record disclosure.

APPY-001 ``f_onset`` requires "询问起病时间和诱因", but the case bundles an extra trauma clause into
the same fact. The legacy evidence (``matched_fact_ids``) only fills once every fragment of the fact
has been answered, so a student who asked and received both rubric-required answers still scored 0.
Engineering checks over the shipped case files and synthetic sessions only; clinical wording stays
with the teaching reviewers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.case_store import CaseStore
from app.models import ActionKind, CaseDefinition, Event, SessionState
from app.patient_answers import fact_fragments
from app.scoring import score_session
from test_cases_config import Api, item as score_item

ROOT = Path(__file__).parents[1]
CASES = CaseStore(ROOT / "cases")
REQUIRED = ["f_onset#a1", "f_onset#a3"]


def _appy(version: int) -> CaseDefinition:
    return CASES.get("APPY-001", version)


def _f_onset_raw() -> tuple[dict, dict]:
    raw = json.loads((ROOT / "cases" / "appendicitis_case_v2.json").read_text(encoding="utf-8"))
    return raw, next(r for r in raw["rubric"] if r["item_id"] == "f_onset")


def _event(seq: int, atoms: list[str], *, matched: list[str] | None = None) -> Event:
    return Event(
        event_id=f"e{seq}", request_id=f"r{seq}", seq=seq, actor="student", action=ActionKind.ASK,
        intent="q", payload={"question": "q", "matched_fact_ids": matched or []},
        result={"answer": "答", "answer_atom_ids": atoms, "turn_kind": "fact"},
        sim_time=0, state_before={}, state_after={},
    )


def _score_items(events: list[Event]) -> dict:
    state = SessionState(owner_id="t", case_id="APPY-001", case_version=2, events=list(events))
    snapshot = score_session(state, _appy(2))
    return {item.item_id: item for item in snapshot.items}


# --- the declaration itself ---------------------------------------------------

@pytest.mark.parametrize("version", [1, 2])
def test_f_onset_requires_time_and_trigger_atoms_not_the_trauma_clause(version):
    rubric = next(r for r in _appy(version).rubric if r.item_id == "f_onset")
    atoms = {f.atom_id: f.content for f in fact_fragments(
        next(f for f in _appy(version).facts if f.fact_id == "f_onset"))}
    assert rubric.required_atom_ids == REQUIRED
    assert atoms["f_onset#a1"] == "大约24小时前开始的"
    assert "诱因" in atoms["f_onset#a3"]
    assert "受伤" in atoms["f_onset#a2"]  # bundled into the fact but outside the rubric


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize(("item_id", "target"), [
    ("f_onset_time", "是上午9点半发生的"),
    ("f_hx_af", "有房颤好几年"),
])
def test_stroke_rubric_cites_only_the_answer_to_its_question(version, item_id, target):
    case = CASES.get("STROKE-001", version)
    fact = next(fact for fact in case.facts if fact.fact_id == item_id)
    fragments = fact_fragments(fact)
    target_id = next(fragment.atom_id for fragment in fragments if fragment.content == target)
    other_ids = [fragment.atom_id for fragment in fragments if fragment.atom_id != target_id]
    state = SessionState(owner_id="t", case_id=case.case_id, case_version=version,
                         events=[_event(1, other_ids)])
    before = next(item for item in score_session(state, case).items if item.item_id == item_id)
    assert before.score == 0 and not before.evidence_event_ids
    state.events.append(_event(2, [target_id]))
    after = next(item for item in score_session(state, case).items if item.item_id == item_id)
    assert after.hit and after.score == after.max_score
    assert after.evidence_event_ids == ["e2"]


def test_required_atoms_must_reference_derived_atoms():
    raw, f_onset = _f_onset_raw()
    f_onset["required_atom_ids"] = ["f_onset#a9"]
    with pytest.raises(ValueError, match="unknown atoms"):
        CaseDefinition(**raw)


def test_required_atoms_are_limited_to_ask_event_rubrics():
    raw, _ = _f_onset_raw()
    ex_us = next(r for r in raw["rubric"] if r["item_id"] == "ex_us")
    ex_us["required_atom_ids"] = REQUIRED
    with pytest.raises(ValueError, match="only valid for ask_event"):
        CaseDefinition(**raw)


def test_required_atoms_must_be_unique():
    raw, f_onset = _f_onset_raw()
    f_onset["required_atom_ids"] = REQUIRED + ["f_onset#a1"]
    with pytest.raises(ValueError, match="repeats a required atom"):
        CaseDefinition(**raw)


# --- scoring semantics --------------------------------------------------------

def test_time_only_is_a_miss_with_one_partial_evidence():
    items = _score_items([_event(1, ["f_onset#a1"])])
    f_onset = items["f_onset"]
    assert not f_onset.hit and f_onset.score == 0.0
    assert len(f_onset.evidence_event_ids) == 1
    assert "1/2" in f_onset.reason


def test_trigger_only_is_a_miss():
    items = _score_items([_event(1, ["f_onset#a3"])])
    assert not items["f_onset"].hit and items["f_onset"].evidence_event_ids


def test_time_plus_trauma_is_still_a_miss():
    # The trauma atom is not part of the rubric; asking it must not substitute for the trigger.
    items = _score_items([_event(1, ["f_onset#a1", "f_onset#a2"], matched=["f_onset"])])
    assert not items["f_onset"].hit


def test_two_turns_hit_and_cite_both_qa_events_in_order():
    items = _score_items([_event(1, ["f_onset#a1"]), _event(2, ["f_onset#a3"])])
    f_onset = items["f_onset"]
    assert f_onset.hit and f_onset.score == 1.0
    assert f_onset.evidence_event_ids == ["e1", "e2"]
    assert f_onset.reason == "已找到符合条件的事件证据"


def test_reversed_order_hits_and_keeps_event_order():
    items = _score_items([_event(1, ["f_onset#a3"]), _event(2, ["f_onset#a1"])])
    assert items["f_onset"].hit
    assert items["f_onset"].evidence_event_ids == ["e1", "e2"]


def test_one_shot_hit_cites_the_single_event():
    items = _score_items([_event(1, ["f_onset#a1", "f_onset#a3"])])
    f_onset = items["f_onset"]
    assert f_onset.hit and f_onset.evidence_event_ids == ["e1"]


def test_repeated_atom_answers_do_not_duplicate_evidence():
    items = _score_items([_event(1, ["f_onset#a1"]), _event(2, ["f_onset#a1"]), _event(3, ["f_onset#a3"])])
    assert items["f_onset"].hit
    assert items["f_onset"].evidence_event_ids == ["e1", "e3"]


def test_pre_atom_full_disclosure_keeps_credit_for_an_existing_open_session():
    event = _event(1, [], matched=["f_onset"])
    del event.result["answer_atom_ids"]
    item = _score_items([event])["f_onset"]
    assert item.hit and item.evidence_event_ids == ["e1"]


def test_explicit_empty_atoms_do_not_fall_back_to_parent_fact_credit():
    item = _score_items([_event(1, [], matched=["f_onset"])])["f_onset"]
    assert not item.hit and item.evidence_event_ids == []


def test_other_actions_and_facts_cannot_supply_required_answers():
    unrelated = _event(1, REQUIRED)
    unrelated.action = ActionKind.WAIT
    item = _score_items([unrelated, _event(2, ["f_hx#a1", "f_hx#a3"])])["f_onset"]
    assert not item.hit and item.evidence_event_ids == []


def test_atoms_outside_seal_do_not_count():
    state = SessionState(owner_id="t", case_id="APPY-001", case_version=2,
                         events=[_event(1, ["f_onset#a1"]), _event(2, ["f_onset#a3"])],
                         sealed=True, seal_seq=1)
    f_onset = {item.item_id: item for item in score_session(state, _appy(2)).items}["f_onset"]
    assert not f_onset.hit and len(f_onset.evidence_event_ids) == 1


def test_legacy_rubrics_still_key_off_whole_fact_disclosure():
    # f_hx declares no required atoms: partial asks stay a miss with no evidence, and the
    # one-shot full fact keeps the legacy single-event evidence.
    items = _score_items([_event(1, ["f_hx#a1"]), _event(2, ["f_hx#a3"])])
    assert not items["f_hx"].hit and items["f_hx"].evidence_event_ids == []
    items = _score_items([_event(1, ["f_hx#a1", "f_hx#a2", "f_hx#a3"], matched=["f_hx"])])
    assert items["f_hx"].hit and items["f_hx"].evidence_event_ids == ["e1"]


# --- end-to-end over the student API -----------------------------------------

def _start_v1(api: Api) -> str:
    state = SessionState(owner_id="demo-cfg", case_id="APPY-001", case_version=1)
    api.repository.create(state)
    return state.session_id


def _ask(api: Api, sid: str, question: str) -> str:
    return api.ask(sid, question)


@pytest.mark.parametrize("version", [1, 2])
def test_two_turns_score_full_and_teacher_can_trace_both_answers(version, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start_v1(api) if version == 1 else api.start("APPY-001")
    time_event = _ask(api, sid, "什么时候开始的？")
    trigger_event = _ask(api, sid, "有没有诱因？")
    f_onset = score_item(api.seal_and_score(sid), "f_onset")
    assert f_onset["hit"] and f_onset["score"] == 1.0
    assert f_onset["evidence_event_ids"] == [time_event, trigger_event]
    state = api.state(sid)
    assert state["disclosed_fact_count"] == 0  # the trauma clause was never given away


@pytest.mark.parametrize("version", [1, 2])
def test_one_shot_scores_full_with_single_cited_event(version, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start_v1(api) if version == 1 else api.start("APPY-001")
    event_id = _ask(api, sid, "什么时候开始的，有没有诱因？")
    f_onset = score_item(api.seal_and_score(sid), "f_onset")
    assert f_onset["hit"] and f_onset["evidence_event_ids"] == [event_id]


@pytest.mark.parametrize("version", [1, 2])
def test_time_only_stays_zero_with_traceable_partial_evidence(version, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start_v1(api) if version == 1 else api.start("APPY-001")
    time_event = _ask(api, sid, "什么时候开始的？")
    f_onset = score_item(api.seal_and_score(sid), "f_onset")
    assert not f_onset["hit"] and f_onset["score"] == 0.0
    assert f_onset["evidence_event_ids"] == [time_event]
    assert "1/2" in f_onset["reason"]


@pytest.mark.parametrize("version", [1, 2])
def test_unasked_trauma_atom_stays_undisclosed(version, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = _start_v1(api) if version == 1 else api.start("APPY-001")
    _ask(api, sid, "什么时候开始的？")
    _ask(api, sid, "有没有诱因？")
    stored = api.repository.get(sid, "demo-cfg")
    ask_events = [event for event in stored.events if event.action == ActionKind.ASK]
    assert all(event.payload["matched_fact_ids"] == [] for event in ask_events)
    assert all("f_onset#a2" not in event.result["answer_atom_ids"] for event in ask_events)
    assert "f_onset" not in stored.disclosed_fact_ids


@pytest.mark.parametrize("version", [1, 2])
def test_cap_history_cites_smoking_condition_and_allergy_turns(version, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    state = SessionState(owner_id="demo-cfg", case_id="CAP-001", case_version=version)
    api.repository.create(state)
    events = []
    for question, expected in [("抽烟吗？", "吸烟30年。"),
                               ("以前有什么病吗？", "有慢性支气管炎。"),
                               ("有药物过敏吗？", "没有药物过敏。")]:
        events.append(api.ask(state.session_id, question))
        saved = api.repository.get(state.session_id, "demo-cfg").events[-1]
        assert saved.result["answer"] == expected
    item = score_item(api.seal_and_score(state.session_id), "f_hx")
    assert item["score"] == item["max_score"] == 1
    assert item["evidence_event_ids"] == events


@pytest.mark.parametrize("question", ["以前有糖尿病史吗？", "您父亲有哪些基础病？", "抽烟吗？"])
def test_other_history_questions_do_not_disclose_cap_chronic_condition(question, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CAP-001")
    api.ask(sid, question)
    saved = api.repository.get(sid, "demo-cfg").events[-1]
    assert "支气管炎" not in saved.result["answer"]
    assert "f_hx#a2" not in saved.result["answer_atom_ids"]
    assert score_item(api.seal_and_score(sid), "f_hx")["score"] == 0


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("questions, answers", [
    (["活动后有没有气短，休息后会不会好一点？"], ["活动后气短，休息时好一点。"]),
    (["活动后有没有气短，休息后会不会好转？"], ["活动后气短，休息时好一点。"]),
    (["有没有气短？", "休息后会好转吗？"], ["活动后气短。", "休息时好一点。"]),
])
def test_cap_rest_answer_and_sealed_evidence(version, questions, answers, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    state = SessionState(owner_id="demo-cfg", case_id="CAP-001", case_version=version)
    api.repository.create(state)
    events = []
    for question, answer in zip(questions, answers, strict=True):
        events.append(api.ask(state.session_id, question))
        assert api.repository.get(state.session_id, "demo-cfg").events[-1].result["answer"] == answer
    item = score_item(api.seal_and_score(state.session_id), "f_dyspnea")
    assert item["score"] == item["max_score"] == 1
    assert item["evidence_event_ids"] == events


@pytest.mark.parametrize("prior", [None, "有没有药物过敏？", "有没有气短和咳嗽？"])
def test_rest_followup_without_one_clear_symptom_asks_for_clarification(prior, tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CAP-001")
    if prior:
        api.ask(sid, prior)
    api.ask(sid, "休息后会好转吗？")
    event = api.repository.get(sid, "demo-cfg").events[-1]
    assert event.result["turn_kind"] == "clarify_topic" and not event.result["answer_atom_ids"]


def test_sealed_score_snapshot_is_returned_unchanged(tmp_path, monkeypatch):
    from app import main

    api = Api(tmp_path, monkeypatch)
    sid = api.start("APPY-001")
    _ask(api, sid, "什么时候开始的？")
    _ask(api, sid, "有没有诱因？")
    # Seal under the old rubric, then reload the new rubric as an application upgrade would.
    original_case = api.store.get("APPY-001", 2).model_copy(deep=True)
    next(r for r in original_case.rubric if r.item_id == "f_onset").required_atom_ids = []
    with monkeypatch.context() as old:
        old.setattr(api.store, "get", lambda *args, **kwargs: original_case)
        before = api.seal_and_score(sid)
    assert not score_item(before, "f_onset")["hit"]
    stored = api.repository.get(sid, "demo-cfg")
    sealed_item = next(item for item in stored.score_snapshot.items if item.item_id == "f_onset")
    assert _score_items(stored.events)["f_onset"].hit

    def unexpected_recalculation(*args, **kwargs):
        pytest.fail("A stored sealed score must not be recalculated")

    monkeypatch.setattr(main, "score_session", unexpected_recalculation)
    api_item = score_item(api.client.get(f"/api/sessions/{sid}/score", headers=api.headers).json(), "f_onset")
    assert api_item["hit"] == sealed_item.hit
    assert api_item["score"] == sealed_item.score
    assert api_item["evidence_event_ids"] == sealed_item.evidence_event_ids

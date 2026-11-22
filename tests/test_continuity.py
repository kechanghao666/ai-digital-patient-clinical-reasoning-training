"""Continuous consultation: bare follow-ups carry the previous turn's topic forward.

Engineering checks over synthetic draft cases only; they say nothing about clinical correctness.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import disclosure
from app.case_store import CaseStore
from app.disclosure import _is_continuation, classify_turn, non_fact_reply
from app.models import Fact, SessionState
from test_cases_config import Api

ROOT = Path(__file__).parents[1]
CASES = CaseStore(ROOT / "cases")


def _classify(public_id: str, question: str, disclosed: set[str]):
    case = CASES.resolve_for_student(public_id, include_unpublished=True)
    state = SessionState(owner_id="t", case_id=case.case_id, case_version=case.version,
                         disclosed_fact_ids=list(disclosed))
    return classify_turn(state, question, case.facts)


def _fact_ids(public_id: str, question: str, disclosed: set[str]) -> list[str]:
    return [fact.fact_id for fact in _classify(public_id, question, disclosed)[0]]


def _ask(api: Api, sid: str, question: str) -> dict:
    response = api.client.post(
        f"/api/sessions/{sid}/ask",
        headers=api.headers,
        json={"request_id": api.rid(), "question": question},
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- detection ---------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "后来呢", "然后呢", "接着呢", "接下来呢", "之后呢", "继续", "继续说", "继续讲", "还有吗", "还有呢",
])
def test_continuation_cues_are_recognised(question):
    assert _is_continuation(question)


@pytest.mark.parametrize("question", [
    "然后你有没有发烧", "然后吃什么药", "还有别的症状吗", "然后呢今天天气怎么样",
])
def test_content_or_clinical_questions_are_not_continuation(question):
    assert not _is_continuation(question)


# --- the two CASE-001 non-sequiturs -----------------------------------------

def test_when_question_answers_the_time_not_the_trigger():
    # "什么时候开始的" asks for the onset *time*; the case's time ("两个小时前") lives in the chief
    # complaint, not in the trigger/course fact f_pain_onset ("休息时突然发作").
    assert _fact_ids("CASE-001", "什么时候开始的", set()) == ["f_cc"]
    assert _fact_ids("CASE-001", "什么时候开始的", {"f_cc"}) == ["f_cc"]


def test_trigger_question_still_answers_the_manner():
    # "当时在干什么" asks how it started, so it still discloses the trigger/course fact.
    assert _fact_ids("CASE-001", "当时在干什么", {"f_cc"}) == ["f_pain_onset"]


def test_location_question_answers_location():
    assert _fact_ids("CASE-001", "具体是哪里疼", {"f_cc"}) == ["f_location"]


# --- topic-aware continuation (integration, real session state) --------------

def test_later_after_onset_answers_course_not_location(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    first = _ask(api, sid, "什么时候开始的？")["answer"]
    later = _ask(api, sid, "后来呢？")["answer"]
    assert "两个小时前" in first  # the onset time
    assert "持续没有缓解" in later  # the course, not the location
    assert "胸口正中间" not in later


def test_later_after_past_history_stays_in_past_history(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    htn = _ask(api, sid, "有高血压吗？")["answer"]
    later = _ask(api, sid, "后来呢？")["answer"]
    assert "高血压" in htn
    assert "糖尿病" in later  # the next past-history item, not a pain symptom
    assert "胸痛" not in later and "放射" not in later


def test_later_after_exam_return_does_not_fabricate(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    _ask(api, sid, "怎么了？")
    api.act(sid, "order_exam", {"exam_id": "ex_ecg"})
    api.wait(sid, 5)  # ECG result is now ready
    later = _ask(api, sid, "后来呢？")["answer"]
    # The patient never volunteers an exam result; a continuation after a non-question turn clarifies.
    assert later == "您想了解哪方面的情况？"
    assert "ST段" not in later and "抬高" not in later


def test_three_consecutive_laters_do_not_unlock_unrelated_symptoms(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    _ask(api, sid, "什么时候开始的？")
    replies = [_ask(api, sid, "后来呢？")["answer"] for _ in range(3)]
    # All three follow the present-illness thread (course -> location -> character), never the
    # background history (hypertension/diabetes/smoking) and never the diagnosis.
    for reply in replies:
        assert "高血压" not in reply and "糖尿病" not in reply and "吸烟" not in reply
        assert "ST段" not in reply
    assert any("持续" in r for r in replies)


def test_later_after_a_single_background_item_confirms_done(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    _ask(api, sid, "有药物过敏吗？")  # 过敏史 has a single fact
    assert _ask(api, sid, "后来呢？")["answer"] == "暂时就这些了。"


# --- clarification and safety -----------------------------------------------

def test_continuation_with_no_context_clarifies():
    selected, kind = _classify("CASE-001", "后来呢", set())
    assert selected == [] and kind == "clarify"
    assert non_fact_reply("后来呢", kind) == "您想先说说哪里不舒服？"


def test_continuation_respects_visibility_bounds():
    future_fact = Fact(fact_id="f_x", field="疼痛性质", content="胸痛像压榨一样。",
                       disclosure_triggers=["性质"], status_valid_from=10)
    state = SessionState(owner_id="t", case_id="t", case_version=1, disclosed_fact_ids=["f_cc"],
                         sim_time_minutes=1)
    selected, kind = classify_turn(state, "后来呢", [future_fact])
    assert selected == [] and kind == "clarify_topic"


def test_continuation_never_discloses_evolution_facts(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    _ask(api, sid, "怎么了？")
    # Ask many continuations; none may surface the evolution fact f_deterioration.
    for _ in range(30):
        body = _ask(api, sid, "后来呢？")
        assert "血压下降" not in body["answer"] and "意识模糊" not in body["answer"]


def test_full_four_turn_sequence_is_answered_coherently(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    answers = {q: _ask(api, sid, q)["answer"] for q in ["怎么了？", "哪里不舒服？", "什么时候开始的？", "后来呢？"]}
    assert answers["怎么了？"] == "我两个小时前突然开始胸痛。"
    assert answers["哪里不舒服？"] == "我两个小时前突然开始胸痛。"
    assert "两个小时前" in answers["什么时候开始的？"]
    assert "持续没有缓解" in answers["后来呢？"]

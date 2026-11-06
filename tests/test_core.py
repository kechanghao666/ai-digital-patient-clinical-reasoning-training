from pathlib import Path

import pytest

from app.case_store import CaseStore
from app.adapters import SimulatorPatientAdapter, authorized_answer_or_fallback
from app.models import ActionKind, CaseDefinition, ClinicalState, ExamState, Fact, ReviewStatus, SessionState
from app.state_machine import StateMachine
from app.storage import ConcurrentUpdateError, SessionRepository
from app.scoring import score_session


ROOT = Path(__file__).parents[1]


@pytest.fixture()
def machine(tmp_path):
    cases = CaseStore(ROOT / "cases")
    repo = SessionRepository(tmp_path / "app.db")
    state = SessionState(owner_id="student-a", case_id="STEMI-001", case_version=1)
    repo.create(state)
    return StateMachine(cases, repo), state


def test_case_fixture_is_strict_and_draft():
    case = CaseStore(ROOT / "cases").get("STEMI-001", 1)
    assert case.review_status == ReviewStatus.DRAFT
    assert case.public_id == "CASE-001"
    assert case.hidden_diagnosis


def test_disclosure_is_atomic_and_does_not_leak_other_facts(machine):
    sm, state = machine
    from app.disclosure import classify_and_authorize

    facts = sm._case(state).facts
    matches = classify_and_authorize(state, "请问疼痛是什么性质？", facts)
    assert [fact.fact_id for fact in matches] == ["f_pain_character"]


def test_idempotency_same_request_returns_original(machine):
    sm, state = machine
    first = sm.apply(state, "req-1", ActionKind.ORDER_EXAM, {"exam_id": "ex_ecg"})
    second = sm.apply(state, "req-1", ActionKind.ORDER_EXAM, {"exam_id": "ex_ecg"})
    assert first.response == second.response
    assert len(state.events) == 1


def test_idempotency_rejects_changed_payload(machine):
    sm, state = machine
    sm.apply(state, "req-1", ActionKind.ORDER_EXAM, {"exam_id": "ex_ecg"})
    with pytest.raises(ValueError, match="different content"):
        sm.apply(state, "req-1", ActionKind.ORDER_EXAM, {"exam_id": "ex_troponin"})


def test_pending_exam_is_not_normal_and_becomes_ready_after_wait(machine):
    sm, state = machine
    result = sm.apply(state, "req-1", ActionKind.ORDER_EXAM, {"exam_id": "ex_ecg"})
    assert result.state.orders[0].state.value == "pending"
    sm.apply(state, "req-2", ActionKind.WAIT, {"minutes": 5})
    assert state.orders[0].state.value == "ready"
    assert state.orders[0].result == "II/III/aVF导联ST段抬高"


def test_priority_ecg_requires_first_exam_before_wait(machine):
    sm, state = machine
    sm.apply(state, "troponin-first", ActionKind.ORDER_EXAM, {"exam_id": "ex_troponin"})
    sm.apply(state, "wait-long", ActionKind.WAIT, {"minutes": 240})
    sm.apply(state, "ecg-late", ActionKind.ORDER_EXAM, {"exam_id": "ex_ecg"})
    sm.apply(state, "seal-late", ActionKind.SEAL, {})
    snapshot = score_session(state, sm._case(state))
    ecg = next(item for item in snapshot.items if item.item_id == "ex_ecg")
    assert not ecg.hit
    assert ecg.score == 0


def test_priority_ecg_first_order_before_wait_is_scored(machine):
    sm, state = machine
    sm.apply(state, "ecg-first", ActionKind.ORDER_EXAM, {"exam_id": "ex_ecg"})
    sm.apply(state, "wait-after", ActionKind.WAIT, {"minutes": 5})
    sm.apply(state, "seal-first", ActionKind.SEAL, {})
    snapshot = score_session(state, sm._case(state))
    ecg = next(item for item in snapshot.items if item.item_id == "ex_ecg")
    assert ecg.hit
    assert ecg.score == ecg.max_score


def test_treatment_requires_explicit_confirmation(machine):
    sm, state = machine
    with pytest.raises(ValueError, match="explicit confirmation"):
        sm.apply(
            state,
            "req-1",
            ActionKind.EXECUTE_TREATMENT,
            {"treatment_id": "reperfusion_pathway", "confirm_execution": False},
        )


def test_case_notes_are_persisted_as_an_auditable_action(machine):
    sm, state = machine
    result = sm.apply(state, "notes-1", ActionKind.UPDATE_NOTES, {"notes": "胸痛两小时，拟完善心电图。"})
    assert result.response == {"submitted": True}
    assert state.case_notes == "胸痛两小时，拟完善心电图。"
    assert state.events[-1].action == ActionKind.UPDATE_NOTES


def test_treatment_plan_does_not_execute(machine):
    sm, state = machine
    result = sm.apply(
        state,
        "req-plan",
        ActionKind.PLAN_TREATMENT,
        {"treatment_id": "reperfusion_pathway", "confirm_execution": False},
    )
    assert result.response == {
        "planned": True,
        "executed": False,
        "treatment_id": "reperfusion_pathway",
    }
    assert not any(event.action == ActionKind.EXECUTE_TREATMENT for event in state.events)


def test_sealed_state_rejects_new_actions(machine):
    sm, state = machine
    state.sealed = True
    with pytest.raises(ValueError, match="sealed"):
        sm.apply(state, "req-1", ActionKind.WAIT, {"minutes": 5})


def test_seal_is_idempotent_but_new_request_is_rejected(machine):
    sm, state = machine
    first = sm.apply(state, "seal-1", ActionKind.SEAL, {})
    second = sm.apply(state, "seal-1", ActionKind.SEAL, {})
    assert first.response == second.response
    assert len(state.events) == 1
    with pytest.raises(ValueError, match="sealed"):
        sm.apply(state, "after-seal", ActionKind.WAIT, {"minutes": 1})


def test_zero_delay_exam_becomes_ready_immediately(machine):
    sm, state = machine
    case = sm._case(state)
    case.exams[0].return_after_minutes = 0
    result = sm.apply(state, "zero-delay", ActionKind.ORDER_EXAM, {"exam_id": "ex_ecg"})
    assert result.state.orders[0].state == ExamState.READY
    assert result.state.orders[0].result == "II/III/aVF导联ST段抬高"


def test_delay_rule_is_applied_from_simulated_time(machine):
    sm, state = machine
    sm.apply(state, "order-ecg", ActionKind.ORDER_EXAM, {"exam_id": "ex_ecg"})
    result = sm.apply(state, "wait-65", ActionKind.WAIT, {"minutes": 65})
    assert result.response["clinical_state"] == ClinicalState.DETERIORATING.value
    assert state.clinical_state == ClinicalState.DETERIORATING
    assert "f_deterioration" in state.disclosed_fact_ids
    assert result.event.rule_id == "r_stemi_delay_fixture"


def test_delay_rule_is_excluded_after_reperfusion_execution(machine):
    sm, state = machine
    sm.apply(state, "order-ecg", ActionKind.ORDER_EXAM, {"exam_id": "ex_ecg"})
    sm.apply(state, "reperfuse", ActionKind.EXECUTE_TREATMENT, {
        "treatment_id": "reperfusion_pathway",
        "confirm_execution": True,
    })
    result = sm.apply(state, "wait-65", ActionKind.WAIT, {"minutes": 65})
    assert result.event.rule_id is None
    assert state.clinical_state == ClinicalState.INITIAL


def test_repository_rejects_old_snapshot_instead_of_losing_event(machine):
    sm, state = machine
    stale = machine[1].model_copy(deep=True)
    sm.apply(state, "fresh", ActionKind.ORDER_EXAM, {"exam_id": "ex_ecg"})
    stale_machine = StateMachine(sm.cases, sm.repository)
    with pytest.raises(ConcurrentUpdateError):
        stale_machine.apply(stale, "stale", ActionKind.ORDER_EXAM, {"exam_id": "ex_troponin"})
    latest = sm.repository.get(state.session_id, state.owner_id)
    assert [event.action for event in latest.events] == [ActionKind.ORDER_EXAM]


def test_scoring_uses_sealed_events_and_rejects_negative_or_unknown_evidence(machine):
    sm, state = machine
    sm.apply(state, "bad-ddx", ActionKind.SUBMIT_DIFFERENTIAL, {
        "candidates": [{
            "candidate_id": "aortic_dissection",
            "supporting_evidence": ["unrelated text"],
            "evidence_event_ids": ["does-not-exist"],
        }]
    })
    sm.apply(state, "negative-dx", ActionKind.SUBMIT_DIAGNOSIS, {
        "diagnosis": "已经排除 STEMI，不考虑该诊断"
    })
    sm.apply(state, "seal", ActionKind.SEAL, {})
    snapshot = score_session(state, sm._case(state))
    ddx = next(item for item in snapshot.items if item.item_id == "aortic_dissection")
    diagnosis = next(item for item in snapshot.items if item.item_id == "stemi")
    assert not ddx.hit
    assert not diagnosis.hit


def test_nstemi_is_not_scored_as_stemi_and_unrelated_existing_event_needs_review(machine):
    sm, state = machine
    ask_result = sm._append(
        state,
        "ask-existing",
        ActionKind.ASK,
        {"question": "疼痛是什么性质", "matched_fact_ids": ["f_pain_character"]},
        {"answer": "胸痛像压榨一样。", "fact_ids": ["f_pain_character"]},
    )
    sm.repository.save(state, expected_version=0)
    sm.apply(state, "bad-ddx", ActionKind.SUBMIT_DIFFERENTIAL, {
        "candidates": [{
            "candidate_id": "aortic_dissection",
            "supporting_evidence": ["火星天气晴朗，与患者无关"],
            "evidence_event_ids": [ask_result.event_id],
        }]
    })
    sm.apply(state, "nstemi", ActionKind.SUBMIT_DIAGNOSIS, {"diagnosis": "NSTEMI"})
    sm.apply(state, "seal", ActionKind.SEAL, {})
    snapshot = score_session(state, sm._case(state))
    assert not next(item for item in snapshot.items if item.item_id == "stemi").hit
    assert snapshot.status == "needs_review"


@pytest.mark.parametrize(
    "diagnosis, hit, needs_review",
    [
        ("STEMI", True, False),
        ("急性ST段抬高型心肌梗死", True, False),
        ("不是STEMI", False, False),
        ("非STEMI", False, False),
        ("未见STEMI", False, False),
        ("非急性ST段抬高型心肌梗死", False, False),
        ("STEMI已排除", False, False),
        ("STEMI？", False, True),
        ("STEMI待确诊", False, True),
        ("STEMI已确诊，无药物过敏史", True, False),
        ("STEMI，已排除主动脉夹层", True, False),
        ("STEMI，考虑急诊PCI", True, False),
        ("ASTEMI", False, False),
        ("不能排除STEMI", False, True),
        ("STEMI不成立", False, False),
        ("STEMI已否定", False, False),
        ("STEMI未被证实", False, False),
        ("初步诊断STEMI，复核后排除STEMI", False, True),
        ("排除诊断：STEMI", False, False),
        ("STEMI已确诊，已排除NSTEMI", True, False),
        ("STEMI已确诊，非NSTEMI", True, False),
        ("暂无证据支持STEMI", False, True),
        ("暂不诊断为STEMI", False, True),
        ("既往STEMI，本次诊断肺栓塞", False, True),
        ("排除：STEMI", False, False),
        ("STEMI已确诊并排除主动脉夹层", True, False),
        ("未明确诊断STEMI", False, True),
        ("尚未确诊STEMI", False, True),
        ("不能确诊STEMI", False, True),
        ("不应诊断为STEMI", False, True),
        ("无法确诊：STEMI", False, True),
        ("STEMI已确诊但现已排除", False, True),
        ("STEMI已确诊，已排除肺栓塞", False, True),
        ("STEMI已确诊，未见肺栓塞证据", False, True),
        ("STEMI已确诊；非糖尿病", False, True),
        ("STEMI已确诊，肺栓塞已排除", False, True),
    ],
)
def test_diagnosis_matching_requires_a_bounded_positive_statement(machine, diagnosis, hit, needs_review):
    sm, state = machine
    sm.apply(state, "diagnosis", ActionKind.SUBMIT_DIAGNOSIS, {"diagnosis": diagnosis})
    sm.apply(state, "seal", ActionKind.SEAL, {})
    snapshot = score_session(state, sm._case(state))
    item = next(item for item in snapshot.items if item.item_id == "stemi")
    assert item.hit is hit
    assert (snapshot.status == "needs_review") is needs_review


def test_pending_exam_result_cannot_support_differential_evidence(machine):
    sm, state = machine
    order = sm.apply(state, "order-ecg", ActionKind.ORDER_EXAM, {"exam_id": "ex_ecg"})
    sm.apply(state, "ddx", ActionKind.SUBMIT_DIFFERENTIAL, {
        "candidates": [{
            "candidate_id": "aortic_dissection",
            "supporting_evidence": ["II/III/aVF导联ST段抬高"],
            "evidence_event_ids": [order.event.event_id],
        }]
    })
    sm.apply(state, "seal", ActionKind.SEAL, {})
    snapshot = score_session(state, sm._case(state))
    item = next(item for item in snapshot.items if item.item_id == "aortic_dissection")
    assert not item.hit
    assert snapshot.status == "needs_review"


def test_delay_rule_reveals_observable_update_and_event_evidence(machine):
    sm, state = machine
    sm.apply(state, "order-ecg", ActionKind.ORDER_EXAM, {"exam_id": "ex_ecg"})
    result = sm.apply(state, "wait-65", ActionKind.WAIT, {"minutes": 65})
    assert result.response["clinical_updates"] == ["患者出现血压下降和意识模糊，需要立即重新评估。"]
    assert state.clinical_updates == result.response["clinical_updates"]
    assert result.event.fact_ids_revealed == ["f_deterioration"]
    reloaded = sm.repository.get(state.session_id, state.owner_id)
    assert reloaded.clinical_updates == state.clinical_updates


def test_negative_fact_is_disclosed_by_normal_question(machine):
    from app.disclosure import classify_and_authorize

    fact = Fact(
        fact_id="f_no_allergy",
        content="没有药物过敏史。",
        field="过敏史",
        polarity="negative",
        disclosure_triggers=["过敏"],
    )
    matches = classify_and_authorize(machine[1], "有没有药物过敏？", [fact])
    assert [item.fact_id for item in matches] == ["f_no_allergy"]


def test_adapter_output_gate_rejects_unauthorized_extra_text(machine):
    case = machine[0]._case(machine[1])
    facts = [fact for fact in case.facts if fact.fact_id == "f_pain_character"]
    answer, ids, fallback = authorized_answer_or_fallback(
        "胸痛像压榨一样。未经授权的诊断是 STEMI。",
        facts,
        case,
    )
    assert fallback
    assert ids == []
    assert "STEMI" not in answer


def test_generic_seal_persists_snapshot_for_restart(machine):
    sm, state = machine
    sm.apply(state, "seal-restart", ActionKind.SEAL, {})
    reloaded = sm.repository.get(state.session_id, state.owner_id)
    assert reloaded.sealed
    assert reloaded.score_snapshot is not None
    assert reloaded.score_snapshot.seal_seq == 0
    recalculated = score_session(reloaded, sm._case(reloaded))
    assert recalculated.model_dump(exclude={"generated_at"}) == reloaded.score_snapshot.model_dump(
        exclude={"generated_at"}
    )


def test_case_validation_rejects_configured_exam_without_result():
    with pytest.raises(ValueError, match="must have a result"):
        CaseDefinition(
            case_id="bad",
            version=1,
            public_id="CASE-BAD",
            specialty="test",
            title="bad",
            learning_goals=["test"],
            target_population="test",
            review_status=ReviewStatus.DRAFT,
            chief_complaint="test",
            facts=[{"fact_id": "f1", "content": "x", "field": "x"}],
            exams=[{
                "exam_id": "e1",
                "standard_name": "test",
                "return_after_minutes": 0,
                "allowed_values": ["ok"],
                "configured": True,
            }],
            rubric=[{
                "item_id": "f1",
                "stage": "病史采集",
                "description": "test",
                "evidence_type": "ask_event",
                "weight": 1,
            }],
        )

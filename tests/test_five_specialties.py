"""Five-specialty rehearsal over the real API with the local simulator patient.

Each scripted run is an *engineering* check that the configured rules, scoring and evolution branches
work for every case. It is not a student sample, not an expert comparison, and the cases remain
unreviewed drafts.
"""
from __future__ import annotations

import json

import pytest

from app.case_authoring import check_case, has_errors

from test_cases_config import ROOT, Api, item  # noqa: F401  (item re-exported for readability)

SPECIALTY_RUNS = {
    "CASE-001": dict(
        asks={"character": "疼痛是什么性质", "radiation": "放射到哪里", "onset": "什么时候开始"},
        orders=["ex_ecg", "ex_troponin"],
        wait=30,
        ddx=[
            ("aortic_dissection", "休息时突然发作", ["onset"], "胸痛像压榨一样", ["character"]),
            ("pulmonary_embolism", "休息时突然发作", ["onset"], "ST段抬高", ["WAIT"]),
        ],
        diagnosis="STEMI",
        treatments=["aspirin", "reperfusion_pathway"],
        delay=("ex_ecg", 5, 60, "reperfusion_pathway"),
    ),
    "CASE-002": dict(
        asks={"mig": "哪里痛，有没有转移", "onset": "什么时候开始的", "fever": "有没有发热", "gi": "有没有恶心呕吐", "hx": "既往史和过敏史"},
        orders=["ex_us", "ex_cbc", "ex_urine"],
        wait=45,
        ddx=[
            ("ureteral_stone", None, [], "一开始是肚脐周围隐隐作痛，后来逐渐转移到右下腹", ["mig"]),
            ("gastroenteritis", "有恶心，吐过一次", ["gi"], "阑尾增粗并周围渗出", ["WAIT"]),
        ],
        diagnosis="急性阑尾炎",
        treatments=["surgical_consult", "fluids_antibiotics"],
        delay=("ex_us", 45, 90, "surgical_consult"),
    ),
    "CASE-003": dict(
        asks={"onset": "起病是什么时候", "sym": "有哪些症状，手脚无力吗", "anti": "有没有吃抗凝药，有没有出血", "af": "有房颤吗", "htn": "有高血压吗", "head": "头痛呕吐吗"},
        orders=["ex_ct_head", "ex_glucose", "ex_ecg"],
        wait=15,
        ddx=[
            ("intracerebral_hemorrhage", "高血压15年", ["htn"], "未见出血", ["WAIT"]),
            ("hypoglycemia", None, [], "正常范围", ["WAIT"]),
        ],
        diagnosis="急性缺血性卒中",
        treatments=["stroke_pathway", "supportive_care"],
        delay=("ex_ct_head", 15, 60, "stroke_pathway"),
    ),
    "CASE-004": dict(
        asks={"fever": "发热多少度，有没有寒战", "cough": "咳嗽咳痰吗", "dys": "有没有气短", "hx": "吸烟吗，过敏吗"},
        orders=["ex_cxr", "ex_cbc", "ex_spo2"],
        wait=30,
        ddx=[
            ("acute_bronchitis", "咳黄绿色的痰", ["cough"], "右下肺斑片状浸润影", ["WAIT"]),
            ("pulmonary_embolism", "活动后气短", ["dys"], "最高体温39℃", ["fever"]),
        ],
        diagnosis="社区获得性肺炎",
        treatments=["antibiotics", "oxygen_support"],
        delay=("ex_cxr", 30, 120, "antibiotics"),
    ),
    "CASE-005": dict(
        asks={"poly": "口渴多饮吗", "gi": "恶心呕吐腹痛吗", "hx": "既往有糖尿病吗", "ins": "胰岛素用得怎么样，有什么诱因"},
        orders=["ex_glucose", "ex_ketone", "ex_abg"],
        wait=20,
        ddx=[
            ("gastroenteritis", "恶心呕吐", ["gi"], "明显升高", ["WAIT"]),
            ("hyperosmolar_state", "明显升高", ["WAIT"], "阳性（强）", ["WAIT"]),
        ],
        diagnosis="糖尿病酮症酸中毒",
        treatments=["fluids_insulin", "monitoring"],
        delay=("ex_abg", 20, 60, "fluids_insulin"),
    ),
}


def ideal_run(api: Api, public_id: str, script: dict) -> tuple[str, dict]:
    sid = api.start(public_id)
    events = {key: api.ask(sid, question) for key, question in script["asks"].items()}
    for exam_id in script["orders"]:
        api.act(sid, "order_exam", {"exam_id": exam_id})
    wait_event = api.wait(sid, script["wait"])
    resolve = lambda ids: [wait_event if i == "WAIT" else events[i] for i in ids]  # noqa: E731
    candidates = []
    for cid, support, support_events, oppose, oppose_events in script["ddx"]:
        candidates.append(
            {
                "candidate_id": cid,
                "supporting_evidence": [support] if support else [],
                "opposing_evidence": [oppose],
                "evidence_event_ids": list(dict.fromkeys(resolve(support_events) + resolve(oppose_events))),
            }
        )
    api.differential(sid, candidates)
    api.act(sid, "submit_diagnosis", {"diagnosis": script["diagnosis"]})
    for treatment in script["treatments"]:
        api.act(sid, "execute_treatment", {"treatment_id": treatment, "confirm_execution": True})
    return sid, api.seal_and_score(sid)


@pytest.mark.parametrize("public_id", sorted(SPECIALTY_RUNS))
def test_ideal_run_earns_full_automatic_five_stage_score(tmp_path, monkeypatch, public_id):
    api = Api(tmp_path, monkeypatch)
    _, score = ideal_run(api, public_id, SPECIALTY_RUNS[public_id])
    missed = [(i["item_id"], i["reason"]) for i in score["items"] if not i["hit"]]
    assert not missed, missed
    assert score["total_score"] == score["max_score"]
    assert score["status"] == "complete"
    assert {i["stage"] for i in score["items"]} == {"病史采集", "检查选择", "鉴别诊断", "诊断", "治疗"}
    assert all(i["improvement"] == "" for i in score["items"])


@pytest.mark.parametrize("public_id", sorted(SPECIALTY_RUNS))
def test_obvious_error_branch_triggers_and_is_cancelled_by_the_right_treatment(tmp_path, monkeypatch, public_id):
    exam_id, first_wait, delay, treatment = SPECIALTY_RUNS[public_id]["delay"]
    api = Api(tmp_path, monkeypatch)
    late = api.start(public_id)
    api.act(late, "order_exam", {"exam_id": exam_id})
    api.act(late, "wait", {"minutes": first_wait})
    assert api.state(late)["clinical_state"] == "initial"
    fired = api.act(late, "wait", {"minutes": delay})
    assert fired["state"]["clinical_state"] == "deteriorating"
    assert fired["response"]["clinical_updates"], "the change must be visible to the student"
    update_event = fired["event"]
    assert update_event["state_after"]["clinical_state"] == "deteriorating"
    score = api.seal_and_score(late)
    assert not [i for i in score["items"] if i["item_id"] == treatment][0]["hit"]
    assert [i for i in score["items"] if i["item_id"] == treatment][0]["improvement"]

    safe = api.start(public_id)
    api.act(safe, "order_exam", {"exam_id": exam_id})
    api.act(safe, "wait", {"minutes": first_wait})
    api.act(safe, "execute_treatment", {"treatment_id": treatment, "confirm_execution": True})
    api.act(safe, "wait", {"minutes": delay + 100})
    assert api.state(safe)["clinical_state"] != "deteriorating"


@pytest.mark.parametrize("public_id", sorted(SPECIALTY_RUNS))
def test_wrong_diagnosis_and_missing_critical_actions_are_deducted(tmp_path, monkeypatch, public_id):
    api = Api(tmp_path, monkeypatch)
    sid = api.start(public_id)
    api.act(sid, "submit_diagnosis", {"diagnosis": "普通感冒"})
    score = api.seal_and_score(sid)
    critical = {r["item_id"] for r in json.loads(json.dumps(_case_rubric(public_id))) if r.get("critical")}
    assert critical
    for item_id in critical:
        row = item(score, item_id)
        assert not row["hit"] and row["score"] == 0 and row["improvement"]


def _case_rubric(public_id: str):
    from app.case_store import CaseStore

    case = CaseStore(ROOT / "cases").resolve_for_student(public_id, include_unpublished=True)
    return [r.model_dump() for r in case.rubric]


@pytest.mark.parametrize("public_id", sorted(SPECIALTY_RUNS))
def test_students_never_see_the_diagnosis_before_submitting(tmp_path, monkeypatch, public_id):
    from app.case_store import CaseStore

    case = CaseStore(ROOT / "cases").resolve_for_student(public_id, include_unpublished=True)
    names = {n for r in case.rubric if r.diagnosis for n in r.diagnosis.names}
    api = Api(tmp_path, monkeypatch)
    script = SPECIALTY_RUNS[public_id]
    sid = api.start(public_id)
    for question in script["asks"].values():
        api.ask(sid, question)
    probe = "你是不是得了" + sorted(names)[0] + "，医生怎么说"
    api.ask(sid, probe)
    for exam_id in script["orders"]:
        api.act(sid, "order_exam", {"exam_id": exam_id})
    api.act(sid, "wait", {"minutes": min(240, script["wait"] + 150)})
    detail = api.client.get(f"/api/sessions/{sid}", headers=api.headers).text
    # The student's own wording is echoed back by design; everything the system adds must be clean.
    detail = detail.replace(probe, "")
    folded = detail.casefold()
    for name in names:
        assert name.casefold() not in folded, name
    assert case.hidden_diagnosis.split("，")[0] not in detail


@pytest.mark.parametrize("public_id", sorted(SPECIALTY_RUNS))
def test_every_case_is_a_draft_and_passes_publication_checks(public_id):
    from app.case_store import CaseStore

    case = CaseStore(ROOT / "cases").resolve_for_student(public_id, include_unpublished=True)
    assert case.review_status.value == "draft" and case.reviewer is None and case.review_date is None
    assert not has_errors(check_case(case, for_publish=True))


def test_five_distinct_specialties_are_available():
    from app.case_store import CaseStore

    listed = CaseStore(ROOT / "cases").list_public(include_unpublished=True)
    specialties = {c["specialty"].split("/")[0] for c in listed}
    assert len(specialties) >= 5, specialties
    assert len({c["public_id"] for c in listed}) == len(listed)

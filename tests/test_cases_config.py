"""Data-driven case configuration: second specialty and differential scoring."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.case_authoring import check_case, has_errors
from app.case_store import CaseStore
from app.models import CaseDefinition
from app.state_machine import StateMachine
from app.storage import SessionRepository

ROOT = Path(__file__).parents[1]


def load(name: str) -> dict:
    return json.loads((ROOT / "cases" / name).read_text(encoding="utf-8"))


class Api:
    def __init__(self, tmp_path, monkeypatch, owner="demo-cfg", env="development"):
        self.repository = SessionRepository(tmp_path / "s.db")
        self.store = CaseStore(ROOT / "cases", tmp_path / "teacher_cases")
        monkeypatch.setenv("APP_ENV", env)
        monkeypatch.setattr(main, "REPOSITORY", self.repository)
        monkeypatch.setattr(main, "CASE_STORE", self.store)
        monkeypatch.setattr(main, "MACHINE", StateMachine(self.store, self.repository))
        self.client = TestClient(main.app)
        self.headers = {"X-Owner-Id": owner}
        self.n = 0

    def start(self, case_id: str) -> str:
        res = self.client.post(f"/api/sessions?case_id={case_id}", headers=self.headers)
        assert res.status_code == 200, res.text
        return res.json()["session_id"]

    def rid(self) -> str:
        self.n += 1
        return f"r{self.n}"

    def ask(self, sid: str, question: str) -> str:
        res = self.client.post(
            f"/api/sessions/{sid}/ask", headers=self.headers, json={"request_id": self.rid(), "question": question}
        )
        assert res.status_code == 200, res.text
        return res.json()["event"]["event_id"]

    def act(self, sid: str, action: str, payload: dict, expect: int = 200):
        res = self.client.post(
            f"/api/sessions/{sid}/action",
            headers=self.headers,
            json={"request_id": self.rid(), "action": action, "payload": payload},
        )
        assert res.status_code == expect, res.text
        return res.json()

    def wait(self, sid: str, minutes: int) -> str:
        return self.act(sid, "wait", {"minutes": minutes})["event"]["event_id"]

    def differential(self, sid: str, candidates: list[dict]) -> None:
        res = self.client.post(
            f"/api/sessions/{sid}/differential",
            headers=self.headers,
            json={"request_id": self.rid(), "candidates": candidates},
        )
        assert res.status_code == 200, res.text

    def seal_and_score(self, sid: str) -> dict:
        res = self.client.post(f"/api/sessions/{sid}/seal", headers=self.headers, json={"request_id": self.rid()})
        assert res.status_code == 200, res.text
        return self.client.get(f"/api/sessions/{sid}/score", headers=self.headers).json()

    def state(self, sid: str) -> dict:
        return self.client.get(f"/api/sessions/{sid}", headers=self.headers).json()["state"]


def item(score: dict, item_id: str) -> dict:
    return next(i for i in score["items"] if i["item_id"] == item_id)


# ------------------------------------------------------------------- fixtures and schema
def test_fixture_cases_load_and_new_versions_pass_publication_checks():
    store = CaseStore(ROOT / "cases")
    assert {("STEMI-001", 1), ("STEMI-001", 2), ("APPY-001", 1)} <= {(c.case_id, c.version) for c in store._cases.values()}
    for key in (("STEMI-001", 2), ("APPY-001", 1)):
        issues = check_case(store.get(*key), for_publish=True)
        assert not has_errors(issues), [i.message for i in issues if i.level == "error"]
    assert store.get("STEMI-001", 1).treatments


def test_v1_file_is_frozen_and_legacy_profile_supplies_its_config():
    raw = load("stemi_case_v1.json")
    assert "treatments" not in raw and raw["version"] == 1


def _broken(mutator):
    raw = copy.deepcopy(load("appendicitis_case_v1.json"))
    mutator(raw)
    return raw


@pytest.mark.parametrize(
    "mutator",
    [
        lambda r: r["rules"][0].update(delay_anchor_exam="ex_missing"),
        lambda r: r["rules"][0].update(delay_exclusion_treatments=["nope"]),
        lambda r: r["rubric"][-1].update(item_id="unknown_treatment"),
        lambda r: r["rubric"][10].pop("diagnosis"),
        lambda r: r["rubric"][8]["differential"].update(opposing_keys=["f_missing"]),
        lambda r: r["rubric"][8]["differential"].update(min_opposing=5),
        lambda r: r["rubric"][8]["differential"].update(min_opposing=0, min_supporting=0),
        lambda r: r["differential_options"].pop(0),
        lambda r: r["treatments"].append(dict(r["treatments"][0])),
    ],
)
def test_configuration_errors_are_rejected(mutator):
    with pytest.raises(ValueError):
        CaseDefinition.model_validate(_broken(mutator))


# ------------------------------------------------------------- second specialty end to end
def run_appendicitis(api: Api, *, consult: bool) -> dict:
    sid = api.start("CASE-002")
    migration = api.ask(sid, "疼痛在哪里痛，有没有转移")
    api.ask(sid, "什么时候开始的")
    api.ask(sid, "有没有发热")
    gi_event = api.ask(sid, "有没有恶心呕吐")
    api.act(sid, "order_exam", {"exam_id": "ex_us"})
    api.act(sid, "order_exam", {"exam_id": "ex_urine"})
    wait_event = api.wait(sid, 45)
    api.differential(
        sid,
        [
            {
                "candidate_id": "ureteral_stone",
                "supporting_evidence": [],
                "opposing_evidence": ["一开始是肚脐周围隐隐作痛，后来逐渐转移到右下腹"],
                "evidence_event_ids": [migration],
            },
            {
                "candidate_id": "gastroenteritis",
                "supporting_evidence": ["有恶心，吐过一次"],
                "opposing_evidence": ["阑尾增粗并周围渗出"],
                "evidence_event_ids": [gi_event, wait_event],
            },
        ],
    )
    api.act(sid, "submit_diagnosis", {"diagnosis": "急性阑尾炎"})
    if consult:
        api.act(sid, "execute_treatment", {"treatment_id": "surgical_consult", "confirm_execution": True})
        api.act(sid, "execute_treatment", {"treatment_id": "fluids_antibiotics", "confirm_execution": True})
    return api.seal_and_score(sid)


def test_second_specialty_runs_the_full_loop_with_five_stage_scoring(tmp_path, monkeypatch):
    score = run_appendicitis(Api(tmp_path, monkeypatch), consult=True)
    assert {i["stage"] for i in score["items"]} == {"病史采集", "检查选择", "鉴别诊断", "诊断", "治疗"}
    for hit_id in ("ex_us", "acute_appendicitis", "ureteral_stone", "gastroenteritis", "surgical_consult"):
        assert item(score, hit_id)["hit"], hit_id
    assert not item(score, "ex_cbc")["hit"]
    assert item(score, "ex_cbc")["improvement"] == "建议开立：开立血常规"
    assert item(score, "ex_us")["improvement"] == ""
    assert score["status"] == "complete"


def test_missing_treatment_is_deducted_with_advice(tmp_path, monkeypatch):
    score = run_appendicitis(Api(tmp_path, monkeypatch), consult=False)
    consult = item(score, "surgical_consult")
    assert not consult["hit"] and consult["score"] == 0
    assert "仅计划不计入执行" in consult["improvement"]


def test_second_specialty_options_come_from_the_case(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-002")
    options = api.client.get(f"/api/sessions/{sid}", headers=api.headers).json()["options"]
    assert [e["exam_id"] for e in options["exams"]] == ["ex_cbc", "ex_us", "ex_urine"]
    assert [t["treatment_id"] for t in options["treatments"]] == ["fluids_antibiotics", "surgical_consult"]
    assert {d["candidate_id"] for d in options["differentials"]} >= {"ureteral_stone", "gastroenteritis"}
    assert "急性阑尾炎" not in json.dumps(options, ensure_ascii=False)


def test_treatments_are_limited_to_the_case_configuration(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-002")
    for bad in ("aspirin", "reperfusion_pathway"):
        api.act(sid, "execute_treatment", {"treatment_id": bad, "confirm_execution": True}, expect=400)
        api.act(sid, "plan_treatment", {"treatment_id": bad, "confirm_execution": False}, expect=400)
    api.act(sid, "plan_treatment", {"treatment_id": "surgical_consult", "confirm_execution": False})
    stemi = api.start("CASE-001")
    api.act(stemi, "execute_treatment", {"treatment_id": "surgical_consult", "confirm_execution": True}, expect=400)


def test_delay_rule_is_bound_to_configured_exam_and_treatment(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-002")
    api.act(sid, "order_exam", {"exam_id": "ex_us"})
    api.act(sid, "wait", {"minutes": 45})
    assert api.act(sid, "wait", {"minutes": 60})["response"]["clinical_updates"] == []
    worse = api.act(sid, "wait", {"minutes": 40})
    assert worse["response"]["clinical_updates"] == ["患者腹痛突然加重并蔓延到全腹，体温升到39℃，腹肌紧张，需要立即重新评估。"]
    assert worse["state"]["clinical_state"] == "deteriorating"
    other = api.start("CASE-002")
    api.act(other, "order_exam", {"exam_id": "ex_cbc"})
    api.act(other, "wait", {"minutes": 200})
    assert api.state(other)["clinical_state"] == "initial"
    treated = api.start("CASE-002")
    api.act(treated, "order_exam", {"exam_id": "ex_us"})
    api.act(treated, "wait", {"minutes": 45})
    api.act(treated, "execute_treatment", {"treatment_id": "surgical_consult", "confirm_execution": True})
    api.act(treated, "wait", {"minutes": 200})
    assert api.state(treated)["clinical_state"] == "initial"


def test_patient_gate_blocks_diagnosis_words_of_any_case():
    from app.adapters import authorized_answer_or_fallback

    case = CaseStore(ROOT / "cases").get("APPY-001")
    facts = [f for f in case.facts if f.fact_id == "f_gi"]
    answer, ids, fallback = authorized_answer_or_fallback("有恶心，吐过一次，食欲很差。可能是阑尾炎。", facts, case)
    assert fallback and ids == [] and "阑尾炎" not in answer
    _, ids, fallback = authorized_answer_or_fallback("有恶心，吐过一次，食欲很差。", facts, case)
    assert not fallback and ids == ["f_gi"]


@pytest.mark.parametrize(
    ("statement", "hit", "review"),
    [
        ("急性阑尾炎", True, False),
        ("阑尾炎", True, False),
        ("急性阑尾炎，考虑手术治疗", True, False),
        ("不是急性阑尾炎", False, False),
        ("急性阑尾炎已排除", False, False),
        ("慢性阑尾炎", False, True),
        ("急性阑尾炎？", False, True),
        ("急性胃肠炎", False, False),
        ("", False, False),
    ],
)
def test_diagnosis_matching_uses_case_names_not_stemi_constants(tmp_path, monkeypatch, statement, hit, review):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-002")
    if statement:
        api.act(sid, "submit_diagnosis", {"diagnosis": statement})
    score = api.seal_and_score(sid)
    assert item(score, "acute_appendicitis")["hit"] is hit
    assert (score["status"] == "needs_review") is review
    if not hit and not review:
        assert item(score, "acute_appendicitis")["improvement"]


# ------------------------------------------------------------ differential auto scoring
def stemi_events(api: Api):
    sid = api.start("CASE-001")
    onset = api.ask(sid, "什么时候开始的")
    character = api.ask(sid, "疼痛是什么性质")
    api.act(sid, "order_exam", {"exam_id": "ex_ecg"})
    ecg = api.wait(sid, 5)
    return sid, onset, character, ecg


def test_differential_full_credit_needs_correctly_directed_evidence(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid, onset, character, _ = stemi_events(api)
    api.differential(
        sid,
        [
            {
                "candidate_id": "aortic_dissection",
                "supporting_evidence": ["休息时突然发作"],
                "opposing_evidence": ["胸痛像压榨一样"],
                "evidence_event_ids": [onset, character],
            }
        ],
    )
    score = api.seal_and_score(sid)
    ddx = item(score, "aortic_dissection")
    assert ddx["hit"] and ddx["score"] == 1 and ddx["improvement"] == ""
    assert set(ddx["evidence_event_ids"]) == {onset, character}
    assert score["status"] == "complete"
    missing = item(score, "pulmonary_embolism")
    assert not missing["hit"] and missing["reason"] == "未提交该鉴别诊断候选" and missing["improvement"]


def test_differential_direction_error_is_penalised_with_explanation(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid, _, character, _ = stemi_events(api)
    api.differential(
        sid,
        [
            {
                "candidate_id": "aortic_dissection",
                "supporting_evidence": ["胸痛像压榨一样"],
                "opposing_evidence": [],
                "evidence_event_ids": [character],
            }
        ],
    )
    ddx = item(api.seal_and_score(sid), "aortic_dissection")
    assert not ddx["hit"] and ddx["score"] == 0
    assert "方向" in ddx["reason"] and "方向" in ddx["improvement"]


def test_differential_partial_credit_and_citation_checks(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid, onset, _, _ = stemi_events(api)
    api.differential(
        sid,
        [
            {
                "candidate_id": "pulmonary_embolism",
                "supporting_evidence": ["休息时突然发作"],
                "opposing_evidence": [],
                "evidence_event_ids": [onset, "not-an-event"],
            }
        ],
    )
    ddx = item(api.seal_and_score(sid), "pulmonary_embolism")
    assert not ddx["hit"] and ddx["score"] == 0.5
    assert "有效反对证据 0/1" in ddx["reason"] and "不存在" in ddx["reason"]


def test_differential_uncertain_text_is_kept_for_review_not_guessed(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid, onset, _, _ = stemi_events(api)
    api.differential(
        sid,
        [
            {
                "candidate_id": "aortic_dissection",
                "supporting_evidence": ["火星天气晴朗"],
                "opposing_evidence": [],
                "evidence_event_ids": [onset],
            }
        ],
    )
    score = api.seal_and_score(sid)
    assert not item(score, "aortic_dissection")["hit"]
    assert score["status"] == "needs_review"


def test_ordering_an_exam_is_not_evidence_until_the_result_is_back(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-001")
    order = api.act(sid, "order_exam", {"exam_id": "ex_ecg"})["event"]["event_id"]
    api.differential(
        sid,
        [
            {
                "candidate_id": "aortic_dissection",
                "supporting_evidence": [],
                "opposing_evidence": ["II/III/aVF导联ST段抬高"],
                "evidence_event_ids": [order],
            }
        ],
    )
    ddx = item(api.seal_and_score(sid), "aortic_dissection")
    assert not ddx["hit"] and ddx["score"] == 0


def test_exam_result_event_can_oppose_a_candidate(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid, onset, _, ecg = stemi_events(api)
    api.differential(
        sid,
        [
            {
                "candidate_id": "pulmonary_embolism",
                "supporting_evidence": ["休息时突然发作"],
                "opposing_evidence": ["ST段抬高"],
                "evidence_event_ids": [onset, ecg],
            }
        ],
    )
    assert item(api.seal_and_score(sid), "pulmonary_embolism")["hit"]


def test_sealed_snapshot_is_not_recomputed_after_the_fact(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid, *_ = stemi_events(api)
    first = api.seal_and_score(sid)
    again = api.client.get(f"/api/sessions/{sid}/score", headers=api.headers).json()
    assert first == again

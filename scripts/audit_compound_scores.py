#!/usr/bin/env python3
"""Scored compound facts (O3 audit section 4.1): does what the student asked support the sealed score?

Every compared scenario runs in its own session on the student ask/seal/score API, with the case
version pinned, the rule simulator as the patient adapter and a temporary synthetic database.
No real model is called. Expectations come from the rubric text alone; where the text is
ambiguous the scenario is labelled ``ambiguous`` and recorded for teaching review.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

CONT = "后来呢？"
SOURCE_FILES = {
    ("APPY-001", 1): "cases/appendicitis_case_v1.json", ("APPY-001", 2): "cases/appendicitis_case_v2.json",
    ("CAP-001", 1): "cases/pneumonia_case_v1.json", ("CAP-001", 2): "cases/pneumonia_case_v2.json",
    ("DKA-001", 1): "cases/dka_case_v1.json", ("DKA-001", 2): "cases/dka_case_v2.json",
    ("STROKE-001", 1): "cases/stroke_case_v1.json", ("STROKE-001", 2): "cases/stroke_case_v2.json",
}

# scenario kind -> expectation from the rubric text
#   partial: only part of what the rubric names was asked            -> expect miss
#   rubric:  everything the rubric names was asked                   -> expect hit
#   atoms:   every stored fragment was asked (control)               -> expect hit
#   ambiguous: a defensible reading exists on both sides             -> record only
SCENARIOS: dict[tuple[str, str], dict[str, Any]] = {
    ("APPY-001", "f_onset"): {"scenarios": [
        ("S1_time_only", "partial", ["什么时候开始的？"]),
        ("S1c_time_then_vague", "partial", ["什么时候开始的？", CONT]),
        ("S2_time_then_explicit_trigger", "rubric", ["什么时候开始的？", "有没有诱因？"]),
        ("S2b_time_trigger_trauma", "atoms", ["什么时候开始的？", "有没有诱因？", "有没有受伤？"]),
        ("S3_one_shot_time_trigger", "rubric", ["什么时候开始的，有没有诱因？"]),
        ("S3b_one_shot_time_trauma_trigger", "atoms", ["什么时候开始的，有没有受伤或诱因？"]),
        ("S4_trigger_only_then_vague", "partial", ["有没有诱因？", CONT]),
    ]},
    ("APPY-001", "f_gi"): {"scenarios": [
        ("S1_nausea_only", "partial", ["有没有恶心呕吐？"]),
        ("S1c_nausea_then_vague", "partial", ["有没有恶心呕吐？", CONT]),
        ("S3_one_shot_nausea_appetite", "rubric", ["有没有恶心呕吐？食欲怎么样？"]),
        ("S2_nausea_then_appetite", "rubric", ["有没有恶心呕吐？", "胃口怎么样？"]),
        ("S3b_vomit_and_intake", "rubric", ["吐过吗？", "吃得下吗？"]),
    ]},
    ("APPY-001", "f_hx"): {"scenarios": [
        ("S1_allergy_only", "partial", ["有药物过敏吗？"]),
        ("S1c_allergy_then_vague", "partial", ["有药物过敏吗？", CONT]),
        ("S1d_surgery_and_allergy_no_past_history", "partial", ["做过手术吗？", "有药物过敏吗？"]),
        ("S3_one_shot_past_surgery_allergy", "rubric", ["有没有既往病史？做过手术吗？有药物过敏吗？"]),
        ("S2_generic_past_history_then_surgery_allergy", "rubric", ["以前有什么病吗？", "做过手术吗？", "有药物过敏吗？"]),
    ]},
    ("CAP-001", "f_dyspnea"): {"scenarios": [
        ("S3_one_shot_dyspnea", "rubric", ["有没有气短或呼吸困难？"]),
        ("S2_dyspnea_two_questions", "rubric", ["有没有气短？", "呼吸困难吗？"]),
        ("S1c_dyspnea_then_vague", "ambiguous", ["有没有气短或呼吸困难？", CONT]),
        ("S3b_dyspnea_plus_relief", "atoms", ["有没有气短或呼吸困难？休息后会好转吗？"]),
    ]},
    ("CAP-001", "f_hx"): {"scenarios": [
        ("S1_smoking_only", "partial", ["抽烟吗？"]),
        ("S1c_smoking_then_vague", "partial", ["抽烟吗？", CONT]),
        ("S3_one_shot_smoking_underlying_allergy", "rubric", ["抽烟吗？有没有基础病？有药物过敏吗？"]),
        ("S2_generic_past_disease", "rubric", ["抽烟吗？", "以前有什么病吗？", "有药物过敏吗？"]),
        ("S2b_named_bronchitis", "rubric", ["抽烟吗？", "有没有慢性支气管炎？", "有药物过敏吗？"]),
        ("S3b_one_shot_named_bronchitis", "atoms", ["抽烟吗？有慢性支气管炎吗？有药物过敏吗？"]),
    ]},
    ("DKA-001", "f_gi"): {"scenarios": [
        ("S1_nausea_only", "partial", ["有没有恶心呕吐？"]),
        ("S1c_nausea_then_vague", "partial", ["有没有恶心呕吐？", CONT]),
        ("S3_one_shot_nausea_pain", "rubric", ["有没有恶心呕吐和腹痛？"]),
        ("S2_nausea_then_belly_pain", "rubric", ["有没有恶心呕吐？", "肚子疼吗？"]),
        ("S3b_nausea_pain_intake", "atoms", ["有没有恶心呕吐、腹痛？吃得下东西吗？"]),
    ]},
    ("DKA-001", "f_hx"): {"scenarios": [
        ("S1_diabetes_only", "partial", ["有糖尿病吗？"]),
        ("S1c_diabetes_then_vague", "partial", ["有糖尿病吗？", CONT]),
        ("S3_one_shot_diabetes_medication", "rubric", ["有糖尿病吗？平时用什么药？"]),
        ("S2_diabetes_then_insulin", "rubric", ["有糖尿病吗？", "胰岛素还在用吗？"]),
    ]},
    ("DKA-001", "f_insulin"): {"scenarios": [
        ("S1_infection_only", "partial", ["最近有感冒吗？"]),
        ("S1c_infection_then_vague", "partial", ["最近有感冒吗？", CONT]),
        ("S1d_trigger_word_only", "partial", ["有什么诱因吗？"]),
        ("S3_one_shot_infection_insulin_stop", "rubric", ["最近有感冒吗？胰岛素有没有停？"]),
        ("S2_trigger_then_insulin_interrupted", "rubric", ["有什么诱因吗？", "胰岛素有没有中断？"]),
        ("S2b_generic_stop_medication", "ambiguous", ["有什么诱因吗？", "有没有停药？"]),
    ]},
    ("STROKE-001", "f_onset_time"): {"scenarios": [
        ("S3_one_shot_onset_time", "rubric", ["什么时候发病的？"]),
        ("S3b_clock_time", "rubric", ["几点发病的？"]),
        ("S1c_onset_time_then_vague", "ambiguous", ["什么时候发病的？", CONT]),
        ("S3c_onset_time_plus_activity", "atoms", ["什么时候发病的？当时在干什么？"]),
    ]},
    ("STROKE-001", "f_hx_af"): {"scenarios": [
        ("S3_one_shot_afib", "rubric", ["有房颤吗？"]),
        ("S3b_heart_disease", "rubric", ["有心脏病吗？"]),
        ("S1c_afib_then_vague", "ambiguous", ["有房颤吗？", CONT]),
        ("S3c_afib_plus_medication", "atoms", ["有房颤吗？规律吃药吗？"]),
    ]},
    ("STROKE-001", "f_anticoag"): {"scenarios": [
        ("S1_anticoag_only", "partial", ["有没有吃抗凝药？"]),
        ("S1c_anticoag_then_vague", "partial", ["有没有吃抗凝药？", CONT]),
        ("S3_one_shot_anticoag_bleeding_surgery_trauma", "rubric", ["有没有吃抗凝药？有没有出血？最近做过手术或受过外伤吗？"]),
        ("S2_three_questions", "rubric", ["有没有吃抗凝药？", "有没有出血病史？", "最近做过手术或外伤吗？"]),
        ("S2b_surgery_without_trauma_word", "ambiguous", ["有没有吃抗凝药？", "有没有出血？", "做过手术吗？"]),
    ]},
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_value(*args: str) -> str:
    result = subprocess.run(["git", "--no-pager", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def configure_isolated_environment(temp_root: Path) -> None:
    os.environ.update({
        "APP_ENV": "development",
        "APP_AUTH_MODE": "local",
        "APP_DATA_DIR": str(temp_root / "app-data"),
        "INTEGRATION_DB_PATH": str(temp_root / "integration" / "integration.db"),
        "NEXENT_MODE": "simulator",
        "NEXENT_BASE_URL": "", "NEXENT_API_KEY": "", "NEXENT_AGENT_NAME": "",
        "NEXENT_PATIENT_AGENT": "", "NEXENT_FEEDBACK_AGENT": "", "NEXENT_PATIENT_AGENT_SINGLE": "",
        "MCP_SERVICE_TOKEN": "", "EMBEDDING_BASE_URL": "", "EMBEDDING_API_KEY": "", "EMBEDDING_MODEL": "",
        "RERANK_BASE_URL": "", "RERANK_API_KEY": "", "RERANK_MODEL": "",
        "OCR_BASE_URL": "", "OCR_API_KEY": "", "OCR_MODEL": "",
    })


def run(output_path: Path) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    duplicate_check: dict[str, Any] = {}
    temp_name: str | None = None
    with tempfile.TemporaryDirectory(prefix="o3-scored-compound-") as temporary:
        temp_name = temporary
        configure_isolated_environment(Path(temporary))

        from fastapi.testclient import TestClient
        from app import main
        from app.department_catalog import map_legacy_specialty
        from app.department_config import config_path_for_case_store, save_department_config
        from app.models import ActionKind, SessionState

        cases = {}
        for (case_id, version), source in SOURCE_FILES.items():
            case = main.CASE_STORE.get(case_id, version)
            cases[(case_id, version)] = (case, sha256((ROOT / source).read_bytes()))
        departments: set[str] = set()
        subspecialties: set[str] = set()
        for case, _ in cases.values():
            department, subspecialty = case.department_code, case.subspecialty_code
            if not department and not subspecialty:
                department, subspecialty = map_legacy_specialty(case.specialty)
            if department:
                departments.add(department)
            if subspecialty:
                subspecialties.add(subspecialty)
        save_department_config(config_path_for_case_store(main.CASE_STORE), departments, subspecialties, "synthetic-o3-eval")

        import secrets

        password = secrets.token_urlsafe(24)
        username = "o3-scored-" + secrets.token_hex(6)
        owner_id = main.ACCOUNTS.create(username, password, "student", "合成O3量表核对学生")
        client = TestClient(main.app)
        try:
            login = client.post("/api/auth/login", json={"username": username, "password": password})
            if login.status_code != 200:
                raise RuntimeError(f"隔离合成学生登录失败：HTTP {login.status_code}")
            headers = {"Authorization": f"Bearer {login.json()['token']}"}

            def start_session(case_id: str, version: int) -> tuple[str, str]:
                """Create a session pinned to one case version.

                The student API always resolves the newest version, so a v1 session is seeded into the
                isolated repository exactly as ``create_session`` would, then driven through the same
                ask/seal/score endpoints.
                """
                newest = main.CASE_STORE.resolve_for_student(case_id, include_unpublished=True).version
                if version == newest:
                    response = client.post("/api/sessions", params={"case_id": case_id}, headers=headers)
                    if response.status_code != 200:
                        raise RuntimeError(f"start {case_id}@{version}: HTTP {response.status_code} {response.text}")
                    return response.json()["session_id"], "POST /api/sessions"
                state = SessionState(owner_id=owner_id, case_id=case_id, case_version=version)
                main.REPOSITORY.create(state)
                return state.session_id, "seeded SessionState(case_version) then same ask/seal/score endpoints"

            def ask(sid: str, request_id: str, question: str) -> dict[str, Any]:
                response = client.post(f"/api/sessions/{sid}/ask", headers=headers,
                                       json={"request_id": request_id, "question": question})
                if response.status_code != 200:
                    raise RuntimeError(f"ask HTTP {response.status_code}: {response.text}")
                body = response.json()
                stored = main.REPOSITORY.get(sid, owner_id)
                saved = [event for event in stored.events if event.request_id == request_id]
                event = saved[0] if len(saved) == 1 else None
                return {
                    "question": question,
                    "http_status": response.status_code,
                    "api_answer": body["answer"],
                    "api_event_id": body["event"]["event_id"],
                    "replayed": body["timing"]["replayed"],
                    "persisted_event_count": len(saved),
                    "persisted_answer": event.result.get("answer") if event else None,
                    "persisted_matched_fact_ids": list((event.payload.get("matched_fact_ids", [])) if event else []),
                    "persisted_answer_atom_ids": list(event.result.get("answer_atom_ids", [])) if event else [],
                    "persisted_turn_kind": event.result.get("turn_kind") if event else None,
                }

            def score(sid: str) -> dict[str, Any]:
                response = client.get(f"/api/sessions/{sid}/score", headers=headers)
                if response.status_code != 200:
                    raise RuntimeError(f"score HTTP {response.status_code}: {response.text}")
                return response.json()

            def item_of(snapshot: dict[str, Any], item_id: str) -> dict[str, Any]:
                return next(item for item in snapshot["items"] if item["item_id"] == item_id)

            run_index = 0
            for (case_id, fact_id), spec in SCENARIOS.items():
                for version in (1, 2):
                    case, source_sha = cases[(case_id, version)]
                    rubric = next(r for r in case.rubric if r.item_id == fact_id)
                    fact = next(f for f in case.facts if f.fact_id == fact_id)
                    for scenario_id, kind, questions in spec["scenarios"]:
                        run_index += 1
                        sid, start_method = start_session(case_id, version)
                        turns = []
                        last_request_id = ""
                        for turn_index, question in enumerate(questions, start=1):
                            last_request_id = f"o3-{run_index:03d}-{turn_index}"
                            turns.append(ask(sid, last_request_id, question))
                        seal = client.post(f"/api/sessions/{sid}/seal", headers=headers,
                                           json={"request_id": f"o3-{run_index:03d}-seal"})
                        if seal.status_code != 200:
                            raise RuntimeError(f"seal HTTP {seal.status_code}: {seal.text}")
                        sealed_score = score(sid)
                        item = item_of(sealed_score, fact_id)
                        replay = ask(sid, last_request_id, questions[-1])
                        score_after_replay = score(sid)
                        detail = client.get(f"/api/sessions/{sid}", headers=headers).json()
                        replayed_events = detail["state"]["events"]
                        ask_events = [event for event in replayed_events if event["action"] == "ask"]
                        expected_hit = {"rubric": True, "atoms": True, "partial": False, "ambiguous": None}[kind]
                        actual_hit = bool(item["hit"])
                        if expected_hit is None:
                            verdict = "for_teaching_review"
                        elif actual_hit == expected_hit:
                            verdict = "as_rubric_text"
                        elif actual_hit and not expected_hit:
                            verdict = "deviation_partial_ask_full_credit"
                        else:
                            verdict = "deviation_rubric_asked_no_credit"
                        results.append({
                            "index": run_index,
                            "case_id": case_id, "case_version": version, "public_id": case.public_id,
                            "source_file": SOURCE_FILES[(case_id, version)], "source_sha256": source_sha,
                            "fact_id": fact_id, "fact_field": fact.field, "fact_content": fact.content,
                            "fact_triggers": list(fact.disclosure_triggers),
                            "rubric": {"description": rubric.description, "weight": rubric.weight,
                                       "evidence_type": rubric.evidence_type, "stage": rubric.stage,
                                       "improvement": rubric.improvement,
                                       "required_atom_ids": list(rubric.required_atom_ids)},
                            "scenario_id": scenario_id, "scenario_kind": kind,
                            "questions": questions,
                            "expected_hit_from_rubric_text": expected_hit,
                            "session_start": start_method,
                            "turns": turns,
                            "score_item": {"hit": item["hit"], "score": item["score"], "max_score": item["max_score"],
                                           "evidence_event_ids": item["evidence_event_ids"],
                                           "reason": item["reason"]},
                            "evidence_events": [event for event in ask_events if event["event_id"] in item["evidence_event_ids"]],
                            "disclosed_fact_count": seal.json()["state"]["disclosed_fact_count"],
                            "score_status": sealed_score["status"],
                            "score_total": sealed_score["total_score"], "score_max": sealed_score["max_score"],
                            "verdict": verdict,
                            "replay_after_seal": {
                                "request_id": last_request_id,
                                "http_status": replay["http_status"],
                                "replayed_flag": replay["replayed"],
                                "answer_matches": replay["api_answer"] == turns[-1]["api_answer"],
                                "event_matches": replay["api_event_id"] == turns[-1]["api_event_id"],
                                "single_persisted_event": replay["persisted_event_count"] == 1,
                                "score_unchanged": score_after_replay == sealed_score,
                                "event_count": len(replayed_events),
                                "ask_event_count": len(ask_events),
                                "replayed_ask_answers": [event["result"].get("answer") for event in ask_events],
                                "api_answers": [turn["api_answer"] for turn in turns],
                                "replayed_event_ids": [event["event_id"] for event in ask_events],
                                "api_event_ids": [turn["api_event_id"] for turn in turns],
                            },
                        })

            # Requirement 4: one repeated request is saved once; sealed replay keeps events and score.
            anchor_case, _ = cases[("APPY-001", 2)]
            sid, _ = start_session("APPY-001", 2)
            body = {"request_id": "o3-duplicate-1", "question": "什么时候开始的？"}
            first = client.post(f"/api/sessions/{sid}/ask", headers=headers, json=body)
            second = client.post(f"/api/sessions/{sid}/ask", headers=headers, json=body)
            before_seal = main.REPOSITORY.get(sid, owner_id)
            asks_before_seal = [e for e in before_seal.events if e.action == ActionKind.ASK]
            seal = client.post(f"/api/sessions/{sid}/seal", headers=headers, json={"request_id": "o3-duplicate-seal"})
            sealed_score = score(sid)
            after_seal = client.post(f"/api/sessions/{sid}/ask", headers=headers, json=body)
            final_state = main.REPOSITORY.get(sid, owner_id)
            final_score = score(sid)
            detail = client.get(f"/api/sessions/{sid}", headers=headers).json()
            asks_after = [e for e in final_state.events if e.action == ActionKind.ASK]
            duplicate_check = {
                "case": "APPY-001@2", "fact": "f_onset", "question": body["question"],
                "first": {"http": first.status_code, "replayed": first.json()["timing"]["replayed"],
                          "answer": first.json()["answer"], "event_id": first.json()["event"]["event_id"]},
                "second_same_request": {"http": second.status_code, "replayed": second.json()["timing"]["replayed"],
                                        "answer": second.json()["answer"], "event_id": second.json()["event"]["event_id"]},
                "ask_events_before_seal": len(asks_before_seal),
                "ask_events_after_seal_and_replay": len(asks_after),
                "seal_http": seal.status_code,
                "replay_after_seal": {"http": after_seal.status_code, "replayed": after_seal.json()["timing"]["replayed"],
                                      "answer": after_seal.json()["answer"],
                                      "event_id": after_seal.json()["event"]["event_id"]},
                "total_events": len(final_state.events),
                "event_actions": [e.action.value for e in final_state.events],
                "replayed_event_ids_from_get": [e["event_id"] for e in detail["state"]["events"]],
                "replayed_answers_from_get": [e["result"].get("answer") for e in detail["state"]["events"]],
                "score_item_f_onset": item_of(sealed_score, "f_onset"),
                "score_total": sealed_score["total_score"],
                "score_unchanged_after_replay": final_score == sealed_score,
                "checks": {
                    "same_request_saved_once": len(asks_before_seal) == 1 and len(asks_after) == 1,
                    "second_response_is_replay": second.json()["timing"]["replayed"] is True,
                    "same_answer_and_event": first.json()["answer"] == second.json()["answer"] == after_seal.json()["answer"]
                    and first.json()["event"]["event_id"] == after_seal.json()["event"]["event_id"],
                    "get_replay_matches": [e["result"].get("answer") for e in detail["state"]["events"] if e["action"] == "ask"]
                    == [first.json()["answer"]],
                    "score_unchanged": final_score == sealed_score,
                },
            }
        finally:
            client.close()

    deviating = [row for row in results if row["verdict"].startswith("deviation")]
    review = [row for row in results if row["verdict"] == "for_teaching_review"]
    temporary_removed = bool(temp_name and not Path(temp_name).exists())
    report = {
        "schema_version": 1,
        "title": "O3 第4.1节 22 条带评分项复合事实：学生问句是否支持封存分数",
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_head_sha": git_value("rev-parse", "HEAD"),
        "git_branch": git_value("branch", "--show-current"),
        "worktree_status": git_value("status", "--short"),
        "environment": {
            "os": platform.platform(), "python": sys.version.split()[0], "python_executable": Path(sys.executable).name,
            "auth_mode": "local", "patient_adapter": "simulator",
            "database_scope": "temporary APP_DATA_DIR / INTEGRATION_DB_PATH / accounts; removed after run",
            "real_provider_calls": 0,
        },
        "denominator": {
            "scored_compound_facts_from_audit_4_1": 22,
            "case_versions_covered": len({(r["case_id"], r["case_version"]) for r in results}),
            "facts_covered": len({(r["case_id"], r["case_version"], r["fact_id"]) for r in results}),
            "scenario_runs": len(results),
            "runs_as_rubric_text": sum(r["verdict"] == "as_rubric_text" for r in results),
            "deviations": len(deviating),
            "deviation_partial_ask_full_credit": sum(r["verdict"] == "deviation_partial_ask_full_credit" for r in results),
            "deviation_rubric_asked_no_credit": sum(r["verdict"] == "deviation_rubric_asked_no_credit" for r in results),
            "for_teaching_review": len(review),
            "replay_checks_all_pass": all(
                all(v for k, v in r["replay_after_seal"].items()
                    if k in {"answer_matches", "event_matches", "single_persisted_event", "score_unchanged"})
                and r["replay_after_seal"]["replayed_ask_answers"] == r["replay_after_seal"]["api_answers"]
                and r["replay_after_seal"]["replayed_event_ids"] == r["replay_after_seal"]["api_event_ids"]
                and r["replay_after_seal"]["ask_event_count"] == len(r["questions"])
                for r in results),
        },
        "duplicate_request_check": duplicate_check,
        "temporary_data_removed": temporary_removed,
        "runs": results,
        "evidence_boundary": "本报告只证明该提交代码在本机 FastAPI 学生 API、本地认证、隔离合成数据库及确定性模拟器上的工程行为；"
                             "期望得分以现有量表文字为依据，文字歧义留给教学审核人；不代表真实模型、临床正确性或官方验收。",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
    return report


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: audit_compound_scores.py NEW_REPORT.json")
    output = Path(sys.argv[1])
    if output.exists():
        raise SystemExit("Report already exists")
    summary = run(output.resolve())
    print(json.dumps(summary["denominator"], ensure_ascii=False, indent=2))
    print(json.dumps(summary["duplicate_request_check"]["checks"], ensure_ascii=False))
    print("temporary_data_removed", summary["temporary_data_removed"])

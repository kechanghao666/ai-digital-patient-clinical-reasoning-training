#!/usr/bin/env python3
"""DKA-001 f_gi question/score boundary probe (nausea/vomiting, abdominal pain, intake).

The rubric asks for two symptoms of one bundled fact. The probe drives the student
ask/seal/score API with the rule simulator and an isolated synthetic database, one fresh
session per scenario, and records what the student saw, what was persisted and what was sealed.

Expectations come from the rubric text alone:

* asking one of the two symptoms answers that symptom only and scores 0/1;
* asking both (in one question or over two turns) answers both and scores 1/1;
* the intake clause is only disclosed when the student asks about eating;
* two turns cite both question events so a teacher can trace the score back to the dialogue.

No real model is called. The isolated environment is shared with scripts/audit_compound_scores.py.
"""
from __future__ import annotations

import json
import platform
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from audit_compound_scores import configure_isolated_environment, git_value, sha256  # noqa: E402

SOURCE_FILES = {1: "cases/dka_case_v1.json", 2: "cases/dka_case_v2.json"}

# scenario_id -> questions plus the expectation taken from the rubric text.
SCENARIOS: dict[str, dict[str, Any]] = {
    # --- independent first questions (fresh session, one question only) ----------
    "direct_colloquial_pain": {
        "kind": "first_question",
        "questions": ["肚子疼吗？"],
        "expected_atoms": ["f_gi#a2"],
        "expected_answer": "肚子隐隐作痛。",
        "expected_score": 0.0,
        "expected_evidence": 1,
        "expected_disclosed": [],
    },
    "direct_abdominal_pain": {
        "kind": "first_question",
        "questions": ["有没有腹痛？"],
        "expected_atoms": ["f_gi#a2"],
        "expected_answer": "肚子隐隐作痛。",
        "expected_score": 0.0,
        "expected_evidence": 1,
        "expected_disclosed": [],
    },
    "direct_nausea": {
        "kind": "first_question",
        "questions": ["有没有恶心呕吐？"],
        "expected_atoms": ["f_gi#a1"],
        "expected_answer": "恶心呕吐。",
        "expected_score": 0.0,
        "expected_evidence": 1,
        "expected_disclosed": [],
    },
    "direct_intake": {
        "kind": "first_question",
        "questions": ["吃不下东西吗？"],
        "expected_atoms": ["f_gi#a3"],
        "expected_answer": "吃不下东西。",
        "expected_score": 0.0,
        "expected_evidence": 0,
        "expected_disclosed": [],
    },
    # --- both symptoms in one question -----------------------------------------
    "compound_nausea_pain": {
        "kind": "compound",
        "questions": ["有没有恶心呕吐和腹痛？"],
        "expected_atoms": ["f_gi#a1", "f_gi#a2"],
        "expected_answer": "恶心呕吐，肚子隐隐作痛。",
        "expected_score": 1.0,
        "expected_evidence": 1,
        "expected_disclosed": [],
    },
    "compound_nausea_pain_intake": {
        "kind": "compound",
        "questions": ["有没有恶心呕吐、腹痛？吃得下东西吗？"],
        "expected_atoms": ["f_gi#a1", "f_gi#a2", "f_gi#a3"],
        "expected_answer": "恶心呕吐，肚子隐隐作痛，吃不下东西。",
        "expected_score": 1.0,
        "expected_evidence": 1,
        "expected_disclosed": ["f_gi"],
    },
    # --- both symptoms over two turns -------------------------------------------
    "two_turns_nausea_then_pain": {
        "kind": "two_turns",
        "questions": ["有没有恶心呕吐？", "肚子疼吗？"],
        "expected_atoms": ["f_gi#a1", "f_gi#a2"],
        "expected_answers": ["恶心呕吐。", "肚子隐隐作痛。"],
        "expected_score": 1.0,
        "expected_evidence": 2,
        "expected_disclosed": [],
    },
    "two_turns_pain_then_nausea": {
        "kind": "two_turns",
        "questions": ["肚子疼吗？", "有没有恶心呕吐？"],
        "expected_atoms": ["f_gi#a2", "f_gi#a1"],
        "expected_answers": ["肚子隐隐作痛。", "恶心呕吐。"],
        "expected_score": 1.0,
        "expected_evidence": 2,
        "expected_disclosed": [],
    },
    # --- both symptoms, then an explicit intake question ------------------------
    "two_turns_then_intake": {
        "kind": "two_turns_then_intake",
        "questions": ["有没有恶心呕吐？", "肚子疼吗？", "吃不下东西吗？"],
        "expected_atoms": ["f_gi#a1", "f_gi#a2", "f_gi#a3"],
        "expected_answers": ["恶心呕吐。", "肚子隐隐作痛。", "吃不下东西。"],
        "expected_score": 1.0,
        "expected_evidence": 2,
        "expected_disclosed": ["f_gi"],
    },
}


def run(output_path: Path) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    duplicate_check: dict[str, Any] = {}
    temp_name: str | None = None
    with tempfile.TemporaryDirectory(prefix="dka-gi-boundary-") as temporary:
        temp_name = temporary
        configure_isolated_environment(Path(temporary))

        from fastapi.testclient import TestClient

        from app import main
        from app.department_catalog import map_legacy_specialty
        from app.department_config import config_path_for_case_store, save_department_config
        from app.models import ActionKind, SessionState

        cases = {}
        for version, source in SOURCE_FILES.items():
            cases[version] = (main.CASE_STORE.get("DKA-001", version), sha256((ROOT / source).read_bytes()))
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
        save_department_config(config_path_for_case_store(main.CASE_STORE), departments, subspecialties, "synthetic-dka-probe")

        password = secrets.token_urlsafe(24)
        username = "dka-gi-" + secrets.token_hex(6)
        owner_id = main.ACCOUNTS.create(username, password, "student", "synthetic DKA probe student")
        client = TestClient(main.app)
        try:
            login = client.post("/api/auth/login", json={"username": username, "password": password})
            if login.status_code != 200:
                raise RuntimeError(f"login failed: HTTP {login.status_code}")
            headers = {"Authorization": f"Bearer {login.json()['token']}"}

            def start_session(version: int) -> str:
                newest = main.CASE_STORE.resolve_for_student("DKA-001", include_unpublished=True).version
                if version == newest:
                    response = client.post("/api/sessions", params={"case_id": "DKA-001"}, headers=headers)
                    if response.status_code != 200:
                        raise RuntimeError(f"start DKA-001@{version}: HTTP {response.status_code} {response.text}")
                    return response.json()["session_id"]
                state = SessionState(owner_id=owner_id, case_id="DKA-001", case_version=version)
                main.REPOSITORY.create(state)
                return state.session_id

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

            run_index = 0
            for version in (1, 2):
                case, source_sha = cases[version]
                rubric = next(r for r in case.rubric if r.item_id == "f_gi")
                fact = next(f for f in case.facts if f.fact_id == "f_gi")
                for scenario_id, spec in SCENARIOS.items():
                    run_index += 1
                    sid = start_session(version)
                    turns = []
                    for turn_index, question in enumerate(spec["questions"], start=1):
                        turns.append(ask(sid, f"dka-{run_index:03d}-{turn_index}", question))
                    seal = client.post(f"/api/sessions/{sid}/seal", headers=headers,
                                       json={"request_id": f"dka-{run_index:03d}-seal"})
                    if seal.status_code != 200:
                        raise RuntimeError(f"seal HTTP {seal.status_code}: {seal.text}")
                    sealed = score(sid)
                    item = next(i for i in sealed["items"] if i["item_id"] == "f_gi")
                    detail = client.get(f"/api/sessions/{sid}", headers=headers).json()
                    ask_events = [event for event in detail["state"]["events"] if event["action"] == "ask"]
                    stored = main.REPOSITORY.get(sid, owner_id)
                    atoms: list[str] = []
                    for turn in turns:
                        atoms.extend(turn["persisted_answer_atom_ids"])
                    answers = [turn["api_answer"] for turn in turns]
                    expected_answers = spec.get("expected_answers") or [spec["expected_answer"]]
                    checks = {
                        "answers_match_expectation": answers == expected_answers,
                        "persisted_answer_matches_api": all(
                            turn["persisted_answer"] == turn["api_answer"] for turn in turns),
                        "atoms_match_expectation": atoms == spec["expected_atoms"],
                        "one_event_per_turn": all(turn["persisted_event_count"] == 1 for turn in turns),
                        "score_matches_expectation": abs(item["score"] - spec["expected_score"]) < 1e-9,
                        "hit_matches_expectation": bool(item["hit"]) == (spec["expected_score"] > 0),
                        "evidence_cites_rubric_turns": item["evidence_event_ids"] == [
                            turn["api_event_id"] for turn in turns[:spec["expected_evidence"]]],
                        "evidence_events_exist": all(
                            event_id in {event["event_id"] for event in ask_events}
                            for event_id in item["evidence_event_ids"]),
                        "intake_only_when_asked": all(
                            ("吃不下" in turn["api_answer"]) == ("吃" in turn["question"])
                            for turn in turns),
                        "disclosed_fact_ids_as_expected": list(stored.disclosed_fact_ids) == spec["expected_disclosed"],
                    }
                    results.append({
                        "index": run_index,
                        "case_id": "DKA-001",
                        "case_version": version,
                        "source_file": SOURCE_FILES[version],
                        "source_sha256": source_sha,
                        "scenario_id": scenario_id,
                        "scenario_kind": spec["kind"],
                        "fact_id": "f_gi",
                        "fact_field": fact.field,
                        "fact_content": fact.content,
                        "fact_triggers": list(fact.disclosure_triggers),
                        "rubric": {"description": rubric.description, "weight": rubric.weight,
                                   "evidence_type": rubric.evidence_type, "stage": rubric.stage,
                                   "improvement": rubric.improvement,
                                   "required_atom_ids": list(rubric.required_atom_ids)},
                        "questions": spec["questions"],
                        "expected_answers": expected_answers,
                        "expected_atoms": spec["expected_atoms"],
                        "expected_score": spec["expected_score"],
                        "turns": turns,
                        "persisted_answer_atom_ids": atoms,
                        "persisted_disclosed_fact_ids": list(stored.disclosed_fact_ids),
                        "score_item": {"hit": item["hit"], "score": item["score"], "max_score": item["max_score"],
                                       "evidence_event_ids": item["evidence_event_ids"],
                                       "reason": item["reason"]},
                        "evidence_events": [event for event in ask_events
                                            if event["event_id"] in item["evidence_event_ids"]],
                        "score_status": sealed["status"],
                        "score_total": sealed["total_score"],
                        "score_max": sealed["max_score"],
                        "checks": checks,
                        "all_checks_pass": all(checks.values()),
                    })

            # One repeated request is saved once; the sealed score stays the sealed score.
            sid = start_session(2)
            body = {"request_id": "dka-duplicate-1", "question": "有没有恶心呕吐和腹痛？"}
            first = client.post(f"/api/sessions/{sid}/ask", headers=headers, json=body)
            second = client.post(f"/api/sessions/{sid}/ask", headers=headers, json=body)
            before_seal = main.REPOSITORY.get(sid, owner_id)
            asks_before_seal = [e for e in before_seal.events if e.action == ActionKind.ASK]
            seal = client.post(f"/api/sessions/{sid}/seal", headers=headers,
                               json={"request_id": "dka-duplicate-seal"})
            sealed_score = score(sid)
            after_seal = client.post(f"/api/sessions/{sid}/ask", headers=headers, json=body)
            final_state = main.REPOSITORY.get(sid, owner_id)
            final_score = score(sid)
            asks_after = [e for e in final_state.events if e.action == ActionKind.ASK]
            duplicate_check = {
                "case": "DKA-001@2",
                "fact": "f_gi",
                "question": body["question"],
                "first": {"http": first.status_code, "replayed": first.json()["timing"]["replayed"],
                          "answer": first.json()["answer"], "event_id": first.json()["event"]["event_id"]},
                "second_same_request": {"http": second.status_code, "replayed": second.json()["timing"]["replayed"],
                                        "answer": second.json()["answer"],
                                        "event_id": second.json()["event"]["event_id"]},
                "ask_events_before_seal": len(asks_before_seal),
                "ask_events_after_seal_and_replay": len(asks_after),
                "seal_http": seal.status_code,
                "replay_after_seal": {"http": after_seal.status_code, "replayed": after_seal.json()["timing"]["replayed"],
                                      "answer": after_seal.json()["answer"],
                                      "event_id": after_seal.json()["event"]["event_id"]},
                "total_events": len(final_state.events),
                "score_item_f_gi": next(i for i in sealed_score["items"] if i["item_id"] == "f_gi"),
                "score_total": sealed_score["total_score"],
                "score_unchanged_after_replay": final_score == sealed_score,
                "checks": {
                    "same_request_saved_once": len(asks_before_seal) == 1 and len(asks_after) == 1,
                    "first_response_is_new": first.json()["timing"]["replayed"] is False,
                    "seal_succeeded": seal.status_code == 200,
                    "sealed_response_is_replay": after_seal.json()["timing"]["replayed"] is True,
                    "second_response_is_replay": second.json()["timing"]["replayed"] is True,
                    "same_answer_and_event": first.json()["answer"] == second.json()["answer"] == after_seal.json()["answer"]
                    and first.json()["event"]["event_id"] == second.json()["event"]["event_id"] == after_seal.json()["event"]["event_id"],
                    "score_unchanged": final_score == sealed_score,
                },
            }
        finally:
            client.close()

    temporary_removed = bool(temp_name and not Path(temp_name).exists())
    failed = [row for row in results if not row["all_checks_pass"]]
    report = {
        "schema_version": 1,
        "title": "DKA-001 f_gi question/score boundary check (nausea/vomiting, abdominal pain, intake)",
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
            "case_versions_covered": len({row["case_version"] for row in results}),
            "scenarios_per_version": len(SCENARIOS),
            "scenario_runs": len(results),
            "all_checks_pass": not failed,
            "failed_scenarios": [f"{row['case_version']}:{row['scenario_id']}" for row in failed],
        },
        "duplicate_request_check": duplicate_check,
        "temporary_data_removed": temporary_removed,
        "scenarios": results,
        "evidence_boundary": (
            "This report proves the engineering behaviour of the committed code on the local FastAPI student API, "
            "local auth, an isolated synthetic database and the deterministic simulator. Expectations follow the "
            "rubric text only; it is not evidence of real model behaviour, clinical correctness or official acceptance."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
    return report


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: dka_gi_boundary_probe.py NEW_REPORT.json")
    output = Path(sys.argv[1])
    if output.exists():
        raise SystemExit("Report already exists")
    summary = run(output.resolve())
    print(json.dumps(summary["denominator"], ensure_ascii=False, indent=2))
    print(json.dumps(summary["duplicate_request_check"]["checks"], ensure_ascii=False))
    print("temporary_data_removed", summary["temporary_data_removed"])
    if not (summary["denominator"]["all_checks_pass"]
            and all(summary["duplicate_request_check"]["checks"].values())
            and summary["temporary_data_removed"]):
        raise SystemExit("Boundary, duplicate-request or temporary-data check failed; see report")

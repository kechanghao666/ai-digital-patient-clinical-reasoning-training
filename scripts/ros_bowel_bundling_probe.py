#!/usr/bin/env python3
"""Bundled-symptom probe: what a single symptom question also answers, and what the teacher sees.

Two shipped facts bundle more than the asked symptom into one stored fragment:

* ``STEMI-001`` v3 ``f_ros`` — "没有发烧、咳嗽，也没有头晕、心慌，大小便都正常。" splits into
  ``f_ros#a1`` (发烧+咳嗽), ``f_ros#a2`` (头晕+心慌) and ``f_ros#a3`` (大小便正常). Asking only
  "有没有发烧？" answers "没有发烧、咳嗽。", i.e. the fever question also volunteers the cough.
* ``APPY-001`` v2 ``f_bowel`` — "排便基本正常，没有腹泻。" splits into ``f_bowel#a1`` (排便基本正常)
  and ``f_bowel#a2`` (没有腹泻). Asking only "有没有腹泻？" answers both and marks ``f_bowel`` fully
  disclosed.

Neither fact is a rubric item, so neither directly changes the sealed score. The probe drives the
student ask/seal/score API with the rule simulator and an isolated synthetic database, one fresh
session per scenario, and records the original question, the visible answer, the persisted
fragments, when the whole fact is marked disclosed, and what the sealed score cites.

Contrast records (``f_onset`` trauma, fixed in c5c50ce; ``f_urine``, a sibling bundled fact) show
the two behaviours side by side so the teaching lead can decide whether the patient should answer
related symptoms together. No real model is called.
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

# (case_id, version, fact_id, source_file) -> scenarios.
# ``kind`` labels whether the fact is one of the two named records or a contrast for the teaching lead.
RECORDS: list[dict[str, Any]] = [
    {
        "case_id": "STEMI-001",
        "version": 3,
        "fact_id": "f_ros",
        "source_file": "cases/stemi_case_v3.json",
        "kind": "named_record",
        "scenarios": [
            ("fever_only", ["有没有发烧？"]),
            ("fever_cough_combined", ["有没有发烧、咳嗽？"]),
            ("fever_then_cough", ["有没有发烧？", "有没有咳嗽？"]),
        ],
    },
    {
        "case_id": "APPY-001",
        "version": 2,
        "fact_id": "f_bowel",
        "source_file": "cases/appendicitis_case_v2.json",
        "kind": "named_record",
        "scenarios": [
            ("diarrhea_only", ["有没有腹泻？"]),
            ("diarrhea_bowel_combined", ["排便正常吗？有没有腹泻？"]),
            ("diarrhea_then_bowel", ["有没有腹泻？", "排便正常吗？"]),
        ],
    },
    {
        "case_id": "APPY-001",
        "version": 2,
        "fact_id": "f_onset",
        "source_file": "cases/appendicitis_case_v2.json",
        "kind": "contrast_split",
        "scenarios": [
            ("trauma_only", ["有没有受伤？"]),
        ],
    },
    {
        "case_id": "APPY-001",
        "version": 2,
        "fact_id": "f_urine",
        "source_file": "cases/appendicitis_case_v2.json",
        "kind": "contrast_bundled",
        "scenarios": [
            ("hematuria_only", ["有没有血尿？"]),
        ],
    },
]


def run(output_path: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    duplicate_check: dict[str, Any] = {}
    temp_name: str | None = None
    with tempfile.TemporaryDirectory(prefix="ros-bowel-bundling-") as temporary:
        temp_name = temporary
        configure_isolated_environment(Path(temporary))

        from fastapi.testclient import TestClient

        from app import main
        from app.department_catalog import map_legacy_specialty
        from app.department_config import config_path_for_case_store, save_department_config
        from app.models import ActionKind, SessionState
        from app.patient_answers import fact_fragments

        case_keys = {(r["case_id"], r["version"]) for r in RECORDS}
        cases = {}
        for case_id, version in case_keys:
            cases[(case_id, version)] = main.CASE_STORE.get(case_id, version)
        departments: set[str] = set()
        subspecialties: set[str] = set()
        for case in cases.values():
            department, subspecialty = case.department_code, case.subspecialty_code
            if not department and not subspecialty:
                department, subspecialty = map_legacy_specialty(case.specialty)
            if department:
                departments.add(department)
            if subspecialty:
                subspecialties.add(subspecialty)
        save_department_config(config_path_for_case_store(main.CASE_STORE), departments, subspecialties, "synthetic-ros-bowel")

        password = secrets.token_urlsafe(24)
        username = "ros-bowel-" + secrets.token_hex(6)
        owner_id = main.ACCOUNTS.create(username, password, "student", "synthetic ROS/bowel bundling probe student")
        client = TestClient(main.app)
        try:
            login = client.post("/api/auth/login", json={"username": username, "password": password})
            if login.status_code != 200:
                raise RuntimeError(f"login failed: HTTP {login.status_code}")
            headers = {"Authorization": f"Bearer {login.json()['token']}"}

            def start_session(case_id: str, version: int) -> str:
                newest = main.CASE_STORE.resolve_for_student(case_id, include_unpublished=True).version
                if version == newest:
                    response = client.post("/api/sessions", params={"case_id": case_id}, headers=headers)
                    if response.status_code != 200:
                        raise RuntimeError(f"start {case_id}@{version}: HTTP {response.status_code} {response.text}")
                    return response.json()["session_id"]
                state = SessionState(owner_id=owner_id, case_id=case_id, case_version=version)
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
                persisted_answer = event.result.get("answer") if event else None
                return {
                    "question": question,
                    "http_status": response.status_code,
                    "api_answer": body["answer"],
                    "api_event_id": body["event"]["event_id"],
                    "replayed": body["timing"]["replayed"],
                    "persisted_event_count": len(saved),
                    "persisted_answer": persisted_answer,
                    "saved_matches_api": persisted_answer == body["answer"],
                    "persisted_answer_atom_ids": list(event.result.get("answer_atom_ids", [])) if event else [],
                    "persisted_matched_fact_ids": list((event.payload.get("matched_fact_ids", [])) if event else []),
                    "persisted_turn_kind": event.result.get("turn_kind") if event else None,
                    "disclosed_fact_ids_after": list(stored.disclosed_fact_ids),
                }

            def score(sid: str) -> dict[str, Any]:
                response = client.get(f"/api/sessions/{sid}/score", headers=headers)
                if response.status_code != 200:
                    raise RuntimeError(f"score HTTP {response.status_code}: {response.text}")
                return response.json()

            for spec in RECORDS:
                case_id, version = spec["case_id"], spec["version"]
                case = cases[(case_id, version)]
                fact_id = spec["fact_id"]
                fact = next(f for f in case.facts if f.fact_id == fact_id)
                source_sha = sha256((ROOT / spec["source_file"]).read_bytes())
                rubric_ids = [r.item_id for r in case.rubric]
                fragments = [{"atom_id": f.atom_id, "content": f.content} for f in fact_fragments(fact)]
                scenario_runs = []
                for scenario_id, questions in spec["scenarios"]:
                    sid = start_session(case_id, version)
                    turns = []
                    for turn_index, question in enumerate(questions, start=1):
                        turns.append(ask(sid, f"rosbowel-{case_id}-{fact_id}-{scenario_id}-{turn_index}", question))
                    seal = client.post(f"/api/sessions/{sid}/seal", headers=headers,
                                       json={"request_id": f"rosbowel-seal-{case_id}-{fact_id}-{scenario_id}"})
                    if seal.status_code != 200:
                        raise RuntimeError(f"seal HTTP {seal.status_code}: {seal.text}")
                    sealed = score(sid)
                    stored = main.REPOSITORY.get(sid, owner_id)
                    # When (if ever) did the whole fact become disclosed? None if never.
                    first_disclosed_turn = next(
                        (i for i, turn in enumerate(turns, start=1) if fact_id in turn["disclosed_fact_ids_after"]),
                        None,
                    )
                    fact_in_score = fact_id in {item["item_id"] for item in sealed["items"]}
                    # The three bug checks from the task: saved content vs returned answer, negation
                    # altered by splitting, and a score citing a question that never happened.
                    # Fragments are verbatim substrings of the fact content, so the shared negation
                    # ("没有") is preserved by construction; this is recorded, not inferred.
                    checks = {
                        "saved_matches_api": all(turn["saved_matches_api"] for turn in turns),
                        "fact_has_scoring_item": fact_in_score,
                        "negation_preserved": all(
                            fragment["content"] in fact.content for fragment in fragments
                        ),
                    }
                    scenario_runs.append({
                        "scenario_id": scenario_id,
                        "questions": questions,
                        "turns": turns,
                        "first_disclosed_turn": first_disclosed_turn,
                        "final_disclosed_fact_ids": list(stored.disclosed_fact_ids),
                        "sealed_score_items": [
                            {"item_id": item["item_id"], "hit": item["hit"], "score": item["score"],
                             "evidence_event_ids": item["evidence_event_ids"]}
                            for item in sealed["items"]
                        ],
                        "checks": checks,
                    })
                records.append({
                    "case_id": case_id,
                    "case_version": version,
                    "fact_id": fact_id,
                    "kind": spec["kind"],
                    "source_file": spec["source_file"],
                    "source_sha256": source_sha,
                    "fact_field": fact.field,
                    "fact_content": fact.content,
                    "fact_polarity": fact.polarity,
                    "fact_triggers": list(fact.disclosure_triggers),
                    "fragments": fragments,
                    "rubric_item_ids": rubric_ids,
                    "fact_is_rubric_item": fact_id in rubric_ids,
                    "scenarios": scenario_runs,
                })

            # One repeated request is saved once; the sealed score stays the sealed score.
            sid = start_session("APPY-001", 2)
            body = {"request_id": "rosbowel-duplicate-1", "question": "有没有腹泻？"}
            first = client.post(f"/api/sessions/{sid}/ask", headers=headers, json=body)
            second = client.post(f"/api/sessions/{sid}/ask", headers=headers, json=body)
            before_seal = main.REPOSITORY.get(sid, owner_id)
            asks_before_seal = [e for e in before_seal.events if e.action == ActionKind.ASK]
            seal = client.post(f"/api/sessions/{sid}/seal", headers=headers, json={"request_id": "rosbowel-duplicate-seal"})
            sealed_score = score(sid)
            after_seal = client.post(f"/api/sessions/{sid}/ask", headers=headers, json=body)
            final_state = main.REPOSITORY.get(sid, owner_id)
            final_score = score(sid)
            asks_after = [e for e in final_state.events if e.action == ActionKind.ASK]
            duplicate_check = {
                "case": "APPY-001@2",
                "fact": "f_bowel",
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
    report = {
        "schema_version": 1,
        "title": "Bundled-symptom probe: single symptom question, incidental answer, and teacher view",
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
            "records": len(records),
            "named_record_scenarios": sum(
                len(r["scenarios"]) for r in records if r["kind"] == "named_record"),
            "contrast_scenarios": sum(
                len(r["scenarios"]) for r in records if r["kind"] != "named_record"),
        },
        "duplicate_request_check": duplicate_check,
        "temporary_data_removed": temporary_removed,
        "records": records,
        "evidence_boundary": (
            "This report proves the engineering behaviour of the committed code on the local FastAPI student API, "
            "local auth, an isolated synthetic database and the deterministic simulator. The two named facts are not "
            "rubric items, so no sealed score is changed. Whether the patient should answer related symptoms together "
            "is a teaching/editorial decision, not an engineering fix."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
    return report


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: ros_bowel_bundling_probe.py NEW_REPORT.json")
    output = Path(sys.argv[1])
    if output.exists():
        raise SystemExit("Report already exists")
    summary = run(output.resolve())
    print(json.dumps(summary["denominator"], ensure_ascii=False, indent=2))
    print(json.dumps(summary["duplicate_request_check"]["checks"], ensure_ascii=False))
    print("temporary_data_removed", summary["temporary_data_removed"])
    if not (all(summary["duplicate_request_check"]["checks"].values())
            and summary["temporary_data_removed"]):
        raise SystemExit("Duplicate-request or temporary-data check failed; see report")

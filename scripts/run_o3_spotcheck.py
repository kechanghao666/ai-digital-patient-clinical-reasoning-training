#!/usr/bin/env python3
"""Run a frozen O3 engineering set through the local student API in isolated temp storage."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
DEFAULT_DATASET = ROOT / "evaluation" / "o3_engineering_spotcheck_20261125.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_value(*args: str) -> str:
    result = subprocess.run(["git", "--no-pager", *args], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def configure_isolated_environment(temp_root: Path) -> None:
    env = {
        "APP_ENV": "development",
        "APP_AUTH_MODE": "local",
        "APP_DATA_DIR": str(temp_root / "app-data"),
        "INTEGRATION_DB_PATH": str(temp_root / "integration" / "integration.db"),
        "NEXENT_MODE": "simulator",
        "NEXENT_BASE_URL": "",
        "NEXENT_API_KEY": "",
        "NEXENT_AGENT_NAME": "",
        "NEXENT_PATIENT_AGENT": "",
        "NEXENT_FEEDBACK_AGENT": "",
        "NEXENT_PATIENT_AGENT_SINGLE": "",
        "MCP_SERVICE_TOKEN": "",
        "EMBEDDING_BASE_URL": "",
        "EMBEDDING_API_KEY": "",
        "EMBEDDING_MODEL": "",
        "RERANK_BASE_URL": "",
        "RERANK_API_KEY": "",
        "RERANK_MODEL": "",
        "OCR_BASE_URL": "",
        "OCR_API_KEY": "",
        "OCR_MODEL": "",
    }
    os.environ.update(env)


def run(dataset_path: Path, report_path: Path) -> dict[str, Any]:
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes.decode("utf-8"))
    rounds = dataset.get("rounds", dataset.get("probes", []))
    if len(rounds) not in {4, 20}:
        raise ValueError("评测输入必须是 4 条独立复现探针或冻结的 20 轮抽检")

    expected_case_specs = {case["case_id"]: case for case in dataset["cases"]}
    results: list[dict[str, Any]] = []
    start_failure_by_group: dict[str, dict[str, Any]] = {}
    temp_name: str | None = None

    with tempfile.TemporaryDirectory(prefix="o3-student-api-") as temporary:
        temp_name = temporary
        temp_root = Path(temporary)
        configure_isolated_environment(temp_root)

        from fastapi.testclient import TestClient
        from app import main
        from app.department_catalog import map_legacy_specialty
        from app.department_config import config_path_for_case_store, save_department_config
        from app.adapters import join_fact_contents

        selected_cases = {}
        for case_id, spec in expected_case_specs.items():
            case = main.CASE_STORE.get(case_id, spec["version"])
            if case.version != spec["version"]:
                raise ValueError(f"病例版本与输入不匹配：{case_id}@{spec['version']}")
            source_path = ROOT / spec["source_file"]
            if spec.get("source_sha256") and sha256(source_path.read_bytes()) != spec["source_sha256"]:
                raise ValueError(f"病例来源哈希变化：{case_id}@{spec['version']}")
            selected_cases[case_id] = case

        departments: set[str] = set()
        subspecialties: set[str] = set()
        for case in selected_cases.values():
            department, subspecialty = case.department_code, case.subspecialty_code
            if not department and not subspecialty:
                department, subspecialty = map_legacy_specialty(case.specialty)
            if department:
                departments.add(department)
            if subspecialty:
                subspecialties.add(subspecialty)
        save_department_config(
            config_path_for_case_store(main.CASE_STORE), departments, subspecialties, "synthetic-o3-eval"
        )

        synthetic_password = secrets.token_urlsafe(24)
        username = "o3-student-" + secrets.token_hex(6)
        owner_id = main.ACCOUNTS.create(username, synthetic_password, "student", "合成O3测试学生")
        client = TestClient(main.app)
        try:
            health = client.get("/health")
            login = client.post("/api/auth/login", json={"username": username, "password": synthetic_password})
            if login.status_code != 200:
                raise RuntimeError(f"隔离合成学生登录失败：HTTP {login.status_code}")
            token = login.json().get("token")
            if not token:
                raise RuntimeError("本地认证登录未返回会话令牌")
            headers = {"Authorization": f"Bearer {token}"}
            cases_response = client.get("/api/cases", headers=headers)
            listed = cases_response.json() if cases_response.status_code == 200 else []
            listed_by_public_id = {item.get("public_id"): item for item in listed if isinstance(item, dict)}

            sessions: dict[str, str] = {}
            for index, item in enumerate(rounds, start=1):
                case_id = item["case_id"]
                case_spec = expected_case_specs[case_id]
                case = selected_cases[case_id]
                group = item.get("session_group", item.get("probe_id", item.get("round_id", str(index))))
                request_id = f"o3-{index:03d}-{secrets.token_hex(4)}"
                row: dict[str, Any] = {
                    "index": index,
                    "round_id": item.get("round_id", item.get("probe_id", f"probe-{index}")),
                    "case_id": case_id,
                    "case_version": item.get("case_version", item.get("version", case_spec["version"])),
                    "review_status": case.review_status.value,
                    "session_group": group,
                    "session_policy": item.get("session_policy", dataset.get("session_policy", "one isolated session per probe")),
                    "question": item["question"],
                    "http_status": None,
                    "answer": None,
                    "turn_kind": None,
                    "required_fact_ids": item.get("required_fact_ids", []),
                    "allowed_fact_ids": item.get("allowed_fact_ids", []),
                    "prohibited_fact_ids": item.get("prohibited_fact_ids", []),
                    "required_answer": item.get("required_answer"),
                    "required_answer_fragments": item.get("required_answer_fragments", []),
                    "prohibited_answer_fragments": item.get("prohibited_answer_fragments", []),
                    "actual_answer_atom_ids": [],
                    "actual_fact_ids": [],
                    "persisted_answer": None,
                    "persisted_answer_matches_api": False,
                    "persisted_event_id": None,
                    "persisted_event_matches_api": False,
                    "replay_http_status": None,
                    "replay_answer_matches_api": False,
                    "replay_event_matches_api": False,
                    "single_saved_event_after_replay": False,
                    "adapter_source": None,
                    "flags": [],
                    "primary_result": None,
                }
                if group in start_failure_by_group:
                    row["start_error"] = start_failure_by_group[group]
                    row["flags"] = ["interface_failure"]
                    row["primary_result"] = "interface_failure"
                    results.append(row)
                    continue
                if group not in sessions:
                    start_response = client.post("/api/sessions", params={"case_id": case_id}, headers=headers)
                    if start_response.status_code != 200:
                        safe_detail = ""
                        try:
                            safe_detail = str(start_response.json().get("detail", ""))[:240]
                        except (ValueError, AttributeError):
                            pass
                        start_failure_by_group[group] = {"http_status": start_response.status_code, "detail": safe_detail}
                        row["start_error"] = start_failure_by_group[group]
                        row["flags"] = ["interface_failure"]
                        row["primary_result"] = "interface_failure"
                        results.append(row)
                        continue
                    start_body = start_response.json()
                    sessions[group] = start_body["session_id"]
                    student_case = start_body.get("case", {})
                    row["student_case_entry_visible"] = bool(
                        student_case.get("public_id") in listed_by_public_id
                        and listed_by_public_id[student_case.get("public_id")].get("version") == case_spec["version"]
                    )
                session_id = sessions[group]
                response = client.post(
                    f"/api/sessions/{session_id}/ask",
                    headers=headers,
                    json={"request_id": request_id, "question": item["question"]},
                )
                row["http_status"] = response.status_code
                if response.status_code != 200:
                    detail = ""
                    try:
                        detail = str(response.json().get("detail", ""))[:240]
                    except (ValueError, AttributeError):
                        pass
                    row["error"] = detail
                    row["flags"] = ["interface_failure"]
                    row["primary_result"] = "interface_failure"
                    results.append(row)
                    continue
                try:
                    body = response.json()
                except ValueError:
                    row["flags"] = ["interface_failure"]
                    row["primary_result"] = "interface_failure"
                    row["error"] = "HTTP 200 body was not valid JSON"
                    results.append(row)
                    continue

                row["answer"] = body.get("answer")
                api_event = body.get("event") or {}
                api_event_id = api_event.get("event_id")
                state = main.REPOSITORY.get(session_id, owner_id)
                saved_events = [event for event in state.events if event.request_id == request_id]
                saved_event = saved_events[0] if len(saved_events) == 1 else None
                if saved_event is not None:
                    row["persisted_event_id"] = saved_event.event_id
                    row["persisted_event_matches_api"] = saved_event.event_id == api_event_id
                    row["persisted_answer"] = saved_event.result.get("answer")
                    row["persisted_answer_matches_api"] = row["persisted_answer"] == row["answer"]
                    row["actual_fact_ids"] = list(saved_event.fact_ids_revealed or saved_event.result.get("fact_ids", []))
                    row["actual_answer_atom_ids"] = list(saved_event.result.get("answer_atom_ids", []))
                    row["turn_kind"] = saved_event.result.get("turn_kind")
                    row["adapter_source"] = (saved_event.result.get("adapter") or {}).get("source")
                else:
                    row["flags"].append("interface_failure")

                replay_response = client.post(
                    f"/api/sessions/{session_id}/ask",
                    headers=headers,
                    json={"request_id": request_id, "question": item["question"]},
                )
                row["replay_http_status"] = replay_response.status_code
                if replay_response.status_code == 200:
                    try:
                        replay_body = replay_response.json()
                        replay_event = replay_body.get("event") or {}
                        row["replay_answer_matches_api"] = replay_body.get("answer") == row["answer"]
                        row["replay_event_matches_api"] = replay_event.get("event_id") == api_event_id
                    except ValueError:
                        pass
                state_after_replay = main.REPOSITORY.get(session_id, owner_id)
                row["single_saved_event_after_replay"] = sum(
                    event.request_id == request_id for event in state_after_replay.events
                ) == 1
                if not all((
                    row["persisted_answer_matches_api"],
                    row["persisted_event_matches_api"],
                    row["replay_http_status"] == 200,
                    row["replay_answer_matches_api"],
                    row["replay_event_matches_api"],
                    row["single_saved_event_after_replay"],
                )):
                    row["flags"].append("interface_failure")

                # 原子回答只记录 answer_atom_ids；为兼容要求父事实 ID 的历史探针，
                # 原子所属父事实仅用于满足 required_fact_ids，不加入 allowed/unexpected 判定，
                # 避免破坏以 required_answer 为准且 allowed_fact_ids 留空的原子评测集。
                atom_parent_fact_ids = {atom_id.split("#", 1)[0] for atom_id in row["actual_answer_atom_ids"]}
                actual_facts = set(row["actual_fact_ids"])
                actual_for_required = actual_facts | atom_parent_fact_ids
                actual = actual_facts
                required = set(item["required_fact_ids"])
                allowed = set(item["allowed_fact_ids"])
                prohibited = set(item.get("prohibited_fact_ids", []))
                missing = required - actual_for_required
                unexpected = actual - allowed
                if actual.intersection(prohibited):
                    row["flags"].append("unauthorized_disclosure")
                if unexpected:
                    row["flags"].append("over_answer")
                answer = row["answer"]
                acceptable_no_fact = item.get("acceptable_no_fact_answers", [])
                is_acceptable_no_fact = isinstance(answer, str) and answer in acceptable_no_fact
                if missing and not is_acceptable_no_fact:
                    row["flags"].append("omission")
                if not isinstance(answer, str) or not answer.strip():
                    row["flags"].append("interface_failure")
                elif (not required and not actual and not row["actual_answer_atom_ids"]
                      and not is_acceptable_no_fact):
                    row["flags"].append("nonfact_response_not_accepted")
                # acceptable_no_fact_answers 中的答复视为合法无事实回答，不额外标记

                semantic_expected = item.get("required_answer")
                if isinstance(answer, str) and semantic_expected is not None:
                    missing_answer = [fragment for fragment in item.get("required_answer_fragments", [])
                                      if fragment not in answer]
                    prohibited_answer = [fragment for fragment in item.get("prohibited_answer_fragments", [])
                                         if fragment and fragment in answer]
                    row["exact_answer_matches"] = answer == semantic_expected
                    row["missing_answer_fragments"] = missing_answer
                    row["prohibited_answer_matches"] = prohibited_answer
                    if missing_answer:
                        row["flags"].append("omission")
                    if prohibited_answer:
                        row["flags"].append("over_answer")
                    if answer != semantic_expected and not missing_answer and not prohibited_answer:
                        row["flags"].append("over_answer")
                elif actual_facts and saved_event is not None:
                    # 只有服务端明确返回父事实 ID 时才做整句文本核对；
                    # 纯原子回答（actual_fact_ids 为空）由 required_answer 语义校验，避免误报。
                    expected_text = join_fact_contents(
                        [fact.content for fact in case.facts if fact.fact_id in actual_facts]
                    )
                    if answer != expected_text:
                        row["flags"].append("over_answer")
                if saved_event is None or not row["persisted_event_matches_api"]:
                    if "interface_failure" not in row["flags"]:
                        row["flags"].append("interface_failure")

                row["flags"] = list(dict.fromkeys(row["flags"]))
                if not row["flags"]:
                    row["primary_result"] = "correct"
                else:
                    priority = (
                        "interface_failure", "unauthorized_disclosure", "over_answer",
                        "inappropriate_refusal", "omission", "nonfact_response_not_accepted",
                    )
                    row["primary_result"] = next(
                        (flag for flag in priority if flag in row["flags"]), row["flags"][0]
                    )
                results.append(row)
        finally:
            client.close()

    counts = {
        "round_count": len(results),
        "correct": sum(row["primary_result"] == "correct" for row in results),
        "omission": sum("omission" in row["flags"] for row in results),
        "over_answer": sum("over_answer" in row["flags"] for row in results),
        "unauthorized_disclosure": sum("unauthorized_disclosure" in row["flags"] for row in results),
        "inappropriate_refusal": sum("inappropriate_refusal" in row["flags"] for row in results),
        "interface_failure": sum("interface_failure" in row["flags"] for row in results),
        "primary_results": {
            key: sum(row["primary_result"] == key for row in results)
            for key in (
                "correct", "omission", "over_answer", "unauthorized_disclosure",
                "inappropriate_refusal", "interface_failure", "nonfact_response_not_accepted",
            )
        },
    }
    report = {
        "schema_version": 1,
        "title": dataset.get("title", "O3 问答一致性工程抽检"),
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "authorship": dataset.get("authorship", "开发者工程样例；非盲测"),
        "git_head_sha": git_value("rev-parse", "HEAD"),
        "git_branch": git_value("branch", "--show-current"),
        "worktree_status": git_value("status", "--short"),
        "environment": {
            "os": platform.platform(),
            "python": sys.version.split()[0],
            "python_executable": Path(sys.executable).name,
            "auth_mode": "local",
            "app_environment": "development (draft visibility enabled only for this isolated synthetic run)",
            "patient_adapter": "simulator",
            "database_scope": "temporary APP_DATA_DIR and INTEGRATION_DB_PATH; removed after run",
            "real_provider_calls": 0,
        },
        "dataset_file": dataset_path.resolve().relative_to(ROOT).as_posix(),
        "dataset_sha256": sha256(dataset_bytes),
        "baseline_sha": dataset.get("baseline_sha"),
        "round_count": len(rounds),
        "student_case_entry": {
            "source": "GET /api/cases and POST /api/sessions via local synthetic student bearer session",
            "visible_case_count": len(listed_by_public_id) if "listed_by_public_id" in locals() else 0,
            "http_status": cases_response.status_code if "cases_response" in locals() else None,
            "health_http_status": health.status_code if "health" in locals() else None,
        },
        "counts": counts,
        "category_counts_are_overlapping": True,
        "category_rule": "错误标记可重叠；primary_results 按每轮一个主结果汇总。所有轮次保留在分母中。",
        "temporary_data_removed": bool(temp_name and not Path(temp_name).exists()),
        "rounds": results,
        "evidence_boundary": "本报告只证明该提交代码在本机 FastAPI 学生 API、本地认证、隔离合成数据库及确定性模拟器上的工程行为；不证明真实模型、临床正确性、专家/学生评价或官方 20 轮验收。",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main_cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.dataset.resolve(), args.report.resolve())
    print(json.dumps({"dataset_sha256": report["dataset_sha256"], "counts": report["counts"],
                      "temporary_data_removed": report["temporary_data_removed"], "report": str(args.report)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main_cli()

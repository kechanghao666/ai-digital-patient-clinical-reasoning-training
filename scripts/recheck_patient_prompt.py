#!/usr/bin/env python3
"""Check the documented edit against two protected v9 exports and summarize archived parser verdicts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence"
V9_SHA256 = "089dc4af3771c88e39b2d1097ed3dd18451dead9ded82258c08f9c936d9b1767"
PRESERVED_RULE = "收到工具提交成功的观察结果后，只输出 <final_answer>已提交</final_answer>。不要再次调用任何工具，不要输出代码。"


def replacement_check(export: Path, old: str, candidate: str) -> dict:
    agent = json.loads(export.read_bytes())["agent_info"]["2"]
    prompt = agent["prompt"]
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    if agent["name"] != "digital_patient_stemi" or digest != V9_SHA256:
        raise ValueError("Unexpected agent or published v9 prompt fingerprint")
    if not old or not candidate or prompt.count(old) != 1 or candidate in prompt:
        raise ValueError("Expected one original passage and a distinct candidate")
    amended = prompt.replace(old, candidate, 1)
    if amended.count(candidate) != 1 or amended.replace(candidate, old, 1) != prompt:
        raise ValueError("Replacement did not round-trip to the published prompt")
    if old in amended or prompt.count(PRESERVED_RULE) != 1 or amended.count(PRESERVED_RULE) != 1:
        raise ValueError("Original step remained or final-answer rule changed")
    return {
        "v9_prompt_sha256": digest,
        "candidate_prompt_sha256": hashlib.sha256(amended.encode()).hexdigest(),
        "old_sentence_occurrences": 1,
        "candidate_present_once": True,
        "round_trip_exact": True,
        "preserved_rule_intact": True,
    }


def tool_for(action: str) -> str:
    if "get_turn_context" in action:
        return "get_turn_context"
    if "submit_fact_selection" in action:
        return "submit_fact_selection"
    return "unknown"


def sample_replay() -> list[dict]:
    archive = json.loads((ROOT / "docs/evidence/2026-11-27-patient-action-parser-replay.json").read_text(encoding="utf-8"))
    rows = []
    for sample in archive["samples"]:
        turn, step, attempt = sample["turn"], sample["step"], sample["attempt"]
        tool = tool_for(sample["action_example"])
        expected_step_tool = "get_turn_context" if step == 1 else "submit_fact_selection"
        rows.append({
            "turn": turn, "step": step, "attempt": attempt,
            "called_tool": tool,
            "platform_can_parse": sample["expected"] == "ExecutableAction",
            "tool_matches_step": tool == expected_step_tool,
            "archived_expected": sample["expected"],
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exports", nargs=2, type=Path, metavar="V9_EXPORT")
    parser.add_argument("--output", required=True, type=Path, help="New report path")
    args = parser.parse_args()
    if args.exports[0].samefile(args.exports[1]):
        parser.error("Provide the delivery and preflight exports as separate files")
    document = (EVIDENCE / "2026-11-27-patient-prompt-candidate.md").read_text(encoding="utf-8")
    old, candidate = re.findall(r"^```text\n(.*?)\n```$", document, re.MULTILINE | re.DOTALL)
    report = {
        "export_checks": [replacement_check(export, old, candidate) for export in args.exports],
        "sample_source": "Archived verdicts; replay independently with replay_patient_action_parser.py",
        "samples": sample_replay(),
    }
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

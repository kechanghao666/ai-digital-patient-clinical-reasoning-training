#!/usr/bin/env python3
"""Offline verification of the AgentsHub delivery package structure.

This script checks that the deliverables directory contains the expected files,
does not contain hand-written credential-looking content, and reports the missing
agent.json that must come from a real Nexent platform export.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DELIVERABLES = ROOT / "deliverables" / "AgentsHub" / "行业智能体" / "医疗" / "AI数字病人临床思维训练"

FORBIDDEN_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # OpenAI-style key
    re.compile(r"Bearer\s+[a-zA-Z0-9_\-]{16,}"),  # Bearer token inline
    re.compile(r"https?://[^\s\"]+:[^\s\"]+@"),  # URL with credentials
    re.compile(r"nexent_?api_?key\s*[:=]\s*[\"']?[a-zA-Z0-9_\-]{8,}", re.I),
    re.compile(r"mcp_service_token\s*[:=]\s*[\"']?[a-zA-Z0-9_\-]{8,}", re.I),
]

REQUIRED_FILES = ["README.md", "IMPORT.md", "CHECKLIST.md", "mcp-config.example.json"]
OPTIONAL_FILES = ["agent.json"]
FORBIDDEN_FILES = ["agent.json.placeholder", "agent.json.template"]


def check_structure() -> list[str]:
    problems = []
    for name in REQUIRED_FILES:
        path = DELIVERABLES / name
        if not path.is_file():
            problems.append(f"missing required file: {name}")
    for name in FORBIDDEN_FILES:
        path = DELIVERABLES / name
        if path.exists():
            problems.append(f"forbidden placeholder file should not exist: {name}")
    return problems


def check_agent_json_status() -> dict[str, bool | str]:
    agent_path = DELIVERABLES / "agent.json"
    if not agent_path.exists():
        return {"present": False, "note": "agent.json must be exported from Nexent; do not hand-write"}
    try:
        data = json.loads(agent_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"present": True, "valid_json": False, "error": str(exc)}
    return {"present": True, "valid_json": True, "top_level_keys": sorted(data.keys())}


def check_sensitive_leaks() -> list[str]:
    leaks = []
    for path in DELIVERABLES.iterdir():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in FORBIDDEN_PATTERNS:
            for match in pattern.finditer(text):
                leaks.append(f"{path.name}: potential credential leak at position {match.start()}: {match.group()[:30]}...")
    return leaks


def main() -> int:
    if not DELIVERABLES.is_dir():
        print(f"FAIL: deliverables directory not found: {DELIVERABLES}", file=sys.stderr)
        return 1

    report: dict[str, object] = {
        "package_dir": str(DELIVERABLES.relative_to(ROOT)),
        "offline_verified": True,
    }

    structure_problems = check_structure()
    if structure_problems:
        report["structure_problems"] = structure_problems
        report["offline_verified"] = False

    agent_status = check_agent_json_status()
    report["agent_json"] = agent_status
    if not agent_status.get("present"):
        report["offline_verified"] = False

    leaks = check_sensitive_leaks()
    if leaks:
        report["credential_leaks"] = leaks
        report["offline_verified"] = False

    out_path = DELIVERABLES / "verify-report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["offline_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

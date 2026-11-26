#!/usr/bin/env python3
"""Verify the published candidate target before the first real question (zero model calls).

After the project owner publishes the independent candidate version, export that agent and run:

    python scripts/verify_published_prompt.py <agent_export.json> [--name digital_patient_stemi]

The script reports the agent name, computes the full prompt SHA-256 and checks it against the
candidate fingerprint recorded during offline calibration. It does NOT call any model. The published
*version number* is a platform-side concept that the export may not carry; record it from the
platform UI and confirm it alongside the fingerprint before asking.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

CANDIDATE_SHA256 = "cb1de45e02980e5d0002f84cae66909b1b714ba49bc1f40413396ed6983873c3"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, metavar="AGENT_EXPORT")
    parser.add_argument("--name", default="digital_patient_stemi", help="Expected agent name")
    args = parser.parse_args()

    data = json.loads(args.export.read_bytes())
    agents = data.get("agent_info", {})
    if not isinstance(agents, dict) or not agents:
        raise SystemExit("导出件缺少 agent_info")

    root_id = data.get("agent_id")
    match = agents.get(str(root_id))
    if root_id is None or not isinstance(match, dict) or str(match.get("agent_id")) != str(root_id):
        raise SystemExit("导出件的主智能体编号与 agent_info 不一致")
    if match.get("name") != args.name:
        raise SystemExit("导出的主智能体名称与预期不符")
    prompt = match.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise SystemExit("导出件缺少主智能体的完整提示词")
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    ok = digest == CANDIDATE_SHA256
    report = {
        "agent_name": match.get("name"),
        "agent_id": match.get("agent_id"),
        "prompt_sha256": digest,
        "expected_candidate_sha256": CANDIDATE_SHA256,
        "fingerprint_matches": ok,
        "note": "published version number is a platform-side value; record it from the platform UI and confirm before asking",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not ok:
        print("fingerprint mismatch; do not ask until the published prompt matches the candidate", file=sys.stderr)
        return 1
    print("fingerprint matches; agent name and prompt are the candidate — proceed after confirming the published version")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

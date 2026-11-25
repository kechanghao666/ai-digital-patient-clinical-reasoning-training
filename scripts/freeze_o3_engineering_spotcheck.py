#!/usr/bin/env python3
"""Freeze the O3 engineering spotcheck before changing disclosure routing."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = ROOT / "evaluation" / "o3_engineering_spotcheck_seed_20261125.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "o3_engineering_spotcheck_20261125.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def freeze(seed_path: Path, output_path: Path) -> None:
    if output_path.exists():
        raise SystemExit(f"拒绝覆盖已存在的冻结输入：{output_path}")
    raw_seed = seed_path.read_bytes()
    seed = json.loads(raw_seed.decode("utf-8"))
    head = subprocess.run(
        ["git", "--no-pager", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head != seed.get("baseline_sha"):
        raise SystemExit(f"冻结基线不匹配：期望 {seed.get('baseline_sha')}，当前 {head}")
    if not seed.get("created_before_product_fix"):
        raise SystemExit("种子未声明在产品修复前创建，拒绝冻结")

    cases: dict[str, dict] = {}
    for spec in seed["cases"]:
        source_path = ROOT / spec["source_file"]
        source_bytes = source_path.read_bytes()
        source = json.loads(source_bytes.decode("utf-8"))
        if source.get("case_id") != spec["case_id"] or source.get("version") != spec["version"]:
            raise SystemExit(f"病例版本与预登记输入不符：{spec['case_id']}@{spec['version']}")
        fact_ids = [fact["fact_id"] for fact in source["facts"]]
        if len(fact_ids) != len(set(fact_ids)):
            raise SystemExit(f"病例事实 ID 重复：{spec['case_id']}@{spec['version']}")
        cases[spec["case_id"]] = {
            **spec,
            "review_status": source.get("review_status", "unspecified"),
            "source_sha256": sha256(source_bytes),
            "fact_ids": fact_ids,
        }

    rounds = []
    expected_round_count = len(cases) * len(seed["round_templates"])
    for case_id, case in cases.items():
        for template in seed["round_templates"]:
            expected = template["expected_fact_ids_by_case"].get(case_id)
            if expected is None:
                raise SystemExit(f"缺少预登记预期事实：{case_id} 第 {template['round_in_case']} 轮")
            unknown = set(expected) - set(case["fact_ids"])
            if unknown:
                raise SystemExit(f"预期事实不在病例版本中：{case_id}: {sorted(unknown)}")
            rounds.append({
                "round_id": f"{case_id}-R{template['round_in_case']:02d}",
                "case_id": case_id,
                "case_version": case["version"],
                "source_file": case["source_file"],
                "source_sha256": case["source_sha256"],
                "round_in_case": template["round_in_case"],
                "session_group": case_id,
                "session_policy": "four sequential rounds share one session within each case",
                "intent": template["intent"],
                "question": template["question"],
                "required_fact_ids": expected,
                "allowed_fact_ids": expected,
                "prohibited_fact_ids": [fact_id for fact_id in case["fact_ids"] if fact_id not in expected],
                "acceptable_no_fact_answers": template["acceptable_no_fact_answers"],
                "correctness_rule": template["correctness_rule"],
            })
    if len(rounds) != expected_round_count or expected_round_count != 20:
        raise SystemExit(f"预登记抽检轮次应为 20，实际 {expected_round_count}")

    frozen = {
        "schema_version": 1,
        "title": "O3 开发者预登记工程抽检（修复前冻结）",
        "authorship": seed["authorship"],
        "baseline_sha": head,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed_file": seed_path.resolve().relative_to(ROOT).as_posix(),
        "seed_sha256": sha256(raw_seed),
        "session_policy": seed["session_policy"],
        "cases": list(cases.values()),
        "round_count": len(rounds),
        "rounds": rounds,
        "evaluation_scope": "本机规则模拟器 + 产品学生问诊 API；仅工程一致性检查，不代表真实模型、医学专家、学生或官方验收结果",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"frozen {len(rounds)} rounds; sha256={sha256(output_path.read_bytes())}; output={output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    freeze(args.seed.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()

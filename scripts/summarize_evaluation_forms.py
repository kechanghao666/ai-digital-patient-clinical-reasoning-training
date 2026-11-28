#!/usr/bin/env python3
"""Summarise the three human-review evaluation forms as recorded — never fabricate a verdict.

Reads filled copies of the 2026-11-27 review forms (20-round QA sampling, ≥3-student expert-scored
agreement, error-decision evolution), checks every referenced case/version against the frozen
manifest, and reports counts and as-recorded rates with explicit denominators:

- Rows left blank are counted as missing; they never enter a rate.
- ``失败或超时`` rows are reported separately — the README leaves the denominator policy to the
  reviewers, so both inclusion variants are shown and NEITHER is declared "the" agreement rate.
- A pass/fail claim against 95%/85%/100% requires ``--policy-confirmed-by <reviewer>``; without it
  the tool prints rates only. Even with the flag the verdict is explicitly "as recorded", not a
  certification.

Exit code 0 when the summary was produced, 2 on structural problems (missing/invalid files).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FORMS = {
    "qa_20rounds": "问答抽检_20轮_待填写.csv",
    "student_expert": "学生专家评分_至少3人_待填写.csv",
    "error_evolution": "错误决策演进_待临床定义.csv",
}
YES, NO = "是", "否"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return [{(key or "").strip(): (value or "").strip() for key, value in row.items()}
                for row in csv.DictReader(stream)]


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _tri(row: dict[str, str], column: str) -> str:
    value = row.get(column, "")
    if value == YES:
        return YES
    if value == NO:
        return NO
    return "未填写"


def summarize_qa(rows: list[dict[str, str]], frozen: set[tuple[str, str]]) -> dict[str, Any]:
    # A timed-out or failed round still counts as an attempted round: it has a question even
    # without a complete answer. Blank planned slots never count.
    filled = [row for row in rows if row.get("提问")]
    consistent = [row for row in filled if _tri(row, "是否一致") == YES]
    inconsistent = [row for row in filled if _tri(row, "是否一致") == NO]
    undecided = [row for row in filled if _tri(row, "是否一致") == "未填写"]
    failed = [row for row in filled if _tri(row, "失败或超时") == YES]
    # Any written case reference is checked against the frozen manifest, filled or not.
    unfrozen = sorted({f"{row.get('病例ID')}@{row.get('版本')}" for row in rows
                       if row.get("病例ID") and (row.get("病例ID"), row.get("版本")) not in frozen})
    answered = len(consistent) + len(inconsistent)
    return {
        "planned_rows": len(rows), "filled_rows": len(filled),
        "consistent": len(consistent), "inconsistent": len(inconsistent),
        "judgement_missing": len(undecided), "failed_or_timeout": len(failed),
        "agreement_rate_excluding_failures": _rate(len(consistent), answered - len(failed)),
        "agreement_rate_including_failures_as_miss": _rate(len(consistent), answered),
        "unfrozen_case_references": unfrozen,
        "note": "两个口径并列展示；分母口径由评审确认后才可作为正式一致率。",
    }


def summarize_student(rows: list[dict[str, str]], frozen: set[tuple[str, str]]) -> dict[str, Any]:
    STAGES = ("病史采集", "检查选择", "鉴别诊断", "诊断", "治疗")
    students: dict[str, dict[str, list[dict]]] = {}
    unfrozen = set()
    for row in rows:
        student = row.get("实际匿名编号")
        if not student:
            continue
        if (row.get("病例ID及版本") or "").split("@")[0] and "@" in (row.get("病例ID及版本") or ""):
            case, _, version = row["病例ID及版本"].partition("@")
            if (case, version) not in frozen:
                unfrozen.add(row["病例ID及版本"])
        students.setdefault(student, {}).setdefault(row.get("评分环节") or "未分类", []).append(row)
    result = []
    for student, stages in sorted(students.items()):
        stage_report = {}
        for stage in STAGES:
            entries = stages.get(stage, [])
            agreed = sum(1 for row in entries if _tri(row, "是否一致") == YES)
            stage_report[stage] = {
                "filled": len(entries),
                "agreed": agreed,
                "judgement_missing": sum(1 for row in entries if _tri(row, "是否一致") not in (YES, NO)),
            }
        judged = [row for row in sum(stages.values(), []) if _tri(row, "是否一致") in (YES, NO)]
        agreed = sum(1 for row in judged if _tri(row, "是否一致") == YES)
        result.append({
            "student": student,
            "stages_present": sorted(k for k in stages if k in STAGES),
            "all_five_stages": all(stage in stages for stage in STAGES),
            "filled_rows": len(sum(stages.values(), [])),
            "agreed": agreed,
            "agreement_rate_as_recorded": _rate(agreed, len(judged)),
            "stage_detail": stage_report,
        })
    complete = [entry for entry in result if entry["all_five_stages"]]
    agreed_total = sum(entry["agreed"] for entry in complete)
    filled_total = sum(entry["filled_rows"] for entry in complete)
    return {
        "students_recorded": len(result),
        "complete_students": len(complete),
        "overall_complete_students_rate_as_recorded": _rate(agreed_total, filled_total),
        "students": result,
        "unfrozen_case_references": sorted(unfrozen),
        "note": "只统计实际匿名编号非空的行；≥3 名学生须每人都含五环节，分项容差与聚合公式由评审确认。",
    }


def summarize_error(rows: list[dict[str, str]], frozen: set[tuple[str, str]]) -> dict[str, Any]:
    denominator = [row for row in rows if _tri(row, "是否纳入错误触发分母") == YES]
    triggered = [row for row in denominator if _tri(row, "是否触发") == YES]
    accurate = [row for row in triggered if _tri(row, "反馈是否准确") == YES]
    unfrozen = sorted({f"{row.get('病例ID')}@{row.get('版本')}" for row in rows
                       if (row.get("病例ID"), row.get("版本")) not in frozen})
    return {
        "planned_rows": len(rows),
        "in_denominator": len(denominator),
        "triggered": len(triggered),
        "feedback_accuracy_among_triggered": _rate(len(accurate), len(triggered)),
        "trigger_rate_as_recorded": _rate(len(triggered), len(denominator)),
        "rows_excluded_from_denominator": len(rows) - len(denominator),
        "unfrozen_case_references": unfrozen,
        "note": "分母以『是否纳入错误触发分母=是』为准；正常对照与例外不自动计入，遗漏分支不得静默排除。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="汇总三项人工评审表单（只按已填行计算，不代下结论）")
    parser.add_argument("--forms-dir", required=True, help="含三份已填写 CSV 的目录")
    parser.add_argument("--policy-confirmed-by", default=None,
                        help="确认分母口径的评审人员标识；提供后附加 as_recorded 判定，否则只给比率")
    parser.add_argument("--report", default=None, help="汇总 JSON 输出路径（不覆盖已有文件）")
    args = parser.parse_args()

    forms_dir = Path(args.forms_dir)
    manifest_path = forms_dir / "manifest.json"
    frozen: set[tuple[str, str]] = set()
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        frozen = {(entry["case_id"], str(entry["version"])) for entry in manifest.get("cases", [])}

    problems = []
    summaries = {}
    for key, name in FORMS.items():
        path = forms_dir / name
        if not path.is_file():
            problems.append(f"missing: {name}")
            continue
        rows = _rows(path)
        if key == "qa_20rounds":
            summaries[key] = summarize_qa(rows, frozen)
        elif key == "student_expert":
            summaries[key] = summarize_student(rows, frozen)
        else:
            summaries[key] = summarize_error(rows, frozen)
    if problems:
        print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    targets = {"qa_20rounds": 0.95, "student_expert": 0.85, "error_evolution": 1.0}
    verdicts = None
    if args.policy_confirmed_by:
        verdicts = {}
        qa = summaries["qa_20rounds"]
        student = summaries["student_expert"]
        error = summaries["error_evolution"]
        verdicts["qa_20rounds_agreement_excluding_failures"] = (
            "meets_target_as_recorded" if (qa["agreement_rate_excluding_failures"] or 0) >= targets["qa_20rounds"]
            else "below_target_as_recorded")
        verdicts["student_expert_overall"] = (
            "meets_target_as_recorded" if (student["overall_complete_students_rate_as_recorded"] or 0) >= targets["student_expert"]
            else "below_target_as_recorded")
        verdicts["error_evolution_trigger"] = (
            "meets_target_as_recorded" if error["trigger_rate_as_recorded"] == 1.0 and error["in_denominator"] > 0
            else "below_target_as_recorded")
        verdicts["recorded_under"] = args.policy_confirmed_by
        verdicts["scope"] = "as_recorded only; not a certification, not an official acceptance"

    report = {"ok": True, "frozen_case_versions": sorted(f"/".join(pair) for pair in sorted(frozen)),
              "summaries": summaries, "verdicts_as_recorded": verdicts,
              "scope": "Counts and rates over filled rows only; missing rows never enter a rate."}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        report_path = Path(args.report)
        if report_path.exists():
            print(json.dumps({"ok": False, "error": f"{report_path} 已存在；汇总不覆盖"}, ensure_ascii=False))
            raise SystemExit(2)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    raise SystemExit(0)


if __name__ == "__main__":
    main()

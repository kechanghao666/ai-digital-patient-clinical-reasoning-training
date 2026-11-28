#!/usr/bin/env python3
"""Summarise the three human-review evaluation forms as recorded — never fabricate a verdict.

Reads filled copies of the 2026-11-27 review forms (20-round QA sampling, ≥3-student expert-scored
agreement, error-decision evolution), checks every referenced case/version against the frozen
manifest, and reports counts and rates with explicit numerators, denominators and the applicable
policy for each:

- Blank planned slots never enter any denominator; attempted-but-undecided rows are listed.
- ``失败或超时`` rounds are reported separately and appear in two clearly labelled variants
  (excluded from the denominator vs counted as a miss). Which variant is *the* agreement rate is
  the reviewers' recorded policy, not this tool's choice.
- Contradictory or unrecognised markers, duplicates, missing stages/scores/tolerances and
  non-frozen case references are flagged and never silently enter a denominator.
- A pass/fail wording against 95%/85%/100% requires ``--policy-confirmed-by`` plus
  ``--failure-policy`` AND sufficient material (≥20 attempted rounds; ≥3 students each with all
  five stages and no flagged rows). Even then the verdict is explicitly "as recorded".

Exit code 0 when the summary was produced, 2 on structural problems (missing files, wrong headers).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FORMS_DIR_HINT = str(ROOT / "docs" / "review-forms-20261127")
FORMS = {
    "qa_20rounds": "问答抽检_20轮_待填写.csv",
    "student_expert": "学生专家评分_至少3人_待填写.csv",
    "error_evolution": "错误决策演进_待临床定义.csv",
}
QA_HEADER = ["计划行号", "病例ID", "版本", "会话匿名ID", "请求ID", "提问", "完整回答", "允许事实", "禁止事实",
             "是否一致", "失败或超时", "评审人", "理由", "日期"]
STUDENT_HEADER = ["计划学生槽位", "实际匿名编号", "病例ID及版本", "轨迹位置", "评分环节", "专家独立原分",
                  "系统原分", "事先确认容差", "是否一致", "差异说明", "评审人", "日期"]
ERROR_HEADER = ["计划场景ID", "病例ID", "版本", "路径类型", "当时可见证据", "决策输入", "时间锚点",
                "预期后果", "例外", "是否纳入错误触发分母", "实际演进", "是否触发", "反馈是否准确",
                "审核人", "结论"]
STAGES = ("病史采集", "检查选择", "鉴别诊断", "诊断", "治疗")
YES, NO = "是", "否"
MIN_QA_ROUNDS = 20
MIN_STUDENTS = 3


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return [{(key or "").strip(): (value or "").strip() for key, value in row.items()}
                for row in csv.DictReader(stream)]


def _check_header(path: Path, expected: list[str], problems: list[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        actual = next(csv.reader(stream), [])
    actual = [column.strip() for column in actual]
    missing = [column for column in expected if column not in actual]
    extra = [column for column in actual if column and column not in expected]
    if missing:
        problems.append(f"{path.name}: 缺少列 {missing}；请从 {FORMS_DIR_HINT} 复制模板后按原列填写")
    if extra:
        problems.append(f"{path.name}: 多出非模板列 {extra}；删除或改名后再汇总")
    return _rows(path)


def _tri(row: dict[str, str], column: str) -> str:
    value = row.get(column, "")
    if value == YES:
        return YES
    if value == NO:
        return NO
    if value == "":
        return "未填写"
    return "非法标记"


def _rate(numerator: int, denominator: int, policy: str) -> dict[str, Any]:
    value = round(numerator / denominator, 4) if denominator else None
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "display": f"{numerator}/{denominator}" if denominator else "未知（分母为 0）",
        "policy": policy,
    }


def _case_marker(row: dict[str, str], column: str, frozen: set[tuple[str, str]]) -> str | None:
    """None when the row's case reference is a frozen version; a reason string otherwise."""
    raw = row.get(column, "")
    if not raw:
        return None
    case, separator, version = raw.partition("@")
    if not separator or not case or not version.isdigit():
        return f"格式错误:{raw}"
    if (case, version) not in frozen:
        return f"非冻结版本:{raw}"
    return None


def summarize_qa(rows: list[dict[str, str]], frozen: set[tuple[str, str]]) -> dict[str, Any]:
    # An attempted round has a question; blank planned slots never count. A timed-out round may
    # legitimately have no judgment, so "failed" is a separate bucket, never silently a miss.
    filled = [row for row in rows if row.get("提问")]
    contradictory = [row for row in filled if _tri(row, "是否一致") == YES and _tri(row, "失败或超时") == YES]
    failed = [row for row in filled if _tri(row, "失败或超时") == YES and row not in contradictory]
    judged = [row for row in filled if _tri(row, "是否一致") in (YES, NO) and row not in contradictory]
    consistent = [row for row in judged if _tri(row, "是否一致") == YES]
    inconsistent = [row for row in judged if _tri(row, "是否一致") == NO]
    nonfailed_judged = [row for row in judged if _tri(row, "失败或超时") != YES]
    consistent_nonfailed = [row for row in consistent if _tri(row, "失败或超时") != YES]
    undecided = [row for row in filled if _tri(row, "是否一致") == "未填写" and _tri(row, "失败或超时") != YES
                 and row not in contradictory]
    invalid_marker = [row for row in filled if _tri(row, "是否一致") == "非法标记"]
    # The QA form stores 病例ID and 版本 in separate columns; combine them for the frozen check.
    def qa_case_reference(row: dict[str, str]) -> dict[str, str]:
        combined = dict(row)
        combined["病例ID@版本"] = f"{row.get('病例ID', '')}@{row.get('版本', '')}"
        return combined

    unfrozen = sorted({marker for row in filled if row.get("病例ID")
                       and (marker := _case_marker(qa_case_reference(row), "病例ID@版本", frozen))})
    return {
        "planned_rows": len(rows),
        "filled_rows": len(filled),
        "consistent": len(consistent),
        "inconsistent": len(inconsistent),
        "failed_or_timeout": len(failed),
        "contradictory": len(contradictory),
        "invalid_marker_rows": len(invalid_marker),
        "judgement_missing": len(undecided),
        "agreement_rate_excluding_failures": _rate(
            len(consistent_nonfailed), len(nonfailed_judged),
            "失败或超时的轮次不进分母（描述性比率；评审确认前不作为正式一致率）"),
        "agreement_rate_failures_as_miss": _rate(
            len(consistent), len(filled),
            "失败或超时的轮次按未命中计入分母（描述性比率；评审确认前不作为正式一致率）"),
        "unfrozen_or_invalid_case_references": unfrozen,
        "note": "两种口径并列展示；哪一种是正式一致率由评审记录的政策决定，工具不代选。",
    }


def summarize_student(rows: list[dict[str, str]], frozen: set[tuple[str, str]]) -> dict[str, Any]:
    invalid: list[dict[str, str]] = []
    seen: dict[tuple[str, str, str, str], int] = {}
    students: dict[str, dict[str, list[dict]]] = {}
    for index, row in enumerate(rows, start=2):
        student = row.get("实际匿名编号")
        if not student:
            invalid.append({"row": index, "reason": "缺少实际匿名编号"})
            continue
        marker = _case_marker(row, "病例ID及版本", frozen)
        if marker:
            invalid.append({"row": index, "student": student, "reason": marker})
            continue
        stage = row.get("评分环节")
        if stage not in STAGES:
            invalid.append({"row": index, "student": student, "reason": f"评分环节不是五个规定环节：{stage or '空'}"})
            continue
        key = (student, row.get("病例ID及版本", ""), row.get("轨迹位置", ""), stage)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            invalid.append({"row": index, "student": student, "reason": "重复行（同一学生/病例/轨迹/环节）"})
            continue
        judgment = _tri(row, "是否一致")
        if judgment not in (YES, NO):
            invalid.append({"row": index, "student": student, "reason": f"一致性判断为空或非法：{judgment}"})
            continue
        if not row.get("专家独立原分") or not row.get("系统原分"):
            invalid.append({"row": index, "student": student, "reason": "缺少专家或系统原分"})
            continue
        if not row.get("事先确认容差"):
            invalid.append({"row": index, "student": student, "reason": "缺少事先确认容差"})
            continue
        students.setdefault(student, {}).setdefault(stage, []).append(row)
    result = []
    for student, stages in sorted(students.items()):
        all_rows = [row for stage_rows in stages.values() for row in stage_rows]
        agreed = sum(1 for row in all_rows if _tri(row, "是否一致") == YES)
        result.append({
            "student": student,
            "stages_present": sorted(stages),
            "all_five_stages": len(stages) == len(STAGES),
            "valid_rows": len(all_rows),
            "agreed": agreed,
            "agreement_rate_as_recorded": _rate(agreed, len(all_rows), "按该生有效行计算（描述性比率）"),
        })
    complete = [entry for entry in result if entry["all_five_stages"]]
    agreed_total = sum(entry["agreed"] for entry in complete)
    valid_total = sum(entry["valid_rows"] for entry in complete)
    return {
        "students_recorded": len(result),
        "complete_students": len(complete),
        "overall_complete_students_rate_as_recorded": _rate(
            agreed_total, valid_total, "仅完整五环节学生的有效行（描述性比率）"),
        "students": result,
        "invalid_rows": invalid,
        "material_gate": {"min_students": MIN_STUDENTS, "complete_students": len(complete),
                          "invalid_row_count": len(invalid)},
        "note": "重复行、缺环节、空判断、非法版本或格式、缺原分/容差的行均不进有效分母，逐行列出。",
    }


def summarize_error(rows: list[dict[str, str]], frozen: set[tuple[str, str]]) -> dict[str, Any]:
    invalid: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=2):
        combined = dict(row)
        combined["病例ID@版本"] = f"{row.get('病例ID', '')}@{row.get('版本', '')}"
        marker = _case_marker(combined, "病例ID@版本", frozen)
        if marker:
            invalid.append({"row": index, "reason": marker})
    inclusion_undecided = [row for row in rows if _tri(row, "是否纳入错误触发分母") == "未填写"]
    in_denominator = [row for row in rows if _tri(row, "是否纳入错误触发分母") == YES]
    triggered = [row for row in in_denominator if _tri(row, "是否触发") == YES]
    not_triggered = [row for row in in_denominator if _tri(row, "是否触发") == NO]
    trigger_undecided = [row for row in in_denominator if _tri(row, "是否触发") == "未填写"]
    accurate = [row for row in triggered if _tri(row, "反馈是否准确") == YES]
    inaccurate = [row for row in triggered if _tri(row, "反馈是否准确") == NO]
    accuracy_undecided = [row for row in triggered if _tri(row, "反馈是否准确") == "未填写"]
    accuracy_on_not_triggered = [row for row in not_triggered if row.get("反馈是否准确")]
    return {
        "planned_rows": len(rows),
        "inclusion_undecided": len(inclusion_undecided),
        "in_denominator": len(in_denominator),
        "excluded_from_denominator": len(rows) - len(in_denominator) - len(inclusion_undecided),
        "triggered": len(triggered),
        "not_triggered": len(not_triggered),
        "trigger_undecided": len(trigger_undecided),
        "trigger_rate_as_recorded": _rate(
            len(triggered), len(in_denominator),
            "分母以『是否纳入错误触发分母=是』为准（描述性比率）"),
        "feedback_accuracy_among_triggered": _rate(
            len(accurate), len(triggered), "仅在触发行内计算（描述性比率）"),
        "feedback_accuracy_detail": {"accurate": len(accurate), "inaccurate": len(inaccurate),
                                     "undecided": len(accuracy_undecided)},
        "accuracy_marked_on_not_triggered_rows": len(accuracy_on_not_triggered),
        "invalid_rows": invalid,
        "note": "正常对照与例外由评审显式排除，不自动计入分母；未判定逐项保留，遗漏分支不得静默排除。",
    }


def _write_verdicts(summaries: dict[str, Any], confirmed_by: str, failure_policy: str) -> dict[str, Any]:
    """Gate pass/fail wording on sufficient material; withhold otherwise, with reasons."""
    reasons: list[str] = []
    qa, student, error = summaries["qa_20rounds"], summaries["student_expert"], summaries["error_evolution"]
    qa_rate = (qa["agreement_rate_excluding_failures"] if failure_policy == "excluding"
               else qa["agreement_rate_failures_as_miss"])
    if qa["filled_rows"] < MIN_QA_ROUNDS:
        reasons.append(f"问答已尝试轮次 {qa['filled_rows']} < {MIN_QA_ROUNDS}")
    if qa["judgement_missing"] or qa["contradictory"] or qa["invalid_marker_rows"]:
        reasons.append("问答存在未判定、矛盾或非法标记行")
    if qa["unfrozen_or_invalid_case_references"]:
        reasons.append("问答引用了非冻结或格式错误的病例版本")
    if student["complete_students"] < MIN_STUDENTS:
        reasons.append(f"完整五环节学生 {student['complete_students']} < {MIN_STUDENTS}")
    if student["invalid_rows"]:
        reasons.append("学生评分存在被排除的无效行")
    if error["inclusion_undecided"] or error["trigger_undecided"] or error["invalid_rows"]:
        reasons.append("错误决策存在未判定或无效行")
    if reasons:
        return {"verdict": "verdict_withheld", "reasons": reasons,
                "note": "材料不足以按已确认政策给出判定；描述性比率仍可在 summaries 中查看。",
                "scope": "as_recorded only; not a certification, not an official acceptance"}
    qa_ok = (qa_rate["value"] or 0) >= 0.95
    student_ok = (student["overall_complete_students_rate_as_recorded"]["value"] or 0) >= 0.85
    error_ok = error["trigger_rate_as_recorded"]["value"] == 1.0 and error["in_denominator"] > 0
    return {
        "verdict": ("meets_target_as_recorded" if qa_ok and student_ok and error_ok
                    else "below_target_as_recorded"),
        "applied_rates": {"qa": qa_rate,
                          "student_overall": student["overall_complete_students_rate_as_recorded"],
                          "error_trigger": error["trigger_rate_as_recorded"]},
        "failure_policy": failure_policy,
        "confirmed_by": confirmed_by,
        "scope": "as_recorded only; not a certification, not an official acceptance",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="汇总三项人工评审表单（只按有效行计算，不代下结论）")
    parser.add_argument("--forms-dir", required=True, help="含三份已填写 CSV 与 manifest.json 的目录")
    parser.add_argument("--policy-confirmed-by", default=None,
                        help="确认分母与聚合政策的评审人员标识；与 --failure-policy 一起提供才可能出判定")
    parser.add_argument("--failure-policy", choices=["excluding", "as_miss"], default=None,
                        help="失败或超时轮次如何入分母：excluding=不进分母，as_miss=按未命中；判定时必须记录")
    parser.add_argument("--report", default=None, help="汇总 JSON 输出路径（不覆盖已有文件）")
    args = parser.parse_args()
    if (args.policy_confirmed_by is None) != (args.failure_policy is None):
        parser.error("--policy-confirmed-by 与 --failure-policy 必须同时提供")

    forms_dir = Path(args.forms_dir)
    problems: list[str] = []
    manifest_path = forms_dir / "manifest.json"
    frozen: set[tuple[str, str]] = set()
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        frozen = {(entry["case_id"], str(entry["version"])) for entry in manifest.get("cases", [])}
    else:
        problems.append(f"缺少 {manifest_path.name}（冻结病例清单）；请从 {FORMS_DIR_HINT} 复制")

    headers = {"qa_20rounds": QA_HEADER, "student_expert": STUDENT_HEADER, "error_evolution": ERROR_HEADER}
    parsed: dict[str, list[dict[str, str]]] = {}
    for key, name in FORMS.items():
        path = forms_dir / name
        if not path.is_file():
            problems.append(f"缺少 {name}；请从 {FORMS_DIR_HINT} 复制模板后填写")
            continue
        parsed[key] = _check_header(path, headers[key], problems)
    if problems:
        print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    summaries = {
        "qa_20rounds": summarize_qa(parsed["qa_20rounds"], frozen),
        "student_expert": summarize_student(parsed["student_expert"], frozen),
        "error_evolution": summarize_error(parsed["error_evolution"], frozen),
    }
    verdicts = (_write_verdicts(summaries, args.policy_confirmed_by, args.failure_policy)
                if args.policy_confirmed_by else None)
    report = {"ok": True, "frozen_case_versions": [f"{case}@{version}" for case, version in sorted(frozen)],
              "summaries": summaries, "verdicts_as_recorded": verdicts,
              "scope": "Counts and rates over valid rows only; missing rows never enter a denominator."}
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

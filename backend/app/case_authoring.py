"""Authoring-time checks for teacher-created cases.

Pydantic already enforces structure and cross references. These checks add teaching-safety rules:
no diagnosis leakage into student-visible text, reachable rubric facts, and the completeness a case
needs before it can be published. They never certify medical correctness; that stays with the
human reviewer recorded at publication.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from . import exam_catalog
from .adapters import diagnosis_leak_terms
from .disclosure import normalize
from .models import CaseDefinition

STAGES = ("病史采集", "检查选择", "鉴别诊断", "诊断", "治疗")


@dataclass(frozen=True)
class Issue:
    level: str  # "error" | "warning"
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "code": self.code, "message": self.message}


def schema_errors(error: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "level": "error",
            "code": "schema",
            "message": f"{'.'.join(str(part) for part in item['loc']) or '病例'}: {item['msg']}",
        }
        for item in error.errors()
    ]


def check_case(
    case: CaseDefinition,
    for_publish: bool = False,
    require_classification: bool = False,
    require_subspecialty: bool | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    leaks = diagnosis_leak_terms(case)

    def leaks_in(text: str) -> list[str]:
        folded = "".join(text.casefold().split())
        return sorted(term for term in leaks if "".join(term.split()) in folded)

    # 科室/亚专科编码验证
    from .department_catalog import (
        department_has_subspecialties,
        is_valid_combination,
        validate_department_code,
        validate_subspecialty_code,
        get_parent_department,
    )
    if case.department_code and validate_department_code(case.department_code) is None:
        issues.append(Issue("error", "invalid_department_code",
                            f"科室编码 '{case.department_code}' 不在受控字典中"))
    if case.subspecialty_code and validate_subspecialty_code(case.subspecialty_code) is None:
        issues.append(Issue("error", "invalid_subspecialty_code",
                            f"亚专科编码 '{case.subspecialty_code}' 不在受控字典中"))
    if case.subspecialty_code and not case.department_code:
        issues.append(Issue("error", "department_missing", "选择亚专科时必须同时选择所属科室"))
    elif case.department_code and case.subspecialty_code and not is_valid_combination(
        case.department_code, case.subspecialty_code
    ):
        parent = get_parent_department(case.subspecialty_code)
        if parent:
            message = f"亚专科 '{case.subspecialty_code}' 属于科室 '{parent}'，与所选科室 '{case.department_code}' 不匹配"
        else:
            message = "科室与亚专科组合不属于受控目录"
        issues.append(Issue("error", "department_mismatch", message))

    if for_publish or require_classification:
        # 旧内置病例在读取时由兼容映射补充展示值，不写回原文件；新教师病例必须显式分类。
        classification_required = require_classification or case.authored_by is not None
        if not case.department_code:
            level = "error" if classification_required else "warning"
            message = "发布前须选择科室" if classification_required else "建议设置科室编码"
            issues.append(Issue(level, "missing_department", message))
        needs_subspecialty = (
            department_has_subspecialties(case.department_code)
            if require_subspecialty is None and case.department_code
            else bool(require_subspecialty)
        )
        if not case.subspecialty_code and case.department_code and needs_subspecialty:
            level = "error" if classification_required else "warning"
            message = "发布前须选择亚专科" if classification_required else "建议设置亚专科编码"
            issues.append(Issue(level, "missing_subspecialty", message))

    visible = {
        "标题": case.title,
        "专科": case.specialty,
        "公开编号": case.public_id,
        "主诉": case.chief_complaint,
    }
    for fact in case.facts:
        visible[f"事实 {fact.fact_id}"] = fact.content
    for exam in case.exams:
        visible[f"检查名称 {exam.exam_id}"] = exam.standard_name
        visible[f"检查结果 {exam.exam_id}"] = exam.result or ""
    for treatment in case.treatments:
        visible[f"处置名称 {treatment.treatment_id}"] = treatment.name
    for rule in case.rules:
        visible[f"病情变化说明 {rule.rule_id}"] = rule.effect.message
    for option in case.differential_options:
        visible[f"鉴别选项 {option.candidate_id}"] = option.label
    for where, text in visible.items():
        found = leaks_in(text)
        if found:
            issues.append(Issue("error", "diagnosis_leak", f"{where} 含有诊断词 {found}，会向学生泄露答案"))

    for name in exam_catalog.unmapped_case_exams(case):
        issues.append(Issue("error", "exam_not_in_catalog",
                            f"检查 \"{name}\" 不在全院检查目录中，学生无法开立；请改用目录名称或其别名"))

    rule_fact_ids = {fid for rule in case.rules for fid in rule.effect.new_fact_ids}
    facts = {fact.fact_id: fact for fact in case.facts}
    for rubric in case.rubric:
        if rubric.evidence_type == "ask_event":
            fact = facts[rubric.item_id]
            reasons = []
            if not fact.patient_known:
                reasons.append("patient_known须为true（患者知晓）")
            if not fact.current:
                reasons.append("current须为true（当前有效）")
            if not any(normalize(trigger) for trigger in fact.disclosure_triggers):
                reasons.append("disclosure_triggers须至少含一个去除空白后有效的询问触发词")
            if fact.status_valid_to is not None and fact.status_valid_to < 0:
                reasons.append("status_valid_to不能早于模拟时钟起点0分钟")
            if reasons:
                issues.append(Issue("error", "unreachable_rubric_fact", f"问诊评分项 {rubric.item_id} 对应的事实无法问到：" + "；".join(reasons)))
        if rubric.evidence_type == "ddx_evidence":
            if rubric.differential is None:
                issues.append(Issue("warning", "ddx_without_rule", f"鉴别项 {rubric.item_id} 没有支持/反对证据规则，只能进入人工复核"))
            if not case.differential_options:
                issues.append(Issue("error", "no_differential_options", "存在鉴别评分项但未配置前端鉴别选项"))
    for fact in case.facts:
        if not fact.disclosure_triggers and not fact.auto_volunteer and fact.fact_id not in rule_fact_ids:
            issues.append(Issue("warning", "unreachable_fact", f"事实 {fact.fact_id} 没有触发词且不由规则揭示，学生无法获知"))
    for rule in case.rules:
        if not rule.medically_reviewed:
            issues.append(Issue("warning", "rule_unreviewed", f"规则 {rule.rule_id} 未经医学审核"))
    if not case.rules:
        issues.append(Issue("warning", "no_evolution_rule", "未配置任何病情演进规则，无法验证错误决策分支"))

    if for_publish:
        stages = {rubric.stage for rubric in case.rubric}
        for stage in STAGES:
            if stage not in stages:
                issues.append(Issue("error", "missing_stage", f"缺少评分环节：{stage}"))
        if not case.hidden_diagnosis:
            issues.append(Issue("error", "no_hidden_diagnosis", "发布前须填写隐藏诊断（供后端评分，不对学生展示）"))
        if not case.sources:
            issues.append(Issue("error", "no_sources", "发布前须记录病例来源或依据"))
        if not case.treatments:
            issues.append(Issue("error", "no_treatments", "发布前须配置可选处置"))
        if not any(rubric.critical for rubric in case.rubric):
            issues.append(Issue("error", "no_critical_item", "发布前须至少标注一个关键评分项"))
    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(issue.level == "error" for issue in issues)

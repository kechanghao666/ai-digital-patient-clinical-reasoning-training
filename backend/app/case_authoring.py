"""Authoring-time checks for teacher-created cases.

Pydantic already enforces structure and cross references. These checks add teaching-safety rules:
no diagnosis leakage into student-visible text, reachable rubric facts, and the completeness a case
needs before it can be published. They never certify medical correctness; that stays with the
human reviewer recorded at publication.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

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


def _leak_terms(case: CaseDefinition) -> set[str]:
    terms: set[str] = set()
    for rubric in case.rubric:
        if rubric.diagnosis is not None:
            terms.update(name.strip().casefold() for name in rubric.diagnosis.names)
            terms.update(term.strip().casefold() for term in rubric.diagnosis.leak_terms)
    return {term for term in terms if term}


def check_case(case: CaseDefinition, for_publish: bool = False) -> list[Issue]:
    issues: list[Issue] = []
    leaks = _leak_terms(case)

    def leaks_in(text: str) -> list[str]:
        folded = "".join(text.casefold().split())
        return sorted(term for term in leaks if "".join(term.split()) in folded)

    visible = {
        "标题": case.title,
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

    rule_fact_ids = {fid for rule in case.rules for fid in rule.effect.new_fact_ids}
    facts = {fact.fact_id: fact for fact in case.facts}
    for rubric in case.rubric:
        if rubric.evidence_type == "ask_event":
            fact = facts[rubric.item_id]
            if not fact.disclosure_triggers:
                issues.append(Issue("error", "unreachable_rubric_fact", f"评分项 {rubric.item_id} 对应的事实没有披露触发词，学生无法问到"))
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

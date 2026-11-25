"""
科室与亚专科受控字典。

来源：《医疗机构诊疗科目名录》（原卫生部 1994 年下发，国家卫生健康委官网现行公布版本）。
适用范围：医疗机构执业登记与科室设置。

三级模型：
  L1 科室（department）       例：内科、外科、急诊医学科
    └─ L2 亚专科/专业方向      例：心血管内科专业、普通外科专业
         └─ L3 教学病种/病例    例：STEMI-001（CASE-001），多版本归并为一个病种

约束：
1. L1/L2 使用受控字典，不由教师自由输入。
2. 参考目录按选定分类覆盖，逐项标明来源。
3. 本校启用项和已有病例分别计数。
4. 目录中可以有暂无病例的科室；启用目录项不会凭空增加病例或训练数。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from .department_catalog_data import (
    DEPARTMENT_NAMES,
    EXCLUDED_LEVEL_1_ITEMS,
    EXCLUDED_LEVEL_2_ITEMS,
    EXCLUDED_LEVEL_3_ITEMS,
    REFERENCE_CATALOG_SCOPE,
    REFERENCE_CATALOG_VERSION,
    REFERENCE_SOURCES,
    SUBSPECIALTY_NAMES,
    UNRESOLVED_SOURCE_ITEMS,
)

def _enum_code_name(code: str) -> str:
    return f"CODE_{code.replace('.', '_')}"


_department_members = {
    "INTERNAL_MEDICINE": "03",
    "SURGERY": "04",
    "EMERGENCY": "20",
}
_department_members.update({
    _enum_code_name(code): code
    for code in DEPARTMENT_NAMES
    if code not in _department_members.values()
})
DepartmentCode = Enum("DepartmentCode", _department_members, type=str)

_subspecialty_members = {
    "RESPIRATORY": "03.01",
    "GASTROENTEROLOGY": "03.02",
    "NEUROLOGY_INTERNAL": "03.03",
    "CARDIOVASCULAR": "03.04",
    "HEMATOLOGY": "03.05",
    "ENDOCRINE": "03.07",
    "GENERAL_SURGERY": "04.01",
}
_subspecialty_members.update({
    _enum_code_name(code): code
    for code in SUBSPECIALTY_NAMES
    if code not in _subspecialty_members.values()
})
SubspecialtyCode = Enum("SubspecialtyCode", _subspecialty_members, type=str)


# 名录 L2 编码以点前两位归属 L1；所有编码均来自受控参考数据。
SUBSPECIALTY_PARENT: dict[str, str] = {
    code: code.split(".", 1)[0] for code in SUBSPECIALTY_NAMES
}


def validate_department_code(code: str | None) -> str | None:
    """验证科室编码是否有效。

    Args:
        code: 科室编码字符串

    Returns:
        有效时返回编码，无效时返回 None
    """
    if not code:
        return None
    return code if code in DEPARTMENT_NAMES else None


def validate_subspecialty_code(code: str | None) -> str | None:
    """验证亚专科编码是否有效。

    Args:
        code: 亚专科编码字符串

    Returns:
        有效时返回编码，无效时返回 None
    """
    if not code:
        return None
    return code if code in SUBSPECIALTY_NAMES else None


def get_parent_department(subspecialty_code: str) -> str | None:
    """获取亚专科所属的科室编码。

    Args:
        subspecialty_code: 亚专科编码

    Returns:
        科室编码，若不存在则返回 None
    """
    return SUBSPECIALTY_PARENT.get(subspecialty_code)


def department_has_subspecialties(department_code: str) -> bool:
    """Whether the reference catalog lists coded child items for this department."""
    return department_code in SUBSPECIALTY_PARENT.values()


def is_valid_combination(department_code: str | None, subspecialty_code: str | None) -> bool:
    """验证科室与亚专科的组合是否匹配。

    规则：
    1. 两者都为空时有效（允许未分类病例）
    2. 仅科室不为空时有效（允许只到科室级别）
    3. 仅亚专科不为空时无效（亚专科必须归属科室）
    4. 两者都不为空时，需验证亚专科确实属于该科室

    Args:
        department_code: 科室编码
        subspecialty_code: 亚专科编码

    Returns:
        组合是否有效
    """
    if not subspecialty_code:
        # 无亚专科时，科室可空可不空
        return not department_code or validate_department_code(department_code) is not None

    # 有亚专科时，必须验证其有效性
    if validate_subspecialty_code(subspecialty_code) is None:
        return False

    parent = get_parent_department(subspecialty_code)
    if not parent:
        return False

    # 若有科室编码，必须匹配
    if department_code:
        return department_code == parent

    return True


def get_department_display_name(code: str) -> str:
    """获取科室显示名称。"""
    return DEPARTMENT_NAMES.get(code, code)


def get_subspecialty_display_name(code: str) -> str:
    """获取亚专科显示名称。"""
    return SUBSPECIALTY_NAMES.get(code, code)


def full_specialty_name(department_code: str | None, subspecialty_code: str | None) -> str:
    """生成完整的专科显示名称。

    格式：<科室>/<亚专科> 或 <科室> 或 <亚专科> 或空

    Args:
        department_code: 科室编码
        subspecialty_code: 亚专科编码

    Returns:
        显示名称字符串
    """
    dept_name = get_department_display_name(department_code) if department_code else None
    subsp_name = get_subspecialty_display_name(subspecialty_code) if subspecialty_code else None

    if dept_name and subsp_name:
        return f"{dept_name}/{subsp_name}"
    if subsp_name:
        return subsp_name
    if dept_name:
        return dept_name
    return ""


def catalog_for_student(has_cases: set[tuple[str | None, str | None]]) -> dict[str, Any]:
    """生成学生端病例库筛选用的科室目录。

    只返回当前有可训练病例的科室组合，不绕过草稿、审核或停止新训练的限制。

    Args:
        has_cases: 有可训练病例的（科室编码, 亚专科编码）元组集合

    Returns:
        分层目录结构，用于前端下拉
    """
    # 按科室分组
    departments: dict[str, dict[str, Any]] = {}

    for dept_code, subsp_code in has_cases:
        if dept_code is None and subsp_code is None:
            continue

        # 获取正确的科室编码（优先从亚专科推导）
        actual_dept = dept_code
        if subsp_code and not dept_code:
            actual_dept = get_parent_department(subsp_code)

        if not actual_dept:
            continue

        if actual_dept not in departments:
            departments[actual_dept] = {
                "code": actual_dept,
                "name": get_department_display_name(actual_dept),
                "subspecialties": [],
            }

        if subsp_code:
            # 检查是否已添加
            if not any(s["code"] == subsp_code for s in departments[actual_dept]["subspecialties"]):
                departments[actual_dept]["subspecialties"].append({
                    "code": subsp_code,
                    "name": get_subspecialty_display_name(subsp_code),
                })

    # 排序
    result = sorted(departments.values(), key=lambda d: d["code"])
    for dept in result:
        dept["subspecialties"].sort(key=lambda s: s["code"])

    return {
        "catalog_version": REFERENCE_CATALOG_VERSION,
        "scope": REFERENCE_CATALOG_SCOPE,
        "source_documents": REFERENCE_SOURCES,
        "departments": result,
    }


def catalog_for_teacher(
    enabled_departments: set[str] | None = None,
    enabled_subspecialties: set[str] | None = None,
) -> dict[str, Any]:
    """生成教师端可选的目录片段。

    ``None`` 在纯目录测试中表示不筛选；运行中的教师 API 必须传入已保存的院校启用集合。
    此处返回有编码的临床/保健目录项，不代表院校实际开设清单。
    """
    departments: dict[str, dict[str, Any]] = {}

    for subsp_code, dept_code in SUBSPECIALTY_PARENT.items():
        if enabled_departments is not None and dept_code not in enabled_departments:
            continue
        if enabled_subspecialties is not None and subsp_code not in enabled_subspecialties:
            continue

        if dept_code not in departments:
            departments[dept_code] = {
                "code": dept_code,
                "name": get_department_display_name(dept_code),
                "enabled": dept_code in enabled_departments if enabled_departments else True,
                "subspecialties": [],
            }

        departments[dept_code]["subspecialties"].append({
            "code": subsp_code,
            "name": get_subspecialty_display_name(subsp_code),
            "enabled": True,
        })

    # 添加无下属亚专科的科室（如急诊医学科）
    for dept_code in DEPARTMENT_NAMES:
        if dept_code not in departments:
            if enabled_departments is not None and dept_code not in enabled_departments:
                continue
            departments[dept_code] = {
                "code": dept_code,
                "name": get_department_display_name(dept_code),
                "enabled": dept_code in enabled_departments if enabled_departments is not None else True,
                "subspecialties": [],
            }

    result = sorted(departments.values(), key=lambda d: d["code"])
    for dept in result:
        dept["subspecialties"].sort(key=lambda s: s["code"])

    return {
        "catalog_version": REFERENCE_CATALOG_VERSION,
        "scope": REFERENCE_CATALOG_SCOPE,
        "source_documents": REFERENCE_SOURCES,
        "departments": result,
    }


# 现有病例 specialty 字段到科室/亚专科编码的映射
# 注：这是基于现有 5 个独立病例的 specialty 原值的兼容映射
SPECIALTY_LEGACY_MAPPING: dict[str, tuple[str, str]] = {
    # specialty 原值 -> (科室编码, 亚专科编码)
    "心内科/急诊": ("03", "03.04"),       # 心血管内科专业
    "普外科/急诊": ("04", "04.01"),       # 普通外科专业
    "神经内科/急诊": ("03", "03.03"),     # 神经内科专业
    "呼吸内科/急诊": ("03", "03.01"),     # 呼吸内科专业
    "内分泌科/急诊": ("03", "03.07"),     # 内分泌专业
}


def map_legacy_specialty(specialty: str) -> tuple[str | None, str | None]:
    """将旧的 specialty 自由文本映射到科室/亚专科编码。

    对于有歧义的值，返回 None 并由教师确认。

    Args:
        specialty: 原 specialty 字段值

    Returns:
        (科室编码, 亚专科编码) 元组，无法映射时返回 (None, None)
    """
    if not specialty:
        return (None, None)

    # 直接匹配
    if specialty in SPECIALTY_LEGACY_MAPPING:
        return SPECIALTY_LEGACY_MAPPING[specialty]

    # 尝试模糊匹配（去除空格、统一标点等）
    normalized = specialty.strip().replace("／", "/").replace(" ", "")

    for original, mapping in SPECIALTY_LEGACY_MAPPING.items():
        if original.replace(" ", "") == normalized:
            return mapping

    # 无法映射
    return (None, None)


def explain_legacy_mapping(specialty: str) -> dict[str, Any]:
    """解释旧 specialty 字段的映射结果。

    Args:
        specialty: 原 specialty 字段值

    Returns:
        包含映射结果和说明的字典
    """
    dept_code, subsp_code = map_legacy_specialty(specialty)

    if dept_code or subsp_code:
        return {
            "original": specialty,
            "mapped": True,
            "department_code": dept_code,
            "subspecialty_code": subsp_code,
            "department_name": get_department_display_name(dept_code) if dept_code else None,
            "subspecialty_name": get_subspecialty_display_name(subsp_code) if subsp_code else None,
            "full_name": full_specialty_name(dept_code, subsp_code),
            "note": "自动映射，需教师确认" if specialty not in SPECIALTY_LEGACY_MAPPING else "明确映射",
        }

    return {
        "original": specialty,
        "mapped": False,
        "department_code": None,
        "subspecialty_code": None,
        "department_name": None,
        "subspecialty_name": None,
        "full_name": None,
        "note": "无法自动映射，需教师指定科室与亚专科",
    }

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

# 参考目录片段：只收录本项目当前已核对的常见项，不是《医疗机构诊疗科目名录》全量转录。
# 来源及修订说明见 docs/科室差距清单.md；不等于院校教研室/住培专业或本校实际开设清单。
REFERENCE_CATALOG_VERSION = "NHC-1994-27-selected-items"

class DepartmentCode(str, Enum):
    """L1 科室编码（参考《诊疗科目名录》）。"""
    INTERNAL_MEDICINE = "03"          # 内科
    SURGERY = "04"                    # 外科
    EMERGENCY = "20"                  # 急诊医学科（名录代码）
    # 以下为可扩展项，暂不全部列出
    # OBSTETRICS_GYNECOLOGY = "05"    # 妇产科
    # PEDIATRICS = "06"               # 儿科
    # NEUROLOGY = "07"                # 神经内科（部分医院作为内科下属）
    # PSYCHIATRY = "08"               # 精神科


class SubspecialtyCode(str, Enum):
    """L2 亚专科/专业方向编码。

    编码格式：<科室代码>.<专业序号>
    参考《诊疗科目名录》内科下属专业。
    """
    # 内科下属专业
    RESPIRATORY = "03.01"             # 呼吸内科专业
    GASTROENTEROLOGY = "03.02"        # 消化内科专业
    NEUROLOGY_INTERNAL = "03.03"      # 神经内科专业
    CARDIOVASCULAR = "03.04"          # 心血管内科专业
    HEMATOLOGY = "03.05"              # 血液内科专业
    ENDOCRINE = "03.07"               # 内分泌专业
    # 外科下属专业
    GENERAL_SURGERY = "04.01"         # 普通外科专业
    # 急诊医学科（通常无下属专业方向）


# 科室与亚专科层级关系
SUBSPECIALTY_PARENT: dict[str, str] = {
    "03.01": "03",   # 呼吸内科专业 → 内科
    "03.02": "03",   # 消化内科专业 → 内科
    "03.03": "03",   # 神经内科专业 → 内科
    "03.04": "03",   # 心血管内科专业 → 内科
    "03.05": "03",   # 血液内科专业 → 内科
    "03.07": "03",   # 内分泌专业 → 内科
    "04.01": "04",   # 普通外科专业 → 外科
}

# 科室显示名称
DEPARTMENT_NAMES: dict[str, str] = {
    "03": "内科",
    "04": "外科",
    "20": "急诊医学科",
}

# 亚专科显示名称
SUBSPECIALTY_NAMES: dict[str, str] = {
    "03.01": "呼吸内科专业",
    "03.02": "消化内科专业",
    "03.03": "神经内科专业",
    "03.04": "心血管内科专业",
    "03.05": "血液内科专业",
    "03.07": "内分泌专业",
    "04.01": "普通外科专业",
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
    try:
        return DepartmentCode(code).value
    except ValueError:
        return None


def validate_subspecialty_code(code: str | None) -> str | None:
    """验证亚专科编码是否有效。

    Args:
        code: 亚专科编码字符串

    Returns:
        有效时返回编码，无效时返回 None
    """
    if not code:
        return None
    try:
        return SubspecialtyCode(code).value
    except ValueError:
        return None


def get_parent_department(subspecialty_code: str) -> str | None:
    """获取亚专科所属的科室编码。

    Args:
        subspecialty_code: 亚专科编码

    Returns:
        科室编码，若不存在则返回 None
    """
    return SUBSPECIALTY_PARENT.get(subspecialty_code)


def department_has_subspecialties(department_code: str) -> bool:
    """Whether the current reference subset lists child items for this department."""
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

    return {"departments": result}


def catalog_for_teacher(
    enabled_departments: set[str] | None = None,
    enabled_subspecialties: set[str] | None = None,
) -> dict[str, Any]:
    """生成教师端可选的目录片段。

    ``None`` 在纯目录测试中表示不筛选；运行中的教师 API 必须传入已保存的院校启用集合。
    此处仅表示当前已核对的参考条目，不代表全量医疗机构目录或本校实际科室。
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
    for dept_code in DepartmentCode:
        if dept_code.value not in departments:
            if enabled_departments is not None and dept_code.value not in enabled_departments:
                continue
            departments[dept_code.value] = {
                "code": dept_code.value,
                "name": get_department_display_name(dept_code.value),
                "enabled": dept_code.value in enabled_departments if enabled_departments else True,
                "subspecialties": [],
            }

    result = sorted(departments.values(), key=lambda d: d["code"])
    for dept in result:
        dept["subspecialties"].sort(key=lambda s: s["code"])

    return {
        "catalog_version": REFERENCE_CATALOG_VERSION,
        "scope": "已核对参考条目子集；不是完整目录，也不是院校实际开设清单",
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

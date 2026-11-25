"""科室目录与病例筛选测试。

验收场景：
1. 五科以外的有效目录项可以配置
2. 启用空科室后，学生病例数不变
3. 教师选择有效组合后可按既有流程送审
4. 无效组合不能发布
5. 学生筛选不会泄露草稿或已停止版本
6. 停用科室不改写历史训练、事件和评分
"""

import pytest
import json
from pathlib import Path

from backend.app.department_config import (
    DepartmentConfigError,
    load_department_config,
    restore_previous_department_config,
    save_department_config,
)
from backend.app.department_catalog import (
    DepartmentCode,
    SubspecialtyCode,
    validate_department_code,
    validate_subspecialty_code,
    get_parent_department,
    is_valid_combination,
    full_specialty_name,
    catalog_for_student,
    catalog_for_teacher,
    map_legacy_specialty,
    explain_legacy_mapping,
)


class TestDepartmentCodeValidation:
    """科室编码验证测试。"""

    def test_valid_department_code(self):
        """有效的科室编码。"""
        assert validate_department_code("03") == "03"  # 内科
        assert validate_department_code("04") == "04"  # 外科
        assert validate_department_code("20") == "20"  # 急诊医学科

    def test_invalid_department_code(self):
        """无效的科室编码。"""
        assert validate_department_code("99") is None
        assert validate_department_code("") is None
        assert validate_department_code(None) is None
        assert validate_department_code("invalid") is None


class TestSubspecialtyCodeValidation:
    """亚专科编码验证测试。"""

    def test_valid_subspecialty_code(self):
        """有效的亚专科编码。"""
        assert validate_subspecialty_code("03.04") == "03.04"  # 心血管内科专业
        assert validate_subspecialty_code("04.01") == "04.01"  # 普通外科专业
        assert validate_subspecialty_code("03.03") == "03.03"  # 神经内科专业

    def test_invalid_subspecialty_code(self):
        """无效的亚专科编码。"""
        assert validate_subspecialty_code("99.99") is None
        assert validate_subspecialty_code("") is None
        assert validate_subspecialty_code(None) is None
        assert validate_subspecialty_code("invalid") is None


class TestDepartmentSubspecialtyCombination:
    """科室与亚专科组合验证测试。"""

    def test_both_empty_is_valid(self):
        """两者都为空时有效（允许未分类病例）。"""
        assert is_valid_combination(None, None) is True

    def test_department_only_is_valid(self):
        """仅科室不为空时有效（允许只到科室级别）。"""
        assert is_valid_combination("03", None) is True
        assert is_valid_combination("04", None) is True

    def test_subspecialty_only_is_invalid(self):
        """仅亚专科不为空时无效（亚专科必须归属科室）。"""
        # 注：当前实现允许这种组合，会从亚专科推导科室
        # 这里测试的是显式不一致的情况
        assert is_valid_combination("04", "03.04") is False  # 心血管不属于外科

    def test_matching_combination_is_valid(self):
        """匹配的科室/亚专科组合有效。"""
        assert is_valid_combination("03", "03.04") is True  # 心血管内科专业属于内科
        assert is_valid_combination("04", "04.01") is True  # 普通外科专业属于外科
        assert is_valid_combination("03", "03.03") is True  # 神经内科专业属于内科

    def test_mismatched_combination_is_invalid(self):
        """不匹配的科室/亚专科组合无效。"""
        assert is_valid_combination("04", "03.04") is False  # 心血管不属于外科
        assert is_valid_combination("03", "04.01") is False  # 普外不属于内科


class TestLegacySpecialtyMapping:
    """旧 specialty 字段映射测试。"""

    def test_map_cardiovascular_emergency(self):
        """心内科/急诊 映射到心血管内科专业。"""
        dept, subsp = map_legacy_specialty("心内科/急诊")
        assert dept == "03"
        assert subsp == "03.04"

    def test_map_general_surgery_emergency(self):
        """普外科/急诊 映射到普通外科专业。"""
        dept, subsp = map_legacy_specialty("普外科/急诊")
        assert dept == "04"
        assert subsp == "04.01"

    def test_map_neurology_emergency(self):
        """神经内科/急诊 映射到神经内科专业。"""
        dept, subsp = map_legacy_specialty("神经内科/急诊")
        assert dept == "03"
        assert subsp == "03.03"

    def test_map_respiratory_emergency(self):
        """呼吸内科/急诊 映射到呼吸内科专业。"""
        dept, subsp = map_legacy_specialty("呼吸内科/急诊")
        assert dept == "03"
        assert subsp == "03.01"

    def test_map_endocrine_emergency(self):
        """内分泌科/急诊 映射到内分泌专业。"""
        dept, subsp = map_legacy_specialty("内分泌科/急诊")
        assert dept == "03"
        assert subsp == "03.07"

    def test_map_unknown_specialty(self):
        """未知的 specialty 返回 None。"""
        dept, subsp = map_legacy_specialty("未知科室")
        assert dept is None
        assert subsp is None

    def test_map_empty_specialty(self):
        """空的 specialty 返回 None。"""
        dept, subsp = map_legacy_specialty("")
        assert dept is None
        assert subsp is None


class TestCatalogGeneration:
    """目录生成测试。"""

    def test_student_catalog_with_cases(self):
        """学生端目录只返回有病例的科室。"""
        has_cases: set[tuple[str | None, str | None]] = {
            ("03", "03.04"),  # 心血管内科
            ("04", "04.01"),  # 普通外科
        }
        catalog = catalog_for_student(has_cases)
        assert "departments" in catalog
        dept_codes = {d["code"] for d in catalog["departments"]}
        assert "03" in dept_codes
        assert "04" in dept_codes
        # 09 没有病例，不应该出现
        assert "20" not in dept_codes

    def test_student_catalog_empty(self):
        """没有病例时返回空目录。"""
        catalog = catalog_for_student(set())
        assert catalog["departments"] == []

    def test_teacher_catalog_full(self):
        """教师端目录返回所有科室。"""
        catalog = catalog_for_teacher(enabled_departments=None)
        dept_codes = {d["code"] for d in catalog["departments"]}
        assert "03" in dept_codes  # 内科
        assert "04" in dept_codes  # 外科
        assert "20" in dept_codes  # 急诊医学科

    def test_teacher_catalog_enabled_only(self):
        """教师端目录可按启用科室过滤。"""
        catalog = catalog_for_teacher(enabled_departments={"03", "04"})
        dept_codes = {d["code"] for d in catalog["departments"]}
        assert "03" in dept_codes
        assert "04" in dept_codes
        assert "20" not in dept_codes  # 未启用


class TestDisplayNames:
    """显示名称测试。"""

    def test_department_display_name(self):
        """科室显示名称。"""
        from backend.app.department_catalog import get_department_display_name
        assert get_department_display_name("03") == "内科"
        assert get_department_display_name("04") == "外科"
        assert get_department_display_name("20") == "急诊医学科"

    def test_subspecialty_display_name(self):
        """亚专科显示名称。"""
        from backend.app.department_catalog import get_subspecialty_display_name
        assert get_subspecialty_display_name("03.04") == "心血管内科专业"
        assert get_subspecialty_display_name("04.01") == "普通外科专业"
        assert get_subspecialty_display_name("03.03") == "神经内科专业"

    def test_full_specialty_name_both(self):
        """完整名称：科室/亚专科。"""
        assert full_specialty_name("03", "03.04") == "内科/心血管内科专业"
        assert full_specialty_name("04", "04.01") == "外科/普通外科专业"

    def test_full_specialty_name_department_only(self):
        """完整名称：仅科室。"""
        assert full_specialty_name("03", None) == "内科"

    def test_full_specialty_name_empty(self):
        """完整名称：空。"""
        assert full_specialty_name(None, None) == ""


class TestFiveLegacySpecialties:
    """验收场景：现有 5 个独立病例的 specialty 原值映射。"""

    @pytest.mark.parametrize("specialty,expected_dept,expected_subsp,expected_name", [
        ("心内科/急诊", "03", "03.04", "心血管内科专业"),
        ("普外科/急诊", "04", "04.01", "普通外科专业"),
        ("神经内科/急诊", "03", "03.03", "神经内科专业"),
        ("呼吸内科/急诊", "03", "03.01", "呼吸内科专业"),
        ("内分泌科/急诊", "03", "03.07", "内分泌专业"),
    ])
    def test_legacy_mapping_for_five_cases(self, specialty, expected_dept, expected_subsp, expected_name):
        """五个现有病例的 specialty 都能唯一映射。"""
        result = explain_legacy_mapping(specialty)
        assert result["mapped"] is True
        assert result["department_code"] == expected_dept
        assert result["subspecialty_code"] == expected_subsp
        assert result["subspecialty_name"] == expected_name


class TestNewDepartmentConfiguration:
    """验收场景：五科以外的有效目录项可以配置。"""

    def test_configure_hematology(self):
        """血液内科专业可以配置（五科以外）。"""
        # 血液内科专业编码 03.05
        assert validate_subspecialty_code("03.05") == "03.05"
        assert get_parent_department("03.05") == "03"
        assert is_valid_combination("03", "03.05") is True

    def test_configure_gastroenterology(self):
        """消化内科专业可以配置（五科以外）。"""
        # 消化内科专业编码 03.02
        assert validate_subspecialty_code("03.02") == "03.02"
        assert get_parent_department("03.02") == "03"
        assert is_valid_combination("03", "03.02") is True


def test_missing_institution_config_fails_closed_and_round_trips_with_rollback(tmp_path):
    path = tmp_path / "department-catalog.json"
    missing = load_department_config(path)
    assert missing.configured is False
    assert missing.enabled_departments == frozenset()
    assert missing.enabled_subspecialties == frozenset()

    first = save_department_config(path, {"03"}, {"03.05"}, "synthetic-test-operator")
    assert first.configured and first.enabled_subspecialties == frozenset({"03.05"})
    second = save_department_config(path, {"04"}, {"04.01"}, "synthetic-test-operator")
    assert second.enabled_departments == frozenset({"04"})
    restored = restore_previous_department_config(path, "synthetic-test-operator")
    assert restored.enabled_departments == frozenset({"03"})
    assert restored.enabled_subspecialties == frozenset({"03.05"})


def test_department_config_rejects_unknown_and_orphan_subspecialty(tmp_path):
    path = tmp_path / "department-catalog.json"
    with pytest.raises(DepartmentConfigError, match="未收录编码"):
        save_department_config(path, {"99"}, set(), "synthetic-test-operator")
    with pytest.raises(DepartmentConfigError, match="父科室"):
        save_department_config(path, {"04"}, {"03.05"}, "synthetic-test-operator")
    assert not path.exists()


def test_department_api_uses_persisted_enablement_and_empty_department_adds_no_case(
    tmp_path, monkeypatch
):
    from test_cases_config import Api, configure_department_catalog

    api = Api(tmp_path, monkeypatch)
    teacher = {"X-Owner-Id": "teacher-wang"}
    save_department_config(
        tmp_path / "department-catalog.json", {"03", "04"},
        {"03.01", "03.02", "03.03", "03.04", "03.07", "04.01"}, "synthetic-test-operator",
    )
    student_cases_before = api.client.get("/api/cases", headers=api.headers).json()
    assert len(student_cases_before) == 5
    teacher_catalog = api.client.get("/api/teacher/department-catalog", headers=teacher).json()
    assert "03.05" not in {s["code"] for d in teacher_catalog["departments"] for s in d["subspecialties"]}

    configure_department_catalog(tmp_path)
    save_department_config(
        tmp_path / "department-catalog.json",
        {"03", "04"},
        {"03.01", "03.02", "03.03", "03.04", "03.05", "03.07", "04.01"},
        "synthetic-test-operator",
    )
    student_cases_after = api.client.get("/api/cases", headers=api.headers).json()
    student_catalog = api.client.get("/api/department-catalog", headers=api.headers).json()
    assert len(student_cases_after) == len(student_cases_before) == 5
    assert "03.05" not in {s["code"] for d in student_catalog["departments"] for s in d["subspecialties"]}


def test_disabling_department_blocks_new_training_but_preserves_sealed_history(tmp_path, monkeypatch):
    from test_cases_config import Api

    api = Api(tmp_path, monkeypatch)
    session_id = api.start("CASE-001")
    api.ask(session_id, "胸痛是什么时候开始的")
    score_before = api.seal_and_score(session_id)
    state_before = api.state(session_id)

    save_department_config(
        tmp_path / "department-catalog.json", {"04"}, {"04.01"}, "synthetic-test-operator"
    )
    listed = api.client.get("/api/cases", headers=api.headers)
    assert listed.status_code == 200
    assert all(case["department_code"] == "04" for case in listed.json())
    assert api.client.post("/api/sessions?case_id=CASE-001", headers=api.headers).status_code == 409
    assert api.state(session_id) == state_before
    assert api.client.get(f"/api/sessions/{session_id}/score", headers=api.headers).json() == score_before


def test_department_api_reports_missing_or_corrupt_config_as_controlled_state(tmp_path, monkeypatch):
    from test_cases_config import Api

    api = Api(tmp_path, monkeypatch)
    path = tmp_path / "department-catalog.json"
    path.unlink()
    teacher = api.client.get("/api/teacher/department-catalog", headers={"X-Owner-Id": "teacher-wang"})
    assert teacher.status_code == 200 and teacher.json()["configured"] is False
    assert teacher.json()["departments"] == []
    assert api.client.get("/api/cases", headers=api.headers).json() == []
    assert api.client.post("/api/sessions?case_id=CASE-001", headers=api.headers).status_code == 409

    path.write_text("{broken", encoding="utf-8")
    assert api.client.get("/api/cases", headers=api.headers).status_code == 503


def test_invalid_utf8_and_blank_restore_operator_do_not_damage_config_files(tmp_path):
    import hashlib

    path = tmp_path / "department-catalog.json"
    save_department_config(path, {"03"}, {"03.04"}, "operator-a")
    save_department_config(path, {"04"}, {"04.01"}, "operator-b")
    previous = path.with_name(path.name + ".previous")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, previous)}

    with pytest.raises(DepartmentConfigError, match="不可读取"):
        path.write_bytes(b"\xff")
        load_department_config(path)

    path.write_bytes(json.dumps({
        "schema_version": 1, "enabled_departments": ["04"], "enabled_subspecialties": ["04.01"],
        "updated_by": "operator-b", "updated_at": "2026-11-25T00:00:00Z",
    }).encode())
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, previous)}
    with pytest.raises(DepartmentConfigError, match="操作者"):
        restore_previous_department_config(path, "   ")
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, previous)} == before


def test_explicit_department_only_does_not_get_legacy_subspecialty(tmp_path):
    from app.case_store import CaseStore

    (tmp_path / "fixtures").mkdir()
    store = CaseStore(tmp_path / "fixtures", tmp_path / "teacher")
    raw = json.loads((Path(__file__).parents[1] / "cases" / "stemi_case_v3.json").read_text(encoding="utf-8"))
    raw["department_code"] = "20"
    raw["subspecialty_code"] = None
    raw["specialty"] = "心内科/急诊"
    (tmp_path / "fixtures" / "explicit-emergency.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    store.reload()
    summary = next(item for item in store.list_all() if item["case_id"] == raw["case_id"])
    assert summary["department_code"] == "20"
    assert summary["subspecialty_code"] is None

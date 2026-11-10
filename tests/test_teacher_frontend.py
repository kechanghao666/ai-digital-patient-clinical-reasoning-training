"""Teacher page exercises the real API against temporary storage, without medical sign-off."""
from streamlit.testing.v1 import AppTest
import pytest

from test_cases_config import Api, ROOT
from test_frontend_smoke import _route_requests


def button(page, label):
    return next(b for b in page.button if b.label == label)


def text(page, label):
    return next(x for x in page.text_input if x.label == label)


def teacher_page(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    _route_requests(monkeypatch, api)
    page = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=30).run()
    page.radio(key="workspace").set_value("教师病例管理").run()
    assert not page.exception, page.exception
    button(page, "以此病例新建草稿").click().run()
    assert not page.exception, page.exception
    return api, page


def test_teacher_can_save_validate_and_submit_without_faking_medical_review(tmp_path, monkeypatch):
    api, page = teacher_page(tmp_path, monkeypatch)
    text(page, "病例编号").set_value("TEACH-UI-001")
    text(page, "病例标题").set_value("教师页面新增的教学病例")
    button(page, "保存新版本草稿").click().run()
    assert not page.exception, page.exception
    case = api.store.get("TEACH-UI-001")
    assert case.title == "教师页面新增的教学病例"
    assert case.review_status.value == "draft"
    button(page, "标记结构校验通过").click().run()
    button(page, "提交医学审核").click().run()
    assert not page.exception, page.exception
    assert api.store.get("TEACH-UI-001").review_status.value == "under_review"
    # There must never be a pre-filled reviewer, date or an implicit confirmation.
    assert text(page, "真实医学审核人").value == ""
    assert page.date_input[0].value is None
    button(page, "确认审核并发布").click().run()
    assert api.store.get("TEACH-UI-001").review_status.value == "under_review"
    assert any("未执行发布" in x.value for x in page.error)


def test_malformed_case_configuration_is_not_saved(tmp_path, monkeypatch):
    api, page = teacher_page(tmp_path, monkeypatch)
    text(page, "病例编号").set_value("TEACH-BAD-UI")
    page.checkbox(key="case_editor_advanced").check()
    page.text_area(key="case_editor_facts").set_value("{invalid")
    button(page, "保存新版本草稿").click().run()
    assert not page.exception, page.exception
    assert any("不是有效的 JSON" in x.value for x in page.error)
    assert api.store.versions("TEACH-BAD-UI") == []


def test_teacher_can_save_prior_result_requirements_in_advanced_editor(tmp_path, monkeypatch):
    import json

    api, page = teacher_page(tmp_path, monkeypatch)
    text(page, "病例编号").set_value("TEACH-PRIOR-UI")
    page.checkbox(key="case_editor_advanced").check()
    case = page.session_state["teacher_case"]
    rubric = case["rubric"]
    treatment = next(r for r in rubric if r["evidence_type"] == "treatment_execution")
    exam_id = next(e["exam_id"] for e in case["exams"] if e.get("configured", True) and e.get("result"))
    treatment["required_prior_exam_ids"] = [exam_id]
    page.text_area(key="case_editor_rubric").set_value(json.dumps(rubric, ensure_ascii=False))
    button(page, "保存新版本草稿").click().run()
    assert not page.exception, page.exception
    stored = api.store.get("TEACH-PRIOR-UI")
    assert next(r for r in stored.rubric if r.item_id == treatment["item_id"]).required_prior_exam_ids == [exam_id]
    assert stored.review_status.value == "draft"


def test_inactive_advanced_configuration_does_not_override_tables(tmp_path, monkeypatch):
    api, page = teacher_page(tmp_path, monkeypatch)
    text(page, "病例编号").set_value("TEACH-TABLE-UI")
    page.text_area(key="case_editor_facts").set_value("{invalid")
    button(page, "保存新版本草稿").click().run()
    assert not page.exception, page.exception
    assert api.store.get("TEACH-TABLE-UI").facts


def test_switching_to_student_clears_teacher_hidden_case(tmp_path, monkeypatch):
    _, page = teacher_page(tmp_path, monkeypatch)
    assert page.session_state["teacher_case"]["hidden_diagnosis"]
    page.radio(key="workspace").set_value("学生训练").run()
    assert not page.exception, page.exception
    with pytest.raises(KeyError):
        page.session_state["teacher_case"]
    assert all("隐藏参考诊断" != x.label for x in page.text_input)


def test_changing_student_identity_does_not_reuse_session_snapshot(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, owner="demo-student")
    api.start("CASE-001")
    _route_requests(monkeypatch, api)
    page = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=30).run()
    button(page, "恢复训练").click().run()
    assert page.session_state["session_snapshot"]
    page.text_input(key="student_identity").set_value("demo-another-student").run()
    assert page.session_state["session_snapshot"] is None
    assert page.session_state["session_id"] is None
    assert not any(b.label == "恢复训练" for b in page.button)

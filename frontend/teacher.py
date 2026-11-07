"""Teacher workspace. All access checks and lifecycle transitions remain on the API."""
import copy
import json
import uuid
from datetime import date
from urllib.parse import quote

import streamlit as st

STATUS = {"draft": "草稿", "validated": "结构校验通过", "under_review": "待医学审核", "published": "已发布"}


def _load_editor(case, *, duplicate=False):
    for key in list(st.session_state):
        if key.startswith("case_editor_"):
            del st.session_state[key]
    data = copy.deepcopy(case)
    if duplicate:
        data.update(case_id=f"TEACH-{uuid.uuid4().hex[:8]}", title=f"{data['title']}（新草稿）",
                    version=1, review_status="draft", reviewer=None, review_date=None, authored_by=None)
        data.pop("public_id", None)
    st.session_state.teacher_case = data
    st.session_state.teacher_validation = None


def _show_issues(result):
    if result.get("valid"):
        st.success("结构校验通过。结构校验不代表医学审核。")
    for issue in result.get("issues", []):
        show = st.error if issue.get("level") == "error" else st.warning
        show(issue.get("message", "病例需要复核"))


def render_teacher(api):
    st.subheader("教师病例管理")
    st.caption("新增或修订病例，完成结构校验后送审。只有经过真实医学审核的病例才能发布。")
    # A failed authorization never renders a cached hidden case.
    cases = api("GET", "/api/teacher/cases")
    if cases is None:
        st.info("请使用有权限的教师身份。当前仅支持开发模式教师账号。")
        return

    a, b, c = st.columns(3)
    a.metric("病例版本", len(cases))
    b.metric("待审核", sum(x["review_status"] == "under_review" for x in cases))
    c.metric("已发布", sum(x["review_status"] == "published" for x in cases))
    with st.container(border=True):
        st.markdown("**病例库**")
        if cases:
            selected = st.selectbox("病例版本", cases,
                format_func=lambda x: f"{x['title']} · v{x['version']} · {STATUS.get(x['review_status'], x['review_status'])}",
                key="teacher_selected_version")
            path = f"/api/teacher/cases/{quote(selected['case_id'], safe='')}/versions/{selected['version']}"
            left, right = st.columns(2)
            if left.button("打开此版本", width="stretch"):
                raw = api("GET", path)
                if raw:
                    _load_editor(raw)
                    st.rerun()
            if right.button("以此病例新建草稿", type="primary", width="stretch"):
                raw = api("GET", path)
                if raw:
                    _load_editor(raw, duplicate=True)
                    st.rerun()
        else:
            st.info("尚无病例，可先导入结构化病例。")
        with st.expander("导入结构化病例"):
            uploaded = st.file_uploader("选择病例 JSON 文件", type=["json"], max_upload_size=2)
            if st.button("载入为新草稿", disabled=uploaded is None):
                try:
                    raw = json.loads(uploaded.getvalue().decode("utf-8-sig"))
                    if not isinstance(raw, dict) or not raw.get("title"):
                        raise ValueError("文件必须是包含病例标题的对象")
                except (ValueError, UnicodeError) as exc:
                    st.error(f"无法读取病例：{exc}")
                else:
                    checked = api("POST", "/api/teacher/cases/validate", json={
                        "public_id": "CASE-T-DRAFT", "version": 1, "review_status": "draft", **raw})
                    if checked is not None:
                        _load_editor(raw, duplicate=True)
                        st.rerun()

    data = st.session_state.get("teacher_case")
    if not data:
        st.info("选择一个病例作为模板，或导入已有病例，开始编写。")
        return

    st.divider()
    st.subheader("编写病例")
    st.caption("保存总是创建新草稿版本，不会改写已有训练引用的病例。内置病例须复制后编辑。")
    st.download_button("下载当前病例版本", data=json.dumps(data, ensure_ascii=False, indent=2),
                       file_name="teaching-case.json", mime="application/json")
    with st.form("teacher_case_editor"):
        left, right = st.columns(2)
        case_id = left.text_input("病例编号", value=data.get("case_id", ""), key="case_editor_id",
                                 help="3至40位英文字母、数字、短横线或下划线。")
        title = right.text_input("病例标题", value=data.get("title", ""), key="case_editor_title")
        specialty = left.text_input("专科", value=data.get("specialty", ""), key="case_editor_specialty")
        population = right.text_input("适用人群", value=data.get("target_population", ""), key="case_editor_population")
        complaint = st.text_area("主诉", value=data.get("chief_complaint", ""), key="case_editor_complaint")
        goals = st.text_area("教学目标（每行一项）", value="\n".join(data.get("learning_goals", [])), key="case_editor_goals")
        diagnosis = st.text_input("隐藏参考诊断（仅教师可见）", value=data.get("hidden_diagnosis") or "",
                                  key="case_editor_diagnosis")
        with st.expander("编辑症状与体征", expanded=True):
            st.caption("修改患者回答和询问触发词；多个触发词以顿号分隔。新增事实及复杂规则在高级配置中维护。")
            facts = st.data_editor([
                {"回答内容": fact["content"], "询问触发词": "、".join(fact.get("disclosure_triggers", [])),
                 "患者知晓": fact.get("patient_known", True)} for fact in data.get("facts", [])
            ], key="case_editor_fact_table", hide_index=True, width="stretch")
        with st.expander("编辑检查与回报"):
            exams = st.data_editor([
                {"检查名称": exam["standard_name"], "结果内容": exam.get("result") or "",
                 "回报等待（分钟）": exam["return_after_minutes"], "已配置结果": exam.get("configured", True)}
                for exam in data.get("exams", [])
            ], key="case_editor_exam_table", hide_index=True, width="stretch",
                column_config={"回报等待（分钟）": st.column_config.NumberColumn(min_value=0, step=1)})
        with st.expander("病例事实、检查与评分配置"):
            advanced = st.checkbox("直接编辑完整结构化配置（覆盖上方表格）", key="case_editor_advanced")
            st.caption("启用后以下配置优先于表格；修改后先执行校验。病例事实与参考答案仅用于教师编辑。")
            content = {}
            for field, label in (("facts", "症状与体征"), ("exams", "检查结果"), ("treatments", "模拟处置"),
                                 ("differential_options", "鉴别候选"), ("rules", "病情演进规则"), ("rubric", "评分量表")):
                content[field] = st.text_area(f"{label}（JSON）", value=json.dumps(data.get(field, []), ensure_ascii=False, indent=2),
                                               key=f"case_editor_{field}", height=180)
            sources = st.text_area("来源说明（每行一项）", value="\n".join(data.get("sources", [])), key="case_editor_sources")
        validate = st.form_submit_button("校验病例")
        save = st.form_submit_button("保存新版本草稿", type="primary")

    if validate or save:
        try:
            updated = {**data, **({field: json.loads(value) for field, value in content.items()} if advanced else {}),
                "case_id": case_id.strip(), "title": title.strip(), "specialty": specialty.strip(),
                "target_population": population.strip(), "chief_complaint": complaint.strip(),
                "learning_goals": [v.strip() for v in goals.splitlines() if v.strip()],
                "hidden_diagnosis": diagnosis.strip() or None,
                "sources": [v.strip() for v in sources.splitlines() if v.strip()]}
        except ValueError:
            st.error("结构化内容不是有效的 JSON，请检查后重试；尚未保存。")
        else:
            if not advanced:
                updated["facts"] = [{**original, "content": row["回答内容"],
                    "disclosure_triggers": [v.strip() for v in (row["询问触发词"] or "").split("、") if v.strip()],
                    "patient_known": row["患者知晓"]} for original, row in zip(data.get("facts", []), facts)]
                updated["exams"] = [{**original, "standard_name": row["检查名称"], "result": row["结果内容"] or None,
                    "return_after_minutes": row["回报等待（分钟）"], "configured": row["已配置结果"]}
                    for original, row in zip(data.get("exams", []), exams)]
            if not case_id.strip() or not title.strip():
                st.error("请填写病例编号和标题。")
            else:
                # The validation endpoint requires an identifier even before allocation on save.
                check = api("POST", "/api/teacher/cases/validate", json={"public_id": "CASE-T-DRAFT", **updated})
                if check is not None:
                    st.session_state.teacher_validation = check
                    if save:
                        result = api("PUT", f"/api/teacher/cases/{quote(case_id.strip(), safe='')}", json=updated)
                        if result:
                            raw = api("GET", f"/api/teacher/cases/{quote(result['case_id'], safe='')}/versions/{result['version']}")
                            if raw:
                                _load_editor(raw)
                                st.session_state.teacher_saved = f"已保存草稿 v{result['version']}，尚未通过医学审核。"
                                st.rerun()
    if st.session_state.get("teacher_saved"):
        st.success(st.session_state.pop("teacher_saved"))
    if st.session_state.get("teacher_validation") is not None:
        _show_issues(st.session_state.teacher_validation)

    st.subheader("审核与发布")
    # Only a persisted version can transition; use the library metadata rather than editor fields.
    current = next((x for x in cases if x["case_id"] == data.get("case_id") and x["version"] == data.get("version")), None)
    if not current or current["source"] != "teacher":
        st.info("请先保存为教师病例草稿，再进行校验和送审。")
        return
    status = current["review_status"]
    st.write(f"当前版本：v{current['version']} · {STATUS.get(status, status)}")
    base = f"/api/teacher/cases/{quote(current['case_id'], safe='')}/versions/{current['version']}"
    if status == "draft" and st.button("标记结构校验通过"):
        if api("POST", base + "/validate"):
            st.rerun()
    if status == "validated" and st.button("提交医学审核", type="primary"):
        if api("POST", base + "/submit-review"):
            st.rerun()
    if status in {"validated", "under_review"} and st.button("撤回为草稿"):
        if api("POST", base + "/withdraw"):
            st.rerun()
    if status == "under_review":
        st.warning("仅在真实医学审核完成后填写。结构校验或程序测试不属于医学审核。")
        with st.form("teacher_publish"):
            reviewer = st.text_input("真实医学审核人", value="")
            reviewed = st.date_input("医学审核日期", value=None, max_value=date.today())
            confirmed = st.checkbox("我确认该版本已由上述人员完成真实医学审核")
            publish = st.form_submit_button("确认审核并发布", type="primary")
        if publish:
            if not confirmed or not reviewer.strip() or reviewed is None:
                st.error("请完整填写真实审核信息并明确确认。未执行发布。")
            elif api("POST", base + "/publish", json={"reviewer": reviewer.strip(), "review_date": reviewed.isoformat(),
                                                       "confirm_medical_review": True}):
                st.rerun()
    if status == "published":
        st.success("此版本已发布；需要修改时请另存为新草稿，保留历史版本。")

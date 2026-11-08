"""Teacher workspace. All access checks and lifecycle transitions remain on the API."""
import copy
import json
import uuid
from datetime import date
from urllib.parse import quote

import streamlit as st
from score_review import render_score_review

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

    task = st.radio("教师任务", ["病例编写与发布", "训练反馈复核", "评分复核与裁定"], horizontal=True, key="teacher_task")
    if task == "训练反馈复核":
        render_feedback_review(api)
        return
    if task == "评分复核与裁定":
        render_score_review(api)
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
            coverage = api("GET", path + "/teaching-basis")
            if coverage:
                if coverage["missing_clinical_stages"]:
                    st.warning("该病例缺少专科临床依据：" + "、".join(coverage["missing_clinical_stages"]))
                st.caption(coverage["notice"])
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
            st.caption("检查须显式开单；当前不支持检查前置表达式或评分阶段截止配置。披露级别与主动回答标记仅作编写备注，不会绕过逐问披露。")
            st.caption("即时演进的触发名须对应某项处置的trigger；延误规则须指定已配置检查。取消条件中的治疗ID或trigger只匹配实际执行，显式动作名可匹配计划等事件。")
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


def render_feedback_review(api):
    st.subheader("训练反馈复核")
    st.caption("按学生提供的账号与训练编号载入已封存训练。修订形成独立版本，保留原始记录。")
    st.warning("反馈复核只修改评语与引用，不调整封存分数，不解除评分待复核状态，也不代表医学审核。")
    with st.form("feedback_review_lookup"):
        owner_id = st.text_input("学生账号", key="feedback_review_owner")
        session_id = st.text_input("训练编号", key="feedback_review_session")
        load = st.form_submit_button("载入或刷新训练反馈")
    if load:
        for key in list(st.session_state):
            if key.startswith("review_editor_"):
                del st.session_state[key]
        st.session_state.pop("feedback_review_record", None)
        st.session_state.pop("feedback_review_pending", None)
        if not owner_id.strip() or not session_id.strip():
            st.error("请填写学生账号和训练编号。")
        else:
            record = api("GET", f"/api/teacher/feedback/{quote(session_id.strip(), safe='')}",
                         params={"owner_id": owner_id.strip()})
            if record:
                st.session_state.feedback_review_record = record
    record = st.session_state.get("feedback_review_record")
    if not record:
        return
    st.write(f"{record['case']['title']} · v{record['case']['version']} · 反馈复核第{record['current_revision']}版")
    st.caption(f"当前载入：{record['owner_id']} · {record['session_id']}")
    if record.get("basis_provenance") == "legacy_unversioned":
        st.warning("此历史反馈未留存依据版本。下方依据用于本次复核；保存后形成新的引用快照，不补写原始记录。")
    if record.get("basis_coverage", {}).get("missing_clinical_stages"):
        st.warning("缺少适用专科临床依据：" + "、".join(record["basis_coverage"]["missing_clinical_stages"]) + "。通用反馈规范不能替代。")
    if record["validation_error"]:
        st.error("当前反馈未通过学生展示校验：" + record["validation_error"])
    if record["original_validation_error"]:
        st.caption("原始反馈校验问题：" + record["original_validation_error"])
    with st.expander("封存评分、学生提交与事件证据", expanded=True):
        st.json({key: record[key] for key in ("score_snapshot", "submissions", "case_notes", "events")})
    with st.expander("原始反馈与历次复核（只读）"):
        st.json({"original": record["original"], "reviews": record["reviews"]})
    with st.expander("适用教学依据与来源（未医学审核）"):
        for entry in record["teaching_basis"]:
            st.write(f"{entry['basis_id']} · {entry['title']}")
            st.caption(f"依据修订 {entry['revision']} · 适用当前病例版本")
            st.caption("适用环节：" + "、".join(entry["stages"]))
            st.write(entry["text"])
            for source in entry["sources"]:
                st.caption("来源：" + source)

    stages = ("病史采集", "检查选择", "鉴别诊断", "诊断", "治疗")
    editable = record["editable_items"] if isinstance(record["editable_items"], list) else []
    existing = {item.get("stage"): item for item in editable
                if isinstance(item, dict) and isinstance(item.get("stage"), str)}
    event_ids = [event["event_id"] for event in record["events"]]
    event_labels = {event["event_id"]: f"#{event['seq']} · {event['action']} · {event['event_id']}"
                    for event in record["events"]}
    basis_labels = {entry["basis_id"]: f"{entry['title']}（{entry['basis_id']}）" for entry in record["teaching_basis"]}
    items = []
    with st.form("feedback_review_edit"):
        st.markdown("**逐环节复核**")
        st.caption("五环节均须填写评语、建议及适用依据。原始无效引用不会自动保留，请对照只读记录核实。")
        for stage in stages:
            old = existing.get(stage, {})
            basis_ids = [entry["basis_id"] for entry in record["teaching_basis"] if stage in entry["stages"]]
            with st.expander(stage, expanded=True):
                comment = st.text_area("复核评语", value=old.get("comment", "") if isinstance(old.get("comment", ""), str) else "",
                    max_chars=600, key=f"review_editor_{stage}_comment")
                improvement = st.text_area("改进建议", value=old.get("improvement", "") if isinstance(old.get("improvement", ""), str) else "",
                    max_chars=300, key=f"review_editor_{stage}_improvement")
                evidence = st.multiselect("事件引用", event_ids,
                    default=[v for v in old.get("evidence_event_ids", []) if v in event_ids] if isinstance(old.get("evidence_event_ids", []), list) else [],
                    format_func=lambda value: event_labels[value], key=f"review_editor_{stage}_events")
                basis = st.multiselect("教学依据", basis_ids,
                    default=[v for v in old.get("basis_ids", []) if v in basis_ids] if isinstance(old.get("basis_ids", []), list) else [],
                    format_func=lambda value: basis_labels[value], key=f"review_editor_{stage}_basis")
                items.append({"stage": stage, "comment": comment, "improvement": improvement,
                              "evidence_event_ids": evidence, "basis_ids": basis})
        reason = st.text_area("复核原因与修改说明（仅教师可见）", max_chars=1000, key="review_editor_reason")
        save = st.form_submit_button("保存反馈复核版本", type="primary")
    if save:
        payload = {"owner_id": record["owner_id"], "expected_revision": record["current_revision"],
                   "reason": reason, "items": items,
                   "basis_hashes": {entry["basis_id"]: entry["content_hash"] for entry in record["teaching_basis"]
                                    if any(entry["basis_id"] in item["basis_ids"] for item in items)}}
        pending = st.session_state.get("feedback_review_pending")
        if not pending or pending["payload"] != payload:
            pending = {"payload": payload, "request_id": str(uuid.uuid4())}
            st.session_state.feedback_review_pending = pending
        result = api("POST", f"/api/teacher/feedback/{quote(record['session_id'], safe='')}",
                     json={**payload, "request_id": pending["request_id"]})
        if result:
            st.session_state.pop("feedback_review_record", None)
            st.session_state.pop("feedback_review_pending", None)
            st.success(f"已保存反馈复核第{result['revision']}版。原记录与封存分数未改变；继续修订前请重新载入。")

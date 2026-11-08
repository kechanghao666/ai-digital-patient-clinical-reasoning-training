import uuid
import json
import sys
from pathlib import Path

import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from teacher import render_teacher

st.set_page_config(page_title="AI数字病人临床思维训练", page_icon=":material/clinical_notes:", layout="wide")
st.html("<style>" + Path(__file__).with_name("styles.css").read_text("utf-8") + "</style>")
st.sidebar.markdown("### 临床思维训练")
WORKSPACE = st.sidebar.radio("工作空间", ["学生训练", "教师病例管理"], key="workspace")
OWNER = st.sidebar.text_input("教师演示账号" if WORKSPACE == "教师病例管理" else "学生演示账号",
    "teacher-demo" if WORKSPACE == "教师病例管理" else "demo-student",
    key="teacher_identity" if WORKSPACE == "教师病例管理" else "student_identity")
with st.sidebar.expander("连接设置"):
    API = st.text_input("后端地址", "http://127.0.0.1:8000", key="api_address").rstrip("/")
    st.caption("演示账号仅供本地开发。公开部署前须接入正式登录与角色权限。")
# Cache contents are scoped to the actual destination and identity, never just the selected role.
context = (API, OWNER, WORKSPACE)
if st.session_state.get("identity_context") != context:
    for key in list(st.session_state):
        if key not in {"workspace", "student_identity", "teacher_identity", "api_address"}:
            del st.session_state[key]
    st.session_state.identity_context = context
st.title("AI数字病人临床思维训练")
st.html('<div class="clinic-banner"><p class="clinic-eyebrow">CLINICAL REASONING · 医学教学</p>'
        '<strong>在可重复的病例中，练习每一步临床判断</strong>'
        '<p>从问诊与检查，到诊断、模拟处置和复盘。关注依据，也关注过程。</p></div>')
st.warning("教学模拟环境，非真实患者，不用于真实诊疗。病例与教学依据尚未通过医学审核。", icon=":material/info:")
error_slot = st.empty()


def headers():
    return {"X-Owner-Id": OWNER}


def _pending_mutation(scope, payload):
    pending = st.session_state.setdefault("pending_mutations", {})
    payload_key = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    current = pending.get(scope)
    if current and current["payload_key"] == payload_key:
        return current["request_id"]
    request_id = str(uuid.uuid4())
    pending[scope] = {"request_id": request_id, "payload_key": payload_key}
    return request_id


def _clear_mutation(scope, request_id):
    pending = st.session_state.get("pending_mutations", {})
    if pending.get(scope, {}).get("request_id") == request_id:
        pending.pop(scope, None)


def mutate(method, path, scope, payload, **kwargs):
    request_id = _pending_mutation(scope, payload)
    body = {"request_id": request_id, **payload}
    result = api(method, path, json=body, **kwargs)
    if result is not None:
        _clear_mutation(scope, request_id)
    return result


def action_mutation(session_id, action, payload):
    scope = f"action:{action}"
    request_id = _pending_mutation(scope, payload)
    result = api(
        "POST",
        f"/api/sessions/{session_id}/action",
        json={"request_id": request_id, "action": action, "payload": payload},
    )
    if result is not None:
        _clear_mutation(scope, request_id)
    return result


def api(method, path, *, allow_missing=False, **kwargs):
    def error_scope(request_path):
        if request_path.endswith("/action"):
            return "action"
        if "/sessions/" in request_path and request_path.endswith("/score"):
            return "score"
        if request_path.endswith("/ask"):
            return "ask"
        if request_path.endswith("/diagnosis"):
            return "diagnosis"
        if request_path.endswith("/differential"):
            return "differential"
        if request_path.endswith("/treatment"):
            return "treatment"
        if request_path.endswith("/seal"):
            return "seal"
        if request_path.endswith("/feedback"):
            return "feedback"
        if request_path.startswith("/api/sessions/"):
            return "session_read"
        return "general"

    scope = error_scope(path)
    try:
        response = requests.request(method, f"{API}{path}", headers=headers(), timeout=(5, 65), **kwargs)
    except requests.RequestException:
        st.session_state.last_error = "连接未完成，请检查后端服务或稍后重试。未确认成功的提交会保留原请求编号。"
        st.session_state.last_error_status = None
        st.session_state.last_error_scope = scope
        return None
    if response.status_code >= 400:
        if response.status_code == 404 and allow_missing:
            return None
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        if isinstance(detail, dict) and "issues" in detail:
            detail = "；".join(x.get("message", "病例需要复核") for x in detail["issues"])
        st.session_state.last_error = f"请求未完成（HTTP {response.status_code}）：{detail}"
        st.session_state.last_error_status = response.status_code
        st.session_state.last_error_scope = scope
        return None
    try:
        result = response.json()
    except ValueError:
        st.session_state.last_error = "后端返回了无法识别的响应，未将其当作成功处理。"
        st.session_state.last_error_status = response.status_code
        st.session_state.last_error_scope = scope
        return None
    if st.session_state.get("last_error_scope") == scope:
        st.session_state.last_error = None
        st.session_state.last_error_status = None
        st.session_state.last_error_scope = None
    return result


if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "session_snapshot" not in st.session_state:
    st.session_state.session_snapshot = None


def render_pending_error():
    error_slot.empty()
    if st.session_state.get("last_error"):
        error_slot.error(st.session_state.last_error)


def clear_training_fields():
    for key in list(st.session_state):
        if key in {"diagnosis_input", "differential_choice", "case_notes_input", "question", "treatment_confirmation",
                   "treatment_choice", "reset_treatment_confirmation", "pending_return"} or key.startswith("ddx_"):
            del st.session_state[key]
    st.session_state.pending_mutations = {}


def return_to_library():
    st.session_state.session_id = None
    st.session_state.session_snapshot = None
    clear_training_fields()
    st.rerun()


def reset_treatment_confirmation():
    st.session_state.treatment_confirmation = False


def evidence_label(event):
    if event["action"] == "ask":
        return f"#{event['seq']} 问诊：{event['intent']}"
    exams = event["result"].get("exam_results", [])
    if exams:
        return f"#{event['seq']} 检查回报：" + "、".join(exam["name"] for exam in exams)
    return f"#{event['seq']} 病情变化"


if WORKSPACE == "教师病例管理":
    render_teacher(api)
    render_pending_error()
    st.stop()

if st.session_state.session_id and st.sidebar.button("返回病例库", width="stretch"):
    saved = ((st.session_state.session_snapshot or {}).get("state") or {}).get("case_notes", "")
    if st.session_state.get("case_notes_input", saved) != saved:
        st.session_state.pending_return = True
    else:
        return_to_library()

if st.session_state.get("pending_return"):
    st.sidebar.warning("病历有未保存修改，请选择如何返回。")
    if st.sidebar.button("保存病历并返回", type="primary", width="stretch"):
        if action_mutation(st.session_state.session_id, "update_notes", {"notes": st.session_state.case_notes_input}):
            return_to_library()
    if st.sidebar.button("放弃未保存内容并返回", width="stretch"):
        return_to_library()
    if st.sidebar.button("继续编辑", width="stretch"):
        st.session_state.pending_return = False
        st.rerun()

cases = api("GET", "/api/cases") or []
if not st.session_state.session_id:
    existing_sessions = api("GET", "/api/sessions") or []
    a, b, c = st.columns(3)
    a.metric("可选专科", len({case["specialty"] for case in cases}))
    b.metric("可选病例", len(cases))
    c.metric("我的训练", len(existing_sessions))
    st.subheader("选择病例")
    st.caption("选择病例开始训练，或恢复已有记录。所有分数均用于教学练习，不能作为正式成绩。")
    if existing_sessions:
        session_labels = [
            (
                f"{item['summary_id']} · {item['case']['public_id']} · "
                f"{'已封存' if item['sealed'] else '进行中'} · "
                f"{item['sim_time_minutes']} 分钟 · {item['event_count']} 个事件",
                item["session_id"],
            )
            for item in existing_sessions
        ]
        selected_session = st.selectbox(
            "恢复已有训练",
            session_labels,
            format_func=lambda value: value[0],
        )
        if st.button("恢复训练"):
            st.session_state.session_id = selected_session[1]
            st.session_state.session_snapshot = None
            clear_training_fields()
            st.rerun()
    if cases:
        labels = {f"{case['public_id']} · {case['title']}": case for case in cases}
        selected = st.selectbox("病例", list(labels))
        selected_case = labels[selected]
        with st.container(border=True):
            st.markdown(f"**{selected_case['specialty']} · {selected_case['title']}**")
            st.write(selected_case.get("chief_complaint", ""))
            st.caption("工程教学病例 · 未经医学审核" if selected_case.get("review_status") != "published" else "已发布教学病例")
        if st.button("开始训练", type="primary"):
            result = api("POST", f"/api/sessions?case_id={labels[selected]['public_id']}")
            if result:
                st.session_state.session_id = result["session_id"]
                st.session_state.session_snapshot = result
                clear_training_fields()
                st.rerun()
    else:
        st.info("暂无可用病例。")
else:
    session = api("GET", f"/api/sessions/{st.session_state.session_id}")
    stale = session is None
    if not session:
        if st.session_state.get("last_error_status") in {401, 404}:
            st.session_state.session_id = None
            st.session_state.session_snapshot = None
            st.rerun()
        session = st.session_state.session_snapshot
        if not session:
            st.warning("当前训练暂时无法读取，请稍后重试。")
            render_pending_error()
            st.stop()
    else:
        st.session_state.session_snapshot = session
    state = session["state"]
    case = session["case"]
    options = session.get("options") or {}
    exam_names = {item["exam_id"]: item["name"] for item in options.get("exams", [])}
    treatment_names = {item["treatment_id"]: item["name"] for item in options.get("treatments", [])}
    differential_labels = {item["candidate_id"]: item["label"] for item in options.get("differentials", [])}
    readonly = state["sealed"] or stale
    if stale:
        st.warning("当前展示上次读取的记录，连接恢复前暂时停用提交。")
    st.caption(f"{case['public_id']} · {case['title']} · 模拟时间 {state['sim_time_minutes']} 分钟")
    a, b, c = st.columns(3)
    a.metric("模拟时间", f"{state['sim_time_minutes']} 分钟")
    b.metric("已记录操作", len(state.get("events", [])))
    c.metric("训练状态", "已封存" if state["sealed"] else "进行中")
    st.caption("01 病史采集　→　02 检查选择　→　03 鉴别诊断　→　04 诊断　→　05 模拟治疗与复盘")
    left, right = st.columns(2)
    with left:
        st.subheader("问诊")
        for event in state.get("events", []):
            if event["action"] == "ask":
                with st.chat_message("user"):
                    st.write(event["intent"])
                with st.chat_message("assistant", avatar=":material/person:"):
                    st.write(event.get("result", {}).get("answer", ""))
        question = st.text_input("向患者提问", key="question", disabled=readonly)
        if st.button("提交问题", disabled=readonly) and question:
            result = mutate(
                "POST",
                f"/api/sessions/{state['session_id']}/ask",
                "ask",
                {"question": question},
            )
            if result:
                st.rerun()
        st.subheader("检查开单")
        exam_id = st.selectbox(
            "检查项目",
            list(exam_names),
            format_func=lambda value: exam_names.get(value, value),
            disabled=readonly or not exam_names,
        )
        if st.button("确认开单", disabled=readonly or not exam_names):
            result = action_mutation(state["session_id"], "order_exam", {"exam_id": exam_id})
            if result:
                st.rerun()
        wait_minutes = st.number_input("显式等待模拟分钟", min_value=0, max_value=240, value=5, disabled=readonly)
        if st.button("推进模拟时间", disabled=readonly):
            result = action_mutation(state["session_id"], "wait", {"minutes": wait_minutes})
            if result:
                st.rerun()
    with right:
        st.subheader("检查结果")
        if not state["orders"]:
            st.info("尚未开立检查。开单后，按回报时间推进模拟时钟。")
        order_labels = {"pending": "等待回报", "ready": "已回报", "failed": "检查失败", "not_configured": "未配置结果", "not_ordered": "尚未开立"}
        for order in state["orders"]:
            st.write(
                f"{order['exam_name']}：{order_labels.get(order['state'], order['state'])} "
                f"（采样 {order['sample_sim_time']} 分钟，回报 {order['return_sim_time']} 分钟）"
            )
            if order["result"]:
                st.success(order["result"])
        for update in state.get("clinical_updates", []):
            st.warning(f"病情变化：{update}")
        st.subheader("诊断与治疗")
        notes = st.text_area(
            "病历记录",
            value=state.get("case_notes", ""),
            key="case_notes_input",
            disabled=readonly,
            help="记录本轮采集到的主诉、现病史、检查结果和处理计划。",
        )
        if st.button("保存病历记录", disabled=readonly):
            if action_mutation(state["session_id"], "update_notes", {"notes": notes}):
                st.rerun()
        if not readonly and notes != state.get("case_notes", ""):
            st.caption("病历有未保存修改，返回病例库前会提醒保存。")
        submissions = state.get("submissions", {})
        restored = {c.get("candidate_id"): c for c in (submissions.get("differential") or [])}
        evidence_events = {
            event["event_id"]: evidence_label(event)
            for event in state.get("events", [])
            if event["action"] == "ask" or event["result"].get("exam_results") or event["result"].get("clinical_updates")
        }
        st.markdown("**鉴别诊断**（先选候选，再填支持/反对证据并选择依据事件）")
        chosen = st.multiselect(
            "候选诊断",
            list(differential_labels),
            default=[value for value in restored if value in differential_labels],
            format_func=lambda value: differential_labels.get(value, value),
            key="differential_choice",
            disabled=readonly or not differential_labels,
        )
        drafts = []
        for candidate_id in chosen:
            previous = restored.get(candidate_id, {})
            label = differential_labels.get(candidate_id, candidate_id)
            support = st.text_input(
                f"{label}：支持证据",
                value="、".join(previous.get("supporting_evidence", [])),
                key=f"ddx_support_{candidate_id}",
                disabled=readonly,
            )
            oppose = st.text_input(
                f"{label}：反对证据",
                value="、".join(previous.get("opposing_evidence", [])),
                key=f"ddx_oppose_{candidate_id}",
                disabled=readonly,
            )
            cited = st.multiselect(
                f"{label}：依据事件",
                list(evidence_events),
                default=[v for v in previous.get("evidence_event_ids", []) if v in evidence_events],
                format_func=lambda value: evidence_events.get(value, value),
                key=f"ddx_events_{candidate_id}",
                disabled=readonly,
            )
            drafts.append(
                {
                    "candidate_id": candidate_id,
                    "supporting_evidence": [support.strip()] if support.strip() else [],
                    "opposing_evidence": [oppose.strip()] if oppose.strip() else [],
                    "evidence_event_ids": cited,
                }
            )
        if st.button("提交鉴别诊断", disabled=readonly or not drafts):
            candidates = drafts
            if candidates:
                mutate(
                    "POST",
                    f"/api/sessions/{state['session_id']}/differential",
                    "differential",
                    {"candidates": candidates},
                )
        diagnosis = st.text_input(
            "诊断提交",
            value=submissions.get("diagnosis") or "",
            key="diagnosis_input",
            disabled=readonly,
        )
        if st.button("提交诊断", disabled=readonly) and diagnosis:
            mutate(
                "POST",
                f"/api/sessions/{state['session_id']}/diagnosis",
                "diagnosis",
                {"diagnosis": diagnosis},
            )
        treatment_id = st.selectbox(
            "治疗动作",
            list(treatment_names),
            format_func=lambda value: treatment_names.get(value, value),
            disabled=readonly or not treatment_names,
            key="treatment_choice",
            on_change=reset_treatment_confirmation,
        )
        if st.session_state.pop("reset_treatment_confirmation", False):
            reset_treatment_confirmation()
        confirm = st.checkbox("确认执行该治疗动作", key="treatment_confirmation", disabled=readonly)
        restored_treatment = submissions.get("treatment") or {}
        if restored_treatment:
            st.caption(
                f"已提交治疗：{restored_treatment.get('treatment_name') or restored_treatment.get('treatment_id', '')} "
                f"（{'已执行' if restored_treatment.get('confirm_execution') else '仅计划'}）"
            )
        if st.button("提交治疗", disabled=readonly or not confirm or not treatment_names):
            result = mutate(
                "POST",
                f"/api/sessions/{state['session_id']}/treatment",
                "treatment",
                {"treatment_id": treatment_id, "confirm_execution": True},
            )
            if result:
                st.session_state.reset_treatment_confirmation = True
                st.rerun()
        if not state["sealed"]:
            st.caption("封存后不能修改本次训练，请先核对病历、诊断和实际执行的处置。")
        if not state["sealed"] and st.button("提交训练并封存", type="primary", disabled=readonly):
            result = mutate(
                "POST",
                f"/api/sessions/{state['session_id']}/seal",
                "seal",
                {},
            )
            if result:
                st.success("训练已封存，规则评分已生成；可继续生成分环节反馈。")
                st.rerun()
    if state["sealed"]:
        st.info("本训练已封存，当前页面为只读反馈。")
        st.divider()
        st.subheader("训练反馈")
        with st.expander("向教师提供复核信息"):
            st.caption("反馈需要复核时，将以下账号和完整训练编号提供给教师。")
            st.code(f"学生账号：{OWNER}\n训练编号：{state['session_id']}", language=None)
        snapshot = api("GET", f"/api/sessions/{state['session_id']}/score")
        if snapshot:
            st.caption("以下为封存时生成的原规则评分；教师裁定在下方独立展示。")
            status_labels = {
                "complete": "已完成自动评分",
                "needs_review": "待人工复核（包含暂计分，不作为最终成绩）",
                "failed": "评分失败",
            }
            status = snapshot.get("status", "complete")
            if status == "needs_review":
                st.warning(status_labels[status])
            elif status == "failed":
                st.error(status_labels[status])
            else:
                st.success(status_labels.get(status, status))
            st.metric("原规则总分", f"{snapshot['total_score']} / {snapshot['max_score']}")
            stage_scores = {}
            for item in snapshot["items"]:
                totals = stage_scores.setdefault(item["stage"], [0, 0])
                totals[0] += item["score"]
                totals[1] += item["max_score"]
            for stage, (got, total) in stage_scores.items():
                st.progress(got / total if total else 0, text=f"{stage} · {got:g} / {total:g}")
            st.dataframe(snapshot["items"], width="stretch", hide_index=True,
                column_config={"stage": "环节", "score": "得分", "max_score": "满分", "reason": "评分说明",
                               "improvement": "改进建议", "item_id": None, "evidence_event_ids": None, "hit": None})
        feedback_cache = st.session_state.setdefault("feedback_by_session", {})
        adjudicated = api("GET", f"/api/sessions/{state['session_id']}/score-review", allow_missing=True) if not stale else None
        if adjudicated:
            reviewed = adjudicated["reviewed_score"]
            st.info(f"教师评分裁定第{adjudicated['revision']}版：{reviewed['total_score']:g} / {reviewed['max_score']:g}。原规则评分保留。")
            st.caption(adjudicated["notice"])
            st.dataframe(reviewed["items"], width="stretch", hide_index=True,
                column_config={"stage": "环节", "score": "裁定得分", "max_score": "满分", "reason": "教师裁定说明",
                               "improvement": "改进建议", "item_id": None, "evidence_event_ids": None, "hit": None})
        if st.button("刷新已存反馈与教师复核", disabled=stale):
            feedback_cache.pop(state["session_id"], None)
        if state["session_id"] not in feedback_cache and not stale:
            stored_feedback = api("GET", f"/api/sessions/{state['session_id']}/feedback", allow_missing=True)
            if stored_feedback:
                feedback_cache[state["session_id"]] = stored_feedback
        if st.button("生成分环节改进反馈", disabled=stale):
            generated = mutate(
                "POST", f"/api/sessions/{state['session_id']}/feedback", "feedback", {}
            )
            if generated:
                feedback_cache[state["session_id"]] = generated
        shown = feedback_cache.get(state["session_id"])
        if shown:
            st.caption(shown.get("notice", ""))
            if shown.get("basis_coverage", {}).get("missing_clinical_stages"):
                st.warning("以下环节尚缺专科临床依据：" + "、".join(shown["basis_coverage"]["missing_clinical_stages"])
                           + "。通用反馈规范只用于组织评语，请教师核对。")
            if shown.get("review_revision"):
                st.info(f"当前展示教师反馈复核第{shown['review_revision']}版；封存分数与评分复核状态保持不变。")
            basis = {entry["basis_id"]: entry for entry in shown.get("teaching_basis", [])}
            for item in shown["items"]:
                st.markdown(f"**{item['stage']}**：{item['comment']}")
                if item.get("improvement"):
                    st.markdown(f"- 改进建议：{item['improvement']}")
                with st.expander(f"{item['stage']} · 教学依据与来源（未审核）"):
                    for basis_id in item.get("basis_ids", []):
                        entry = basis.get(basis_id)
                        if entry:
                            st.write(entry["title"])
                            for source in entry["sources"]:
                                st.caption("来源：" + source)
                            if entry.get("restricted"):
                                st.caption("部分题名或来源含参考答案，供教师核对，未向学生展示。")
                        else:
                            st.caption("当前记录未提供可展示的来源，请教师核对。")
        if st.button("开始新训练"):
            return_to_library()
    with st.expander("训练回放"):
        action_labels = {"ask": "问诊", "order_exam": "检查开单", "wait": "等待回报", "update_notes": "保存病历",
            "submit_differential": "提交鉴别诊断", "submit_diagnosis": "提交诊断", "plan_treatment": "计划处置",
            "execute_treatment": "执行模拟处置", "seal": "封存训练"}
        for event in state.get("events", []):
            st.write(
                f"#{event['seq']} · {action_labels.get(event['action'], event['action'])} · 模拟时间 {event['sim_time']} 分钟"
            )
            result = event.get("result") or {}
            if event["intent"]:
                st.write("提问：" + event["intent"])
            if result.get("answer"):
                st.write("患者回答：" + result["answer"])
            if "advanced_minutes" in result:
                st.write(f"推进模拟时间 {result['advanced_minutes']} 分钟")
            if result.get("state"):
                st.write("检查状态：" + order_labels.get(result["state"], result["state"]))
            for exam in result.get("exam_results", []):
                st.write(f"{exam['name']}：{exam['result']}")
            if "diagnosis" in result:
                st.write("提交诊断：" + result["diagnosis"])
            for candidate in result.get("candidates", []):
                st.write("鉴别候选：" + differential_labels.get(candidate["candidate_id"], "历史候选"))
                st.write("支持证据：" + ("；".join(candidate["supporting_evidence"]) or "未填写"))
                st.write("反对证据：" + ("；".join(candidate["opposing_evidence"]) or "未填写"))
            if result.get("treatment"):
                treatment = result["treatment"]
                st.write("模拟处置：" + treatment_names.get(treatment.get("treatment_id"), "历史处置")
                         + ("（已执行）" if result.get("executed") else "（仅计划）"))
            for update in result.get("clinical_updates", []):
                st.write("病情变化：" + update)

render_pending_error()

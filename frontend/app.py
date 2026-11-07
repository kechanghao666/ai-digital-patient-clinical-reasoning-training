import uuid
import json

import requests
import streamlit as st


st.set_page_config(page_title="AI数字病人临床思维训练", layout="wide")
API = st.sidebar.text_input("后端地址", "http://127.0.0.1:8000")
OWNER = st.sidebar.text_input("业务学生ID", "demo-student")
st.title("AI数字病人临床思维训练")
st.warning("教学模拟环境，非真实患者，不用于真实诊疗。")
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


def api(method, path, **kwargs):
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
        response = requests.request(method, f"{API}{path}", headers=headers(), timeout=30, **kwargs)
    except requests.RequestException as exc:
        st.session_state.last_error = f"后端暂不可用，请检查服务地址或稍后重试：{exc}"
        st.session_state.last_error_status = None
        st.session_state.last_error_scope = scope
        return None
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
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


cases = api("GET", "/api/cases") or []
if not st.session_state.session_id:
    existing_sessions = api("GET", "/api/sessions") or []
    st.subheader("选择病例")
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
            for key in ("diagnosis_input", "differential_choice", "case_notes_input"):
                st.session_state.pop(key, None)
            st.rerun()
    if cases:
        labels = {f"{case['public_id']} · {case['title']}": case for case in cases}
        selected = st.selectbox("病例", list(labels))
        if st.button("开始训练"):
            result = api("POST", f"/api/sessions?case_id={labels[selected]['public_id']}")
            if result:
                st.session_state.session_id = result["session_id"]
                st.session_state.session_snapshot = result
                for key in ("diagnosis_input", "differential_choice", "case_notes_input"):
                    st.session_state.pop(key, None)
                st.rerun()
    else:
        st.info("暂无可用病例。")
else:
    session = api("GET", f"/api/sessions/{st.session_state.session_id}")
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
    readonly = state["sealed"]
    st.caption(f"{case['public_id']} · {case['title']} · 模拟时间 {state['sim_time_minutes']} 分钟")
    left, right = st.columns(2)
    with left:
        st.subheader("问诊")
        question = st.text_input("向患者提问", key="question", disabled=readonly)
        if st.button("提交问题", disabled=readonly) and question:
            result = mutate(
                "POST",
                f"/api/sessions/{state['session_id']}/ask",
                "ask",
                {"question": question},
            )
            if result:
                st.info(result["answer"])
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
        for order in state["orders"]:
            st.write(
                f"{order['exam_name']}：{order['state']} "
                f"(采样 {order['sample_sim_time']}，回报 {order['return_sim_time']})"
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
            action_mutation(state["session_id"], "update_notes", {"notes": notes})
        submissions = state.get("submissions", {})
        restored = {c.get("candidate_id"): c for c in (submissions.get("differential") or [])}
        evidence_events = {
            event["event_id"]: (
                f"#{event['seq']} 问诊：{event['intent']}"
                if event["action"] == "ask"
                else f"#{event['seq']} 等待 {event['result'].get('advanced_minutes', 0)} 分钟后的检查结果"
            )
            for event in state.get("events", [])
            if event["action"] in {"ask", "wait"}
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
        )
        confirm = st.checkbox("确认执行该治疗动作", disabled=readonly)
        restored_treatment = submissions.get("treatment") or {}
        if restored_treatment:
            st.caption(
                f"已提交治疗：{restored_treatment.get('treatment_name') or restored_treatment.get('treatment_id', '')} "
                f"（{'已执行' if restored_treatment.get('confirm_execution') else '仅计划'}）"
            )
        if st.button("提交治疗", disabled=readonly) and confirm:
            mutate(
                "POST",
                f"/api/sessions/{state['session_id']}/treatment",
                "treatment",
                {"treatment_id": treatment_id, "confirm_execution": True},
            )
        if not state["sealed"] and st.button("提交训练并封存"):
            result = mutate(
                "POST",
                f"/api/sessions/{state['session_id']}/seal",
                "seal",
                {},
            )
            if result:
                st.success("训练已封存，反馈已生成。")
                st.rerun()
    if state["sealed"]:
        st.info("本训练已封存，当前页面为只读反馈。")
        st.divider()
        st.subheader("训练反馈")
        snapshot = api("GET", f"/api/sessions/{state['session_id']}/score")
        if snapshot:
            status_labels = {
                "complete": "已完成自动评分",
                "needs_review": "待人工复核（当前分数不含待复核项的最终定分）",
                "failed": "评分失败",
            }
            status = snapshot.get("status", "complete")
            if status == "needs_review":
                st.warning(status_labels[status])
            elif status == "failed":
                st.error(status_labels[status])
            else:
                st.success(status_labels.get(status, status))
            st.metric("总分", f"{snapshot['total_score']} / {snapshot['max_score']}")
            st.dataframe(snapshot["items"], use_container_width=True)
        feedback_cache = st.session_state.setdefault("feedback_by_session", {})
        if st.button("生成分环节改进反馈"):
            generated = mutate(
                "POST", f"/api/sessions/{state['session_id']}/feedback", "feedback", {}
            )
            if generated:
                feedback_cache[state["session_id"]] = generated
        shown = feedback_cache.get(state["session_id"])
        if shown:
            st.caption(shown.get("notice", ""))
            for item in shown["items"]:
                st.markdown(f"**{item['stage']}**：{item['comment']}")
                if item.get("improvement"):
                    st.markdown(f"- 改进建议：{item['improvement']}")
        if st.button("开始新训练"):
            st.session_state.session_id = None
            st.session_state.session_snapshot = None
            st.session_state.pending_mutations = {}
            for key in ("diagnosis_input", "differential_choice", "case_notes_input"):
                st.session_state.pop(key, None)
            st.rerun()
    with st.expander("训练回放"):
        for event in state.get("events", []):
            st.write(
                f"#{event['seq']} · {event['action']} · 模拟时间 {event['sim_time']} 分钟"
            )
            if event.get("result"):
                st.json(event["result"])

render_pending_error()

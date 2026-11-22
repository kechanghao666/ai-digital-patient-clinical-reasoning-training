import uuid
import base64
from html import escape
import json
import time
import sys
import os
from pathlib import Path
from urllib.parse import urlparse

import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from teacher import render_teacher

st.set_page_config(page_title="AI数字病人临床思维训练", page_icon=str(Path(__file__).with_name("assets") / "app-icon.png"), layout="wide")
st.sidebar.html('<div class="brand"><span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span><div><strong>临床思维训练</strong><span>数字病人教学工作台</span></div></div>')
workspace_slot = st.sidebar.container()
navigation = st.sidebar.container()
account_slot = st.sidebar.container()
connection_slot = st.sidebar.expander("连接设置")
with connection_slot:
    API = st.text_input("后端地址", os.getenv("FRONTEND_API_URL", "http://127.0.0.1:8000"), key="api_address").rstrip("/")
assets = Path(__file__).with_name("assets")
font_css = ""
for family, filename in (("Clinic Sans", "interface-sans.ttf"), ("Clinic Serif", "display-serif.ttf")):
    encoded_font = base64.b64encode((assets / filename).read_bytes()).decode("ascii")
    font_css += f'@font-face{{font-family:"{family}";src:url(data:font/ttf;base64,{encoded_font}) format("truetype");font-weight:200 900;font-display:swap;}}'
st.html("<style>" + font_css + Path(__file__).with_name("styles.css").read_text("utf-8") + "</style>")
# Only this checked-in script is executable; no user or API content enters it.
st.html(Path(__file__).with_name("ambient.html").read_text("utf-8"), unsafe_allow_javascript=True)


def clear_identity(keep_context=False):
    retained = {"api_address", "account_screen"}
    if keep_context:
        retained.add("identity_context")
    for key in list(st.session_state):
        if key not in retained:
            del st.session_state[key]


def auth_request(method, path, **kwargs):
    # Refuse cleartext remote credentials and redirects to another destination.
    target = urlparse(API)
    if target.scheme != "https" and not (target.scheme == "http" and target.hostname in {"127.0.0.1", "localhost", "::1"}):
        st.error("账号服务须使用 HTTPS；本机演示可使用回环地址。")
        st.stop()
    try:
        return requests.request(method, API + path, timeout=(3, 10), allow_redirects=False, **kwargs)
    except requests.RequestException:
        st.error("暂时无法连接账号服务。请检查连接设置后重试。")
        st.stop()


saved_login = st.session_state.get("account_login")
if saved_login and saved_login["api"] != API:
    clear_identity()
    saved_login = None
config_response = auth_request("GET", "/api/auth/config")
legacy_demo = config_response.status_code == 404 and urlparse(API).hostname in {"127.0.0.1", "localhost", "::1"}
if legacy_demo:
    mode = "demo"
elif config_response.status_code == 200:
    mode = config_response.json().get("mode")
else:
    st.error("账号服务暂不可用，请检查后端版本和连接设置。")
    st.stop()
if mode not in {"demo", "local"}:
    st.error("无法确认登录方式，请检查账号服务。")
    st.stop()
if mode == "demo" and not saved_login and not st.session_state.get("account_screen"):
    with account_slot:
        if st.button("账号登录", key="open_account_login", width="stretch"):
            st.session_state.account_screen = True
            st.rerun()
if saved_login:
    identity_response = auth_request("GET", "/api/auth/me", headers={"Authorization": "Bearer " + saved_login["token"]})
    if identity_response.status_code == 401:
        # Keep unsaved fields hidden so the same user can resume them after signing in again.
        st.session_state.relogin_draft = {
            "context": st.session_state.get("identity_context"),
            "fields": {key: st.session_state[key] for key in st.session_state
                       if key in {"case_notes_input", "diagnosis_input", "differential_choice", "question"}
                       or key.startswith(("ddx_", "case_editor_"))},
        }
        st.session_state.pop("account_login", None)
        st.session_state.account_screen = True
        saved_login = None
        st.warning("登录已失效，请重新登录。")
    elif identity_response.status_code != 200:
        st.error("身份校验暂不可用，请稍后重试。")
        st.stop()
    else:
        saved_login["user"] = identity_response.json()

if not saved_login and (mode == "local" or st.session_state.get("account_screen")):
    st.html('<div class="login-marker' + ('' if st.session_state.get("show_account_connection") else ' login-fullscreen') + '"></div>')
    brand_column, settings_column = st.container(key="login_toolbar").columns([6.2, 1], vertical_alignment="center")
    with brand_column:
        st.html('<div class="login-brand"><span class="brand-mark" aria-hidden="true"></span><span>临床思维训练</span></div>')
    with settings_column:
        if st.button("连接设置", key="login_connection"):
            st.session_state.show_account_connection = not st.session_state.get("show_account_connection", False)
            st.rerun()
    intro, form_column = st.columns([1.25, 1], gap="large")
    with intro:
        orbit = base64.b64encode((assets / "listening-orbit.svg").read_bytes()).decode("ascii")
        st.html('<section class="login-intro"><div class="hero-art" aria-hidden="true"><img alt="" src="data:image/svg+xml;base64,' + orbit + '"></div>'
                '<div class="hero-copy"><h2>临床思维，<br>从倾听开始。</h2><p>与数字病人对话，梳理证据，形成判断。<br>在每一次问诊与复盘中，积累临床思考的经验。</p>'
                '<div class="login-journey" aria-label="训练流程"><div><strong>问诊与检查</strong><span>收集病史与检查证据</span></div>'
                '<div><strong>病历与判断</strong><span>整理病历，提交诊断</span></div><div><strong>反馈与复盘</strong><span>回看训练，梳理思路</span></div></div></div></section>')
    with form_column:
        with st.container(key="account_entry"):
            st.html('<div class="login-heading"><span>数字病人教学工作台</span><h1>欢迎回来</h1><p>登录，继续你的临床思维之旅。</p></div>')
            with st.form("account_sign_in", clear_on_submit=True):
                username = st.text_input("账号", key="login_username", placeholder="输入分配给你的账号", max_chars=64)
                password = st.text_input("密码", type="password", key="login_password", placeholder="输入密码", max_chars=128)
                submitted = st.form_submit_button("登录工作台", type="primary", width="stretch", disabled=legacy_demo)
            if legacy_demo:
                st.info("当前运行的是旧版演示服务。账号功能需在后端更新后启用。")
            elif submitted:
                if not username.strip() or not password:
                    st.error("请输入账号和密码。")
                else:
                    response = auth_request("POST", "/api/auth/login", json={"username": username, "password": password})
                    if response.status_code == 200:
                        st.session_state.account_login = {"api": API, **response.json()}
                        st.session_state.account_screen = False
                        st.rerun()
                    elif response.status_code == 429:
                        st.error("尝试次数过多，请稍后再试。")
                    else:
                        st.error("登录未完成，请核对账号和密码，或联系教学管理员。")
            st.caption("账号由教学管理员分配。需要开通或重置密码，请联系管理员。")
            if mode == "demo" and st.button("返回本机演示", key="return_demo"):
                clear_identity()
                st.session_state.account_screen = False
                st.rerun()
            st.html('<div class="login-footnote">学生独立保存训练记录，教师管理病例与复核。<br>仅用于教学模拟，不用于真实诊疗。</div>')
    st.stop()

if saved_login:
    user = saved_login["user"]
    OWNER = user["id"]
    spaces = ["学生训练", "教师病例管理"] if user["role"] == "teacher" else ["学生训练"]
    if st.session_state.get("workspace") not in spaces:
        st.session_state.workspace = spaces[0]
    with account_slot:
        st.caption(f'{user["display_name"]} · {"教师" if user["role"] == "teacher" else "学生"}')
        connection_slot.caption(f'账号编号：{OWNER}')
        if st.button("退出登录", key="logout", width="stretch"):
            response = auth_request("POST", "/api/auth/logout", headers={"Authorization": "Bearer " + saved_login["token"]})
            if response.status_code != 200:
                st.error("退出未完成，请重试。")
                st.stop()
            clear_identity()
            st.session_state.account_screen = True
            st.rerun()
else:
    spaces = ["学生训练", "教师病例管理"]
with workspace_slot:
    WORKSPACE = st.radio("工作空间", spaces, key="workspace")
if not saved_login:
    with connection_slot:
        OWNER = st.text_input("教师演示账号" if WORKSPACE == "教师病例管理" else "学生演示账号",
            "teacher-demo" if WORKSPACE == "教师病例管理" else "demo-student",
            key="teacher_identity" if WORKSPACE == "教师病例管理" else "student_identity")
        st.caption("当前为本机演示，自填身份不属于独立账号。")
# Cache contents are scoped to the destination and verified identity.
context = (API, OWNER, WORKSPACE)
relogin_draft = st.session_state.pop("relogin_draft", None)
if relogin_draft and relogin_draft["context"] == context:
    for key, value in relogin_draft["fields"].items():
        st.session_state[key] = value
if st.session_state.get("identity_context") != context:
    for key in list(st.session_state):
        if key not in {"workspace", "student_identity", "teacher_identity", "api_address", "account_login", "account_screen"}:
            del st.session_state[key]
    st.session_state.identity_context = context
st.html('<div class="teaching-notice" role="note"><strong>教学模拟</strong><span>非真实患者，不用于真实诊疗。病例与教学依据尚未通过医学审核，以医师及评审专家意见为准。</span></div>')
error_slot = st.empty()


def headers():
    return {"Authorization": "Bearer " + saved_login["token"]} if saved_login else {"X-Owner-Id": OWNER}


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
    started = time.perf_counter()
    try:
        response = requests.request(method, f"{API}{path}", headers=headers(), timeout=(5, 65), allow_redirects=False, **kwargs)
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
    if scope == "ask" and isinstance(result, dict) and isinstance(result.get("answer"), str) and result["answer"].strip():
        st.session_state.answer_timing = {
            "session_id": path.split("/")[-2], "event_id": result.get("event", {}).get("event_id"),
            "response_received_ms": int((time.perf_counter() - started) * 1000),
            "backend": result.get("timing"),
        }
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


def pending_training_edits(state):
    if state.get("sealed"):
        return []
    pending = []
    saved_notes = state.get("case_notes", "")
    if st.session_state.get("case_notes_input", saved_notes) != saved_notes:
        pending.append("病历")
    submissions = state.get("submissions", {})
    saved_diagnosis = submissions.get("diagnosis") or ""
    if st.session_state.get("diagnosis_input", saved_diagnosis).strip() != saved_diagnosis.strip():
        pending.append("诊断")
    restored = {c["candidate_id"]: c for c in (submissions.get("differential") or [])}
    chosen = st.session_state.get("differential_choice", list(restored))
    changed = set(chosen) != set(restored)
    for candidate_id in chosen:
        previous = restored.get(candidate_id, {})
        for key, field in (("support", "supporting_evidence"), ("oppose", "opposing_evidence")):
            # Compare the rendered text, preserving multi-entry evidence restored from the API.
            saved = "、".join(previous.get(field, []))
            changed |= st.session_state.get(f"ddx_{key}_{candidate_id}", saved).strip() != saved.strip()
        saved_events = previous.get("evidence_event_ids", [])
        changed |= set(st.session_state.get(f"ddx_events_{candidate_id}", saved_events)) != set(saved_events)
    if changed:
        pending.append("鉴别诊断")
    return pending


def evidence_label(event):
    if event["action"] == "ask":
        return f"#{event['seq']} 问诊：{event['intent']}"
    exams = event["result"].get("exam_results", [])
    if exams:
        return f"#{event['seq']} 检查回报：" + "、".join(exam["name"] for exam in exams)
    return f"#{event['seq']} 病情变化"


def render_event_result(event, differential_labels, treatment_names, order_labels):
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


def render_history_coverage(coverage, detailed=False):
    """OSCE-style structure progress based only on the student's own questions."""
    if not coverage:
        return
    chips = "".join(
        f'<span class="coverage-chip{" is-asked" if item["asked"] else ""}" title="{escape(item["hint"] or "已询问 " + str(item["question_count"]) + " 次")}">'
        f'{"✓ " if item["asked"] else ""}{escape(item["label"])}</span>'
        for item in coverage["domains"]
    )
    st.html(f'<div class="history-coverage" role="group" aria-label="问诊结构进度">'
            f'<span class="coverage-title">问诊结构 {coverage["covered"]}/{coverage["total"]}</span>{chips}</div>')
    missing = [item for item in coverage["domains"] if not item["asked"]]
    elements = coverage.get("hpi_elements", [])
    with st.expander("问诊结构提示" if not detailed else "问诊结构复盘", expanded=detailed):
        st.caption(coverage.get("notice", ""))
        if elements:
            st.write("现病史要素：" + "　".join(("✓" if e["asked"] else "○") + e["label"] for e in elements))
        if missing:
            for item in missing:
                st.write(f"○ {item['label']}：{item['hint']}")
        else:
            st.write("常规问诊结构已全部涉及。")


def open_training(session_id, snapshot=None):
    st.session_state.session_id = session_id
    st.session_state.session_snapshot = snapshot
    clear_training_fields()
    st.rerun()


if WORKSPACE == "教师病例管理":
    render_teacher(api, navigation)
    render_pending_error()
    st.stop()

if st.session_state.session_id and navigation.button("返回病例库", width="stretch"):
    if pending_training_edits((st.session_state.session_snapshot or {}).get("state") or {}):
        st.session_state.pending_return = True
    else:
        return_to_library()

if st.session_state.get("pending_return"):
    pending = pending_training_edits((st.session_state.session_snapshot or {}).get("state") or {})
    navigation.warning("、".join(pending) + "有未提交修改，请继续编辑并提交，或明确放弃后返回。")
    if navigation.button("保存病历并返回", type="primary", width="stretch", disabled=any(p != "病历" for p in pending)):
        if action_mutation(st.session_state.session_id, "update_notes", {"notes": st.session_state.case_notes_input}):
            return_to_library()
    if navigation.button("放弃未保存内容并返回", width="stretch"):
        return_to_library()
    if navigation.button("继续编辑", width="stretch"):
        st.session_state.pending_return = False
        st.rerun()

cases = api("GET", "/api/cases") or []
if not st.session_state.session_id:
    existing_sessions = api("GET", "/api/sessions") or []
    view = navigation.radio("学生导航", ["病例库", "我的训练"], key="student_view")
    if not saved_login:
        navigation.caption(f"当前演示账号：{OWNER}")
    st.html('<div class="workspace-path">学生工作台<span>/</span>' + escape(view) + '</div>')
    if view == "我的训练":
        st.title(view)
        st.write("接着上次的思路继续，或回看已封存的训练。")
        status_filter = st.selectbox("训练筛选", ["全部记录", "进行中", "已封存"], key="session_filter")
        visible_sessions = [item for item in existing_sessions
            if status_filter == "全部记录" or item["sealed"] == (status_filter == "已封存")]
        if not visible_sessions:
            st.info("暂无符合条件的训练。到病例库选择一个病例即可开始。")
        for item in visible_sessions:
            with st.container(border=False, key=f"history_{item['session_id']}"):
                detail, action = st.columns([4, 1], vertical_alignment="center")
                with detail:
                    st.subheader(item["case"]["title"])
                    st.caption(f"{'已封存 · 只读复盘' if item['sealed'] else '进行中'} · {item['case']['public_id']} · {item['summary_id']} · 模拟 {item['sim_time_minutes']} 分钟 · {item['event_count']} 条记录")
                if action.button("查看复盘" if item["sealed"] else "继续训练", key=f"resume_{item['session_id']}", width="stretch"):
                    open_training(item["session_id"])
    else:
        orbit = base64.b64encode((assets / "listening-orbit.svg").read_bytes()).decode("ascii")
        st.html('<section class="library-hero"><div class="hero-copy"><h2>临床思维，<br>从倾听开始。</h2>'
                '<p>与数字病人对话，在问诊与复盘之间，<br>练习每一步临床判断。</p></div>'
                '<div class="hero-art" aria-hidden="true"><img alt="" src="data:image/svg+xml;base64,' + orbit + '"></div></section>')
        st.title(view)
        st.html('<div class="library-intro"><p>选择一个病例，开始今天的练习。</p><span>'
                + f'{len(cases)} 个病例<span class="intro-separator">/</span>{len({case["specialty"] for case in cases})} 个专科'
                + f'<span class="intro-separator">/</span>{len(existing_sessions)} 次训练记录</span></div>')
        if existing_sessions:
            latest = existing_sessions[0]
            with st.container(border=False, key="resume_banner"):
                detail, action = st.columns([4, 1], vertical_alignment="center")
                with detail:
                    st.html('<div class="resume-line"><span>最近训练</span><strong>' + escape(latest["case"]["title"])
                            + '</strong><span>' + ("已封存" if latest["sealed"] else "进行中") + f' · {latest["event_count"]} 条记录</span></div>')
                if action.button("恢复训练", key="resume_latest", width="stretch"):
                    open_training(latest["session_id"])
        search, filters = st.columns([4, 1.5])
        query = search.text_input("搜索病例", placeholder="按主诉、病例名称或编号查找", key="case_search").strip().casefold()
        specialty = filters.selectbox("按专科筛选", ["全部专科", *sorted({case["specialty"] for case in cases})], key="library_specialty")
        visible_cases = [case for case in cases
            if (specialty == "全部专科" or case["specialty"] == specialty)
            and query in " ".join(str(case.get(field, "")) for field in ("title", "public_id", "chief_complaint", "specialty")).casefold()]
        if not visible_cases:
            st.info("未找到匹配病例。试试其他关键词，或将专科切换为“全部专科”。")
        with st.container(key="case_register"):
            st.html(f'<div class="register-heading"><span>可选病例 <b>{len(visible_cases)}</b></span><span>选择病例，进入训练</span></div>')
            for selected_case in visible_cases:
                with st.container(border=False, key=f"case_row_{selected_case['public_id']}"):
                    details, specialty_column, action = st.columns([5, 1.6, 1.35], gap="medium", vertical_alignment="center")
                    with details:
                        st.html('<div class="case-line"><div class="case-reference">'
                                + escape(selected_case["public_id"]) + ' <span>v' + escape(str(selected_case["version"]))
                                + '</span></div><h3>' + escape(selected_case["title"]) + '</h3><p>'
                                + escape(selected_case.get("chief_complaint", "")) + '</p></div>')
                    with specialty_column:
                        st.html('<div class="case-classification"><span>' + escape(selected_case["specialty"])
                                + '</span><small>' + ('教学草稿 · 未医学审核' if selected_case.get("review_status") != "published" else '已发布教学病例') + '</small></div>')
                    if action.button("开始训练", key=f"start_{selected_case['public_id']}", width="stretch"):
                        result = api("POST", f"/api/sessions?case_id={selected_case['public_id']}")
                        if result:
                            open_training(result["session_id"], result)
        st.caption("训练中可保存病历并随时恢复；封存后进入只读复盘。分数用于教学练习，不作为正式成绩。")

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
    navigation.caption("当前训练")
    navigation.write(case["title"])
    navigation.caption("封存后只读" if state["sealed"] else "输入会在当前训练中保留；返回前请保存修改。")
    st.html('<div class="workspace-path">学生工作台<span>/</span>' + ("训练复盘" if state["sealed"] else "训练工作台") + '</div>')
    st.title(case["title"])
    st.html(f'<div class="patient-strip"><span>{escape(case["specialty"])}</span><span>{escape(case["public_id"])} · v{escape(str(case["version"]))}</span><strong>主诉：{escape(case.get("chief_complaint", ""))}</strong></div>')
    with st.container(key="session_metrics"):
        a, b, c = st.columns(3)
        a.metric("模拟时间", f"{state['sim_time_minutes']} 分钟")
        b.metric("已记录操作", len(state.get("events", [])))
        c.metric("训练状态", "已封存" if state["sealed"] else "进行中")
    for update in state.get("clinical_updates", []):
        st.warning(f"病情变化：{update}")
    intake, reasoning, treatment, review = st.tabs(
        ["问诊与检查", "病历与判断", "模拟处置", "反馈与回放"],
        default="反馈与回放" if state["sealed"] else "问诊与检查",
        key=f"training_tabs_{state['session_id']}_{state['sealed']}",
    )
    with intake:
        left, right = st.columns([3, 2], gap="large")
        with left, st.container(border=False, key="consultation_panel"):
            st.subheader("患者对话")
            service_health = api("GET", "/health") or {}
            patient_mode = service_health.get("patient_adapter")
            if patient_mode == "simulator":
                st.caption("本地规则模拟 · 未连接大模型 · 回答来自病例已授权信息")
            elif patient_mode == "nexent":
                st.caption("Nexent 患者智能体 · 回答仅限本病例已授权的信息")
            else:
                st.caption("患者服务模式暂未确认 · 回答仅限本病例已授权的信息")
            if not any(event["action"] == "ask" for event in state.get("events", [])):
                st.html('<div class="conversation-empty"><span class="conversation-symbol" aria-hidden="true"></span><h3>先倾听，再判断。</h3><p>围绕患者的主诉开始提问。<br>每次对话，都是下一步判断的依据。</p></div>')
            for event in state.get("events", []):
                if event["action"] == "ask":
                    with st.chat_message("user"):
                        st.write(event["intent"])
                    with st.chat_message("assistant", avatar=":material/person:"):
                        st.write(event.get("result", {}).get("answer", ""))
                        timing = st.session_state.get("answer_timing", {})
                        if timing.get("session_id") == state["session_id"] and timing.get("event_id") == event["event_id"]:
                            st.caption(f"本次回答请求用时 {timing['response_received_ms'] / 1000:.2f} 秒",
                                       help="从前端发出请求到收到有效回答，不含浏览器绘制时间。")
            render_history_coverage(session.get("history_coverage"))
            with st.form("patient_question", border=False):
                question = st.text_input("向患者提问", key="question", disabled=readonly, placeholder="输入你的问题，按 Enter 或点击提交")
                ask = st.form_submit_button("提交问题", disabled=readonly, type="primary")
            if ask and question:
                with st.spinner("患者正在回答，请稍候…"):
                    result = mutate(
                        "POST", f"/api/sessions/{state['session_id']}/ask", "ask", {"question": question},
                    )
                if result:
                    st.rerun()
        with right, st.container(border=False, key="examination_panel"):
            st.subheader("检查与回报")
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
                    with st.container(border=True):
                        st.write(order["result"])
    with reasoning:
        notes_column, judgment_column = st.columns([1, 1], gap="large")
        with notes_column, st.container(border=False, key="notes_panel"):
            st.subheader("病历记录")
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
        with judgment_column, st.container(border=False, key="judgment_panel"):
            st.subheader("鉴别与诊断")
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
                placeholder="选择候选诊断",
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
                    placeholder="选择已记录的依据事件",
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
            if st.button("提交鉴别诊断", disabled=readonly or (not drafts and not restored)):
                if mutate(
                    "POST",
                    f"/api/sessions/{state['session_id']}/differential",
                    "differential",
                    {"candidates": drafts},
                ):
                    st.rerun()
            diagnosis = st.text_input(
                "诊断提交",
                value=submissions.get("diagnosis") or "",
                key="diagnosis_input",
                disabled=readonly,
            )
            if st.button("提交诊断", disabled=readonly) and diagnosis:
                if mutate(
                    "POST",
                    f"/api/sessions/{state['session_id']}/diagnosis",
                    "diagnosis",
                    {"diagnosis": diagnosis},
                ):
                    st.rerun()
    with treatment:
        action_column, finish_column = st.columns([1, 1], gap="large")
        with action_column, st.container(border=False, key="treatment_panel"):
            st.subheader("执行模拟处置")
            st.caption("选择本次模拟动作，明确确认后提交。完成全部记录后再封存训练。")
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
        with finish_column, st.container(border=False, key="finish_panel"):
            st.subheader("结束本次训练")
            if not state["sealed"]:
                st.caption("封存后不能修改本次训练，请先核对病历、诊断和实际执行的处置。")
            pending = pending_training_edits(state)
            if not readonly and pending:
                st.warning("、".join(pending) + "有未提交修改，请回到“病历与判断”保存或提交后再封存。")
            if not state["sealed"] and st.button("提交训练并封存", type="primary", disabled=readonly or bool(pending)):
                result = mutate(
                    "POST",
                    f"/api/sessions/{state['session_id']}/seal",
                    "seal",
                    {},
                )
                if result:
                    st.success("训练已封存，规则评分已生成；可继续生成分环节反馈。")
                    st.rerun()
    with review:
        if not state["sealed"]:
            st.info("完成训练并封存后，可查看评分和分环节反馈；下方可随时回放已记录的操作。")
        if state["sealed"]:
            st.info("本训练已封存，当前页面为只读反馈。")
            render_history_coverage(session.get("history_coverage"), detailed=True)
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
                summary_column, stages_column = st.columns([1, 2], gap="large")
                with summary_column, st.container(border=False, key="score_summary"):
                    st.metric("原规则总分", f"{snapshot['total_score']} / {snapshot['max_score']}")
                    st.caption("原规则评分 · 教学练习")
                with stages_column:
                    stage_scores = {}
                    for item in snapshot["items"]:
                        totals = stage_scores.setdefault(item["stage"], [0, 0])
                        totals[0] += item["score"]
                        totals[1] += item["max_score"]
                    for stage, (got, total) in stage_scores.items():
                        st.progress(got / total if total else 0, text=f"{stage} · {got:g} / {total:g}")
                with st.expander("逐项评分与改进依据"):
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
            if shown and shown.get("source") == "nexent":
                st.warning("模型反馈草稿已生成，待教师复核后展示。请将训练编号提供给教师，复核后点击刷新。")
                shown = None
            if shown:
                st.caption(shown.get("notice", ""))
                if shown.get("basis_provenance") == "legacy_unversioned":
                    st.warning("历史反馈未留存教学依据版本，无法确认生成时的来源。请教师核对；当前知识库不能替代历史依据。")
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
                                if entry.get("revision"):
                                    st.caption(f"依据修订 {entry['revision']} · 来源按本次反馈生成时保存")
                                for source in entry["sources"]:
                                    st.caption("来源：" + source)
                                if entry.get("restricted"):
                                    st.caption("部分题名或来源含参考答案，供教师核对，未向学生展示。")
                            else:
                                st.caption("当前记录未提供可展示的来源，请教师核对。")
            if st.button("开始新训练"):
                return_to_library()
        with st.container():
            st.subheader("训练回放")
            action_labels = {"ask": "问诊", "order_exam": "检查开单", "wait": "等待回报", "update_notes": "保存病历",
                "submit_differential": "提交鉴别诊断", "submit_diagnosis": "提交诊断", "plan_treatment": "计划处置",
                "execute_treatment": "执行模拟处置", "seal": "封存训练"}
            events = state.get("events", [])
            actions = list(dict.fromkeys(event['action'] for event in events))
            selected_action = st.selectbox("回放操作类型", ["all", *actions],
                format_func=lambda value: "全部操作" if value == "all" else action_labels.get(value, value),
                key=f"replay_action_{state['session_id']}")
            shown_events = [event for event in events if selected_action == "all" or event['action'] == selected_action]
            st.caption(f"显示 {len(shown_events)} / {len(events)} 条记录 · 按发生顺序排列 · 模拟时间不代表真实耗时")
            if not events:
                st.info("还没有操作记录。开始问诊或检查后，可在这里逐项复盘。")
            for event in shown_events:
                with st.expander(f"#{event['seq']} · {action_labels.get(event['action'], event['action'])} · 模拟时间 {event['sim_time']} 分钟"):
                    render_event_result(event, differential_labels, treatment_names, order_labels)

render_pending_error()

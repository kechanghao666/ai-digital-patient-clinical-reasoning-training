import streamlit as st
import requests

st.set_page_config(page_title="AI数字病人-课题1", layout="centered")
base = st.sidebar.text_input("后端地址", "http://127.0.0.1:8000")
owner = st.sidebar.text_input("学生标识", "demo-student")
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "error" not in st.session_state:
    st.session_state.error = None
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "pending_request" not in st.session_state:
    st.session_state.pending_request = None

if st.button("新建训练"):
    try:
        result = requests.post(f"{base}/sessions", headers={"X-Owner": owner}, timeout=5)
        result.raise_for_status()
        st.session_state.session_id = result.json()["session_id"]
        st.session_state.error = None
        st.session_state.last_result = None
        st.session_state.pending_request = None
    except requests.RequestException as exc:
        st.session_state.error = str(exc)

if st.session_state.session_id:
    st.write(f"会话：{st.session_state.session_id}")
    action = st.selectbox("操作", ["ask", "wait", "diagnose"])
    text = st.text_input("内容", "胸痛" if action == "ask" else "STEMI" if action == "diagnose" else "")
    if st.button("提交 / 重试"):
        try:
            import uuid

            pending = st.session_state.pending_request
            if pending is None or pending["action"] != action or pending["text"] != text:
                pending = {"request_id": str(uuid.uuid4()), "action": action, "text": text}
                st.session_state.pending_request = pending
            result = requests.post(
                f"{base}/sessions/{st.session_state.session_id}/actions",
                json=pending,
                headers={"X-Owner": owner},
                timeout=5,
            )
            result.raise_for_status()
            st.session_state.error = None
            st.session_state.last_result = result.json()
            st.session_state.pending_request = None
        except requests.RequestException as exc:
            st.session_state.error = str(exc)
    if st.session_state.error:
        st.error(st.session_state.error)
    if st.session_state.get("last_result"):
        st.json(st.session_state.last_result)

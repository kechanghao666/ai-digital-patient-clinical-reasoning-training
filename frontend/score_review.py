"""Teacher score adjudication keeps original rule scores visible and unchanged."""
import uuid
from urllib.parse import quote

import streamlit as st


def render_score_review(api):
    st.subheader("评分复核与裁定")
    st.caption("支持已完成自动评分或待人工复核的封存训练。发现争议时逐项核对并填写原因，另存教师裁定版本。")
    st.warning("当前为开发演示教师身份，裁定用于教学复盘，不代表正式成绩、医学审核或独立专家评测。")
    with st.form("score_review_lookup"):
        owner = st.text_input("学生账号", key="score_review_owner")
        session = st.text_input("训练编号", key="score_review_session")
        load = st.form_submit_button("载入或刷新评分")
    if load:
        for key in list(st.session_state):
            if key.startswith("score_editor_"):
                del st.session_state[key]
        st.session_state.pop("score_review_record", None)
        st.session_state.pop("score_review_pending", None)
        if not owner.strip() or not session.strip():
            st.error("请填写学生账号和训练编号。")
        else:
            record = api("GET", f"/api/teacher/scores/{quote(session.strip(), safe='')}", params={"owner_id": owner.strip()})
            if record:
                st.session_state.score_review_record = record
    record = st.session_state.get("score_review_record")
    if not record:
        return
    st.write(f"{record['case']['title']} · v{record['case']['version']} · 已有{record['current_revision']}版教师裁定")
    st.caption(f"当前载入：{record['owner_id']} · {record['session_id']}")
    if record["basis_coverage"]["missing_clinical_stages"]:
        st.warning("缺少适用专科临床依据：" + "、".join(record["basis_coverage"]["missing_clinical_stages"]) + "。需先由教师核对专业依据。")
    original = record["original_score"]
    with st.expander("原规则评分、量表与封存证据（只读）", expanded=True):
        status = {"complete": "已完成自动评分", "needs_review": "待人工复核"}[original["status"]]
        st.write(f"原规则分数：{original['total_score']:g} / {original['max_score']:g}，状态：{status}")
        st.json({key: record[key] for key in ("original_score", "rubric", "events", "submissions", "case_notes")})
    with st.expander("历次教师裁定与内部原因（只读）"):
        st.json(record["reviews"])
    current = record["reviews"][-1]["snapshot"] if record["reviews"] else original
    descriptions = {item["item_id"]: item["description"] for item in record["rubric"]}
    event_ids = [event["event_id"] for event in record["events"]]
    event_labels = {event["event_id"]: f"#{event['seq']} · 模拟{event['sim_time']}分钟 · {event['event_id']}" for event in record["events"]}
    decisions = []
    with st.form("score_review_edit"):
        st.markdown("**逐项裁定**")
        st.caption("分项说明将向学生展示，请说明判断依据，不公布隐藏参考答案。得分项必须选择真实证据事件。")
        for item in current["items"]:
            key = item["item_id"]
            with st.expander(f"{item['stage']} · {descriptions.get(key, key)}", expanded=True):
                score = st.number_input("裁定得分", min_value=0.0, max_value=float(item["max_score"]),
                    value=float(item["score"]), step=0.1, key=f"score_editor_{key}_score")
                reason = st.text_area("分项裁定说明（学生可见）", value=item["reason"], max_chars=600, key=f"score_editor_{key}_reason")
                evidence = st.multiselect("裁定证据事件", event_ids,
                    default=[value for value in item["evidence_event_ids"] if value in event_ids],
                    format_func=lambda value: event_labels[value], key=f"score_editor_{key}_events")
                decisions.append({"item_id": key, "score": score, "reason": reason, "evidence_event_ids": evidence})
        reason = st.text_area("整体裁定原因（仅教师可见）", max_chars=1000, key="score_editor_reason",
                              help="说明原分争议及依据；即使原规则已完成计算，也须填写具体复核原因。")
        confirmed = st.checkbox("我已逐项核对分数、封存前事件和适用量表", key="score_editor_confirmed")
        save = st.form_submit_button("保存独立评分裁定", type="primary")
    if save:
        if not confirmed:
            st.error("请先逐项核对并确认，尚未提交裁定。")
            return
        if not reason.strip():
            st.error("请填写具体裁定原因，尚未提交裁定。")
            return
        payload = {"owner_id": record["owner_id"], "expected_revision": record["current_revision"],
                   "reason": reason, "confirmed": confirmed, "items": decisions}
        pending = st.session_state.get("score_review_pending")
        if not pending or pending["payload"] != payload:
            pending = {"payload": payload, "request_id": str(uuid.uuid4())}
            st.session_state.score_review_pending = pending
        result = api("POST", f"/api/teacher/scores/{quote(record['session_id'], safe='')}", json={**payload, "request_id": pending["request_id"]})
        if result:
            st.session_state.pop("score_review_record", None)
            st.session_state.pop("score_review_pending", None)
            st.success(f"已保存教师评分裁定第{result['revision']}版；原规则评分和反馈保留。继续修订前请重新载入。")

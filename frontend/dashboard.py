"""Teacher class overview: overview -> per case -> per training (read-only)."""
import pandas as pd
import streamlit as st

STAGES = ["病史采集", "检查选择", "鉴别诊断", "诊断", "治疗"]
STATUS = {"complete": "已评分", "needs_review": "待复核", "failed": "评分失败", None: "进行中"}


def _pct(value):
    return None if value is None else round(value * 100, 1)


def render_dashboard(api):
    st.subheader("班级训练看板")
    data = api("GET", "/api/teacher/dashboard")
    if data is None:
        return
    st.caption(data["notice"])
    overview = data["overview"]
    a, b, c, d, e = st.columns(5)
    a.metric("训练次数", overview["trainings"])
    b.metric("学生人数", overview["students"])
    c.metric("已封存", overview["sealed"])
    d.metric("待复核", overview["needs_review"])
    e.metric("平均得分率", "—" if overview["average_ratio"] is None else f"{_pct(overview['average_ratio'])}%")
    if not data["trainings"]:
        st.info("还没有学生训练记录。学生完成并封存训练后，这里会显示各病例与各环节的得分情况。")
        return

    st.markdown("**各病例五环节得分率（%）**")
    st.caption("低于其他环节的列，通常就是下次课需要讲解或示范的重点。")
    rows = []
    for case in data["cases"]:
        row = {"病例": f"{case['public_id']} {case['title']}", "训练": case["trainings"], "封存": case["sealed"],
               "待复核": case["needs_review"], "平均得分率": _pct(case["average_ratio"])}
        row.update({stage: _pct(case["stage_ratios"].get(stage)) for stage in STAGES})
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    chart = pd.DataFrame(
        {case["public_id"]: [_pct(case["stage_ratios"].get(stage)) or 0 for stage in STAGES]
         for case in data["cases"] if case["sealed"]},
        index=STAGES,
    )
    if not chart.empty:
        st.bar_chart(chart, stack=False, y_label="得分率 %", height=260)

    st.markdown("**高频失分项**")
    lost = [
        {"病例": case["public_id"], "环节": item["stage"], "评分项": item["description"],
         "失分次数": f"{item['lost']}/{item['attempts']}", "失分率%": _pct(item["loss_rate"])}
        for case in data["cases"] for item in case["frequently_lost"]
    ]
    if lost:
        st.dataframe(pd.DataFrame(lost), hide_index=True, width="stretch")
    else:
        st.write("已封存训练中暂无失分项。")

    st.markdown("**逐条训练**")
    cases = sorted({t["public_id"] for t in data["trainings"]})
    left, right = st.columns([1, 1])
    chosen = left.selectbox("按病例筛选", ["全部病例", *cases], key="dashboard_case")
    only_review = right.toggle("只看待复核", key="dashboard_review_only")
    trainings = [t for t in data["trainings"]
                 if (chosen == "全部病例" or t["public_id"] == chosen)
                 and (not only_review or t["score_status"] == "needs_review")]
    table = [{
        "学生": t["display_name"] or t["username"], "账号": t["username"], "病例": f"{t['public_id']} v{t['case_version']}",
        "训练编号": t["summary_id"], "提问数": t["question_count"],
        "问诊结构": f"{t['history_domains']}/{t['history_domains_total']}",
        "状态": STATUS.get(t["score_status"], t["score_status"]), "得分率%": _pct(t["score_ratio"]),
    } for t in trainings]
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    reviewable = [t for t in trainings if t["score_status"] in {"complete", "needs_review"}]
    if reviewable:
        labels = {t["session_id"]: f"{t['display_name'] or t['username']} · {t['public_id']} · {t['summary_id']}"
                  f" · {STATUS.get(t['score_status'])}" for t in reviewable}
        target = st.selectbox("选择训练进行评分复核", list(labels), format_func=labels.get, key="dashboard_target")
        if st.button("打开评分复核与裁定", key="dashboard_open_review"):
            training = next(t for t in reviewable if t["session_id"] == target)
            st.session_state.score_review_owner = training["owner_id"]
            st.session_state.score_review_session = training["session_id"]
            st.session_state.teacher_task_next = "评分复核与裁定"
            st.rerun()

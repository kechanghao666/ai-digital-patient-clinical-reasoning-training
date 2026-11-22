"""History-structure progress (student) and class dashboard (teacher)."""
from streamlit.testing.v1 import AppTest

from app.accounts import AccountStore
from app.history_coverage import history_coverage
from app.models import ActionKind, Event
from test_cases_config import ROOT, Api
from test_frontend_smoke import _route_requests

TEACHER = {"X-Owner-Id": "teacher-dash"}


def _ask_event(question, seq=0):
    return Event(request_id=f"r{seq}", seq=seq, actor="student", action=ActionKind.ASK, intent=question,
                 sim_time=0, state_before={}, state_after={})


def test_coverage_uses_only_question_text_and_is_case_independent():
    events = [_ask_event(q, i) for i, q in enumerate(["怎么了", "疼了多久", "疼痛会跑到别的地方吗", "家里人有类似的病吗"])]
    coverage = history_coverage(events)
    asked = {item["domain"] for item in coverage["domains"] if item["asked"]}
    assert asked == {"chief", "hpi", "family"}
    assert coverage["question_count"] == 4 and coverage["covered"] == 3 and coverage["total"] == 8
    hpi = {item["label"]: item["asked"] for item in coverage["hpi_elements"]}
    assert hpi["起病时间"] and hpi["放射"] and not hpi["部位"]
    # Unasked domains carry generic structure hints only.
    hints = " ".join(item["hint"] for item in coverage["domains"])
    for forbidden in ("阑尾", "心梗", "冠心病", "卒中", "酮症"):
        assert forbidden not in hints


def test_session_detail_returns_coverage_and_dashboard_aggregates(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-002")
    for index, question in enumerate(["怎么了", "疼了多久", "有没有发烧"]):
        api.client.post(f"/api/sessions/{sid}/ask", headers=api.headers, json={"request_id": f"q{index}", "question": question})
    detail = api.client.get(f"/api/sessions/{sid}", headers=api.headers).json()
    assert detail["history_coverage"]["covered"] == 3
    api.start("CASE-001")  # unsealed second training
    assert api.client.post(f"/api/sessions/{sid}/seal", headers=api.headers, json={"request_id": "seal"}).status_code == 200

    assert api.client.get("/api/teacher/dashboard", headers=api.headers).status_code == 403
    data = api.client.get("/api/teacher/dashboard", headers=TEACHER).json()
    assert data["overview"]["trainings"] == 2 and data["overview"]["sealed"] == 1 and data["overview"]["students"] == 1
    appy = next(case for case in data["cases"] if case["public_id"] == "CASE-002")
    assert appy["sealed"] == 1 and set(appy["stage_ratios"]) == {"病史采集", "检查选择", "鉴别诊断", "诊断", "治疗"}
    assert appy["frequently_lost"] and all(item["loss_rate"] == 1.0 for item in appy["frequently_lost"])
    sealed = next(t for t in data["trainings"] if t["session_id"] == sid)
    state = api.repository.get(sid, "demo-cfg")
    assert sealed["score_ratio"] == round(state.score_snapshot.total_score / state.score_snapshot.max_score, 4)
    assert sealed["history_domains"] == 3
    # Read-only: the dashboard never changes stored trainings.
    assert api.repository.get(sid, "demo-cfg") == state


def test_account_directory_exposes_no_secrets(tmp_path):
    store = AccountStore(tmp_path / "accounts.db")
    store.create("alice", "Long-Password-123", "student", "Alice")
    account_id = _id(store, "alice")
    profile = store.directory([account_id, "unknown"])
    assert list(profile) == [account_id]
    assert set(profile[account_id]) == {"id", "username", "display_name", "role"}


def _id(store, username):
    with store.connect() as db:
        return db.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()["id"]


def test_dashboard_page_renders_and_opens_score_review(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-002")
    api.client.post(f"/api/sessions/{sid}/seal", headers=api.headers, json={"request_id": "seal"})
    _route_requests(monkeypatch, api)
    page = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=30).run()
    page.radio(key="workspace").set_value("教师病例管理").run()
    assert not page.exception, page.exception
    assert page.radio(key="teacher_task").value == "班级训练看板"
    assert any(m.label == "训练次数" for m in page.metric)
    next(b for b in page.button if b.label == "打开评分复核与裁定").click().run()
    assert not page.exception, page.exception
    assert page.radio(key="teacher_task").value == "评分复核与裁定"
    assert page.text_input(key="score_review_session").value == sid


def test_student_page_shows_structure_progress(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, owner="demo-student")
    sid = api.start("CASE-002")
    api.client.post(f"/api/sessions/{sid}/ask", headers=api.headers, json={"request_id": "q", "question": "怎么了"})
    _route_requests(monkeypatch, api)
    page = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=30).run()
    next(b for b in page.button if b.label == "恢复训练").click().run()
    assert not page.exception, page.exception
    assert page.session_state["session_id"] == sid
    html = " ".join(str(getattr(block, "proto", "")) for block in page.get("html"))
    assert "问诊结构 1/8" in html
    assert any(e.label == "问诊结构提示" for e in page.expander)

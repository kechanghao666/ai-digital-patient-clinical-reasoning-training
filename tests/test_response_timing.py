from test_integration import Env, select
from test_frontend_smoke import _page, _route_requests


def test_answer_timing_follows_safety_gate_and_replay_has_fresh_request_time(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, select("f_hx_htn"))
    sid = env.new_session()
    result = env.ask(sid, "有高血压吗", "timed")
    assert result.status_code == 200, result.text
    timing = result.json()["timing"]
    assert 0 <= timing["answer_validated_ms"] <= timing["api_ready_ms"]
    trace = env.stored_events(sid)[0].result["adapter"]["trace"]
    assert trace["answer_validated_ms"] == timing["answer_validated_ms"]
    assert "trace" not in result.text and "first_event_ms" not in result.text
    retry = env.ask(sid, "有高血压吗", "timed").json()
    assert retry["timing"]["replayed"] and retry["timing"]["answer_validated_ms"] is None
    assert len(env.runs) == 1


def test_frontend_shows_received_answer_duration_and_clears_on_identity_change(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    sid = env.new_session()
    _route_requests(monkeypatch, env)
    page = _page(env)
    page.text_input(key="student_identity").set_value("demo-integration").run()
    next(b for b in page.button if b.label == "恢复训练").click().run()
    page.text_input(key="question").set_value("有高血压吗")
    next(b for b in page.button if b.label == "提交问题").click().run()
    assert not page.exception
    measured = page.session_state["answer_timing"]
    assert measured["session_id"] == sid and measured["response_received_ms"] >= 0
    assert any("本次回答请求用时" in caption.value for caption in page.caption)
    page.text_input(key="student_identity").set_value("demo-other").run()
    assert "answer_timing" not in page.session_state

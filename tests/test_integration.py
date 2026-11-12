"""Contract tests for the Nexent integration boundary.

These tests use a scripted fake of the Nexent northbound API (httpx.MockTransport). The fake "agent"
calls the real trusted tool service with the turn token it received, exactly as a platform agent
would call the MCP tools. They prove the backend contract only; they are NOT evidence that a real
Nexent instance, model or MCP connection works. Real-platform evidence is stored separately under
docs/evidence/ and is absent until an instance is available.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.adapters import diagnosis_leak_terms
from app.case_store import CaseStore
from app.integration.config import IntegrationSettings
from app.integration.knowledge import KnowledgeStore
from app.integration.nexent_client import NexentClient, NexentError
from app.integration.runtime import IntegrationRuntime
from app.integration.tools import FEEDBACK_STAGES, TrustedToolService
from app.integration.turns import FEEDBACK, PATIENT, TurnError, TurnStore
from app.state_machine import StateMachine
from app.storage import SessionRepository

ROOT = Path(__file__).parents[1]
API_KEY = "test-key-not-real-0123456789"
HIDDEN = "急性ST段抬高型心肌梗死（下壁），待医学审核"


def sse(*events: dict) -> bytes:
    return "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events).encode()


def ok_response(text: str = "好的", conv: str = "42") -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream", "conversation_id": conv},
        content=sse(
            {"type": "model_output_thinking", "content": "思考", "unit_index": 1},
            {"type": "final_answer", "content": text, "unit_index": 2},
        ),
    )


class BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"type":"model_output_thinking","content":"x"}\n\n'
        raise httpx.ReadError("connection reset")


def settings_for(tmp_path: Path, **overrides) -> IntegrationSettings:
    values = dict(
        mode="nexent",
        nexent_base_url="http://nexent.test",
        nexent_api_key=API_KEY,
        patient_agent_name="digital_patient",
        feedback_agent_name="feedback_reviewer",
        connect_timeout_s=1.0,
        first_event_timeout_s=1.0,
        total_timeout_s=5.0,
        turn_ttl_s=60.0,
        mcp_service_token="s" * 32,
        integration_db_path=tmp_path / "integration.db",
        knowledge_dir=ROOT / "knowledge",
    )
    values.update(overrides)
    return IntegrationSettings(**values)


class Env:
    """Scripted Nexent fake plus an API client wired to temporary stores."""

    def __init__(self, tmp_path: Path, monkeypatch, agent=None, mode: str = "nexent", feedback_protocol: str = "review_tool"):
        self.runs: list[dict] = []
        self.stops: list[str] = []
        self.requests: list[httpx.Request] = []
        self.agent = agent
        self.repository = SessionRepository(tmp_path / "sessions.db")
        cases = CaseStore(ROOT / "cases")
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setattr(main, "REPOSITORY", self.repository)
        monkeypatch.setattr(main, "MACHINE", StateMachine(cases, self.repository))
        self.settings = settings_for(tmp_path, mode=mode, feedback_protocol=feedback_protocol)
        self.runtime = IntegrationRuntime(self.settings, httpx.MockTransport(self._handle))
        monkeypatch.setattr(main, "RUNTIME", self.runtime)
        self.service = TrustedToolService(
            self.runtime.turns, self.repository, cases, KnowledgeStore(ROOT / "knowledge")
        )
        self.client = TestClient(main.app)
        self.headers = {"X-Owner-Id": "demo-integration"}

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET" and request.url.path.startswith("/nb/v1/chat/stop/"):
            self.stops.append(request.url.path)
            return httpx.Response(200, json={"message": "success"})
        body = json.loads(request.content)
        self.runs.append(body)
        assert self.agent is not None, "unexpected Nexent call"
        return self.agent(request, body, self.service)

    def new_session(self) -> str:
        response = self.client.post("/api/sessions?case_id=CASE-001", headers=self.headers)
        assert response.status_code == 200
        return response.json()["session_id"]

    def ask(self, session_id: str, question: str, request_id: str):
        return self.client.post(
            f"/api/sessions/{session_id}/ask",
            headers=self.headers,
            json={"request_id": request_id, "question": question},
        )

    def stored_events(self, session_id: str):
        return self.repository.get(session_id, "demo-integration").events


def token_of(body: dict) -> str:
    return body["metadata"]["turn_token"]


def select(*fact_ids: str, final: str = "已回答"):
    def agent(request, body, service):
        token = token_of(body)
        context = service.get_turn_context(token)
        assert context["ok"]
        service.submit_fact_selection(token, list(fact_ids))
        return ok_response(final)

    return agent


# --------------------------------------------------------------------------- patient chain
def test_nexent_selection_subset_is_rendered_by_backend(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, select("f_hx_htn", final="患者诊断为STEMI，api_key=abc"))
    sid = env.new_session()
    response = env.ask(sid, "请问既往史和用药情况", "r1")
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "有高血压10年。"
    assert "STEMI" not in json.dumps(body, ensure_ascii=False)
    assert "api_key" not in json.dumps(body)
    assert body["disclosed_fact_count"] == 1
    event = env.stored_events(sid)[0]
    assert event.fact_ids_revealed == ["f_hx_htn"]
    adapter = event.result["adapter"]
    assert adapter["source"] == "nexent"
    assert [c["tool"] for c in adapter["trace"]["tool_calls"]] == ["get_turn_context", "submit_fact_selection"]
    assert all(c["ok"] for c in adapter["trace"]["tool_calls"])
    assert "adapter" not in body["event"]["result"]
    request = env.requests[0]
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert env.runs[0]["final_answer_mode"] == "complete"
    assert "turn_token" in env.runs[0]["metadata"]
    # The audit-only block carries no owner or session identifiers.
    assert "demo-integration" not in json.dumps(env.runs[0], ensure_ascii=False)
    assert sid not in json.dumps(env.runs[0], ensure_ascii=False)


def test_questions_without_authorized_facts_do_not_call_the_model(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    sid = env.new_session()
    response = env.ask(sid, "你昨天晚饭吃了什么", "r1")
    assert response.status_code == 200
    assert env.runs == []
    assert env.stored_events(sid)[0].result["adapter"]["source"] == "local_no_candidates"


def test_selection_outside_candidates_is_refused_and_falls_back(tmp_path, monkeypatch):
    results = []

    def agent(request, body, service):
        token = token_of(body)
        results.append(service.submit_fact_selection(token, ["f_hx_smoke"]))
        results.append(service.submit_fact_selection(token, []))
        return ok_response()

    env = Env(tmp_path, monkeypatch, agent)
    sid = env.new_session()
    response = env.ask(sid, "有高血压吗", "r1")
    assert results[0]["ok"] is False and results[0]["error"] == "unauthorized_fact"
    assert results[1]["ok"] is True
    assert response.json()["answer"].startswith("我不太确定")
    event = env.stored_events(sid)[0]
    assert event.fact_ids_revealed == []
    assert event.result["fallback"] is True
    assert env.repository.get(sid, "demo-integration").disclosed_fact_ids == []


def test_agent_that_skips_tools_can_retry_without_recording_and_token_dies(tmp_path, monkeypatch):
    seen = {}

    def agent(request, body, service):
        seen["token"] = token_of(body)
        return ok_response("有高血压10年。")

    env = Env(tmp_path, monkeypatch, agent)
    sid = env.new_session()
    response = env.ask(sid, "有高血压吗", "r1")
    assert response.status_code == 502
    assert env.stored_events(sid) == []
    late = env.service.submit_fact_selection(seen["token"], ["f_hx_htn"])
    assert late["ok"] is False and late["error"] == "turn_cancelled"
    env.agent = select("f_hx_htn")
    retry = env.ask(sid, "有高血压吗", "r1")
    assert retry.status_code == 200
    replay = env.ask(sid, "有高血压吗", "r1")
    assert replay.json()["event"]["event_id"] == retry.json()["event"]["event_id"]
    assert len(env.stored_events(sid)) == 1


def test_timeout_registers_nothing_and_late_tool_calls_are_rejected(tmp_path, monkeypatch):
    seen = {}
    mode = {"timeout": True}

    def agent(request, body, service):
        if mode["timeout"]:
            seen["token"] = token_of(body)
            raise httpx.ReadTimeout("slow", request=request)
        return select("f_hx_htn")(request, body, service)

    env = Env(tmp_path, monkeypatch, agent)
    sid = env.new_session()
    response = env.ask(sid, "有高血压吗", "r1")
    assert response.status_code == 504
    assert env.stored_events(sid) == []
    assert env.repository.get(sid, "demo-integration").pending_request_hashes == {}
    late = env.service.submit_fact_selection(seen["token"], ["f_hx_htn"])
    assert late["ok"] is False and late["error"] == "turn_cancelled"
    mode["timeout"] = False
    retry = env.ask(sid, "有高血压吗", "r1")
    assert retry.status_code == 200
    assert len(env.stored_events(sid)) == 1


def test_connection_loss_midstream_registers_nothing(tmp_path, monkeypatch):
    def agent(request, body, service):
        service.get_turn_context(token_of(body))
        return httpx.Response(200, headers={"conversation_id": "7"}, stream=BrokenStream())

    env = Env(tmp_path, monkeypatch, agent)
    sid = env.new_session()
    response = env.ask(sid, "有高血压吗", "r1")
    assert response.status_code == 502
    assert env.stored_events(sid) == []
    assert env.stops == ["/nb/v1/chat/stop/7"]


@pytest.mark.parametrize(
    ("status", "expected"), [(401, 502), (403, 502), (500, 502), (400, 502), (429, 429)]
)
def test_platform_errors_map_without_leaking_credentials(tmp_path, monkeypatch, status, expected):
    env = Env(tmp_path, monkeypatch, lambda r, b, s: httpx.Response(status, json={"detail": f"{API_KEY} boom"}))
    sid = env.new_session()
    response = env.ask(sid, "有高血压吗", "r1")
    assert response.status_code == expected
    assert API_KEY not in response.text
    assert env.stored_events(sid) == []


def test_duplicate_request_replays_without_second_model_call(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, select("f_hx_htn"))
    sid = env.new_session()
    first = env.ask(sid, "有高血压吗", "same")
    second = env.ask(sid, "有高血压吗", "same")
    assert first.json()["answer"] == second.json()["answer"]
    assert len(env.runs) == 1
    assert len(env.stored_events(sid)) == 1
    changed = env.ask(sid, "有糖尿病吗", "same")
    assert changed.status_code == 409
    assert len(env.runs) == 1


def test_ask_after_seal_is_rejected_before_any_model_call(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    sid = env.new_session()
    sealed = env.client.post(f"/api/sessions/{sid}/seal", headers=env.headers, json={"request_id": "s1"})
    assert sealed.status_code == 200
    blocked = env.ask(sid, "有高血压吗", "r-after")
    assert blocked.status_code == 409
    assert env.runs == []


def test_other_owner_cannot_use_session(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    sid = env.new_session()
    other = env.client.post(
        f"/api/sessions/{sid}/ask",
        headers={"X-Owner-Id": "demo-other"},
        json={"request_id": "x", "question": "有高血压吗"},
    )
    assert other.status_code == 404
    assert env.runs == []


def test_concurrent_change_during_model_call_discards_stale_answer(tmp_path, monkeypatch):
    holder = {}

    def agent(request, body, service):
        # While the model is "thinking" the student acts in another tab.
        env = holder["env"]
        env.client.post(
            f"/api/sessions/{holder['sid']}/action",
            headers=env.headers,
            json={"request_id": "other-tab", "action": "order_exam", "payload": {"exam_id": "ex_ecg"}},
        )
        result = service.get_turn_context(token_of(body))
        holder["context"] = result
        holder["selection"] = service.submit_fact_selection(token_of(body), ["f_hx_htn"])
        return ok_response()

    env = Env(tmp_path, monkeypatch, agent)
    holder["env"] = env
    holder["sid"] = env.new_session()
    response = env.ask(holder["sid"], "有高血压吗", "r1")
    assert holder["context"]["ok"] is False and holder["context"]["error"] == "state_changed"
    assert holder["selection"]["ok"] is False
    # The stale ask is rejected as a version conflict; only the other tab's action persists.
    assert response.status_code == 409
    events = env.stored_events(holder["sid"])
    assert [e.action.value for e in events] == ["order_exam"]


# ---------------------------------------------------------------------- token / tool boundary
def _issue(env: Env, sid: str, *, kind=PATIENT, version=0, ttl=60.0):
    session = env.repository.get(sid, "demo-integration")
    return env.runtime.turns.issue(
        kind=kind,
        owner_id="demo-integration",
        session_id=sid,
        case_id=session.case_id,
        case_version=session.case_version,
        request_id="r",
        state_version=version,
        question="有高血压吗",
        candidate_fact_ids=["f_hx_htn"],
        ttl_s=ttl,
    )


def test_tokens_are_bound_and_unforgeable(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    sid = env.new_session()
    turn, token = _issue(env, sid)
    assert env.service.get_turn_context(token)["ok"]
    for bad in ("", "nodot", turn.turn_id + ".wrong", "other." + token.split(".")[1], "x" * 300):
        result = env.service.get_turn_context(bad)
        assert result["ok"] is False and result["error"] == "invalid_token"
    wrong_kind = env.service.get_sealed_review(token)
    assert wrong_kind["ok"] is False and wrong_kind["error"] == "wrong_kind"
    # The token is stored only as a hash.
    import sqlite3

    raw = sqlite3.connect(env.settings.integration_db_path).execute("SELECT secret_hash FROM turns").fetchone()[0]
    assert token.split(".")[1] not in raw


def test_tool_signatures_never_accept_authoritative_fields():
    import asyncio
    from fastmcp import Client

    from app.mcp_server import build_mcp

    class Dummy:
        def __getattr__(self, name):
            raise AssertionError("tools must not be invoked here")

    async def collect():
        async with Client(build_mcp(Dummy())) as client:
            return {tool.name: set(tool.input_schema["properties"]) for tool in await client.list_tools()}

    tools = asyncio.run(collect())
    assert tools == {
        "get_turn_context": {"turn_token"},
        "submit_fact_selection": {"turn_token", "fact_ids"},
        "get_sealed_review": {"turn_token"},
        "search_teaching_basis": {"turn_token", "query", "stage"},
        "submit_feedback": {"turn_token", "items", "use_stage_basis"},
    }
    forbidden = {"session_id", "owner_id", "case_id", "case_version", "score", "facts", "hidden_diagnosis"}
    assert not forbidden & set().union(*tools.values())


def test_mcp_protocol_roundtrip_matches_service(tmp_path, monkeypatch):
    import asyncio
    from fastmcp import Client

    from app.mcp_server import build_mcp

    env = Env(tmp_path, monkeypatch)
    sid = env.new_session()
    turn, token = _issue(env, sid)

    async def run():
        async with Client(build_mcp(env.service)) as client:
            context = await client.call_tool("get_turn_context", {"turn_token": token})
            bad = await client.call_tool("submit_fact_selection", {"turn_token": token, "fact_ids": ["f_hx_dm"]})
            good = await client.call_tool("submit_fact_selection", {"turn_token": token, "fact_ids": ["f_hx_htn"]})
            return context.data, bad.data, good.data

    context, bad, good = asyncio.run(run())
    assert context["candidates"] == [{"fact_id": "f_hx_htn", "content": "有高血压10年。"}]
    assert bad["error"] == "unauthorized_fact"
    assert good["ok"] is True
    assert env.runtime.turns.get(turn.turn_id).submission == {"fact_ids": ["f_hx_htn"]}


def test_expired_state_changed_and_sealed_turns_are_refused(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    sid = env.new_session()
    _, expired = _issue(env, sid, ttl=-1.0)
    assert env.service.get_turn_context(expired)["error"] == "turn_expired"
    # state version differs from the one recorded on the turn
    _, stale = _issue(env, sid, version=5)
    assert env.service.get_turn_context(stale)["error"] == "state_changed"
    _, live = _issue(env, sid, version=0)
    env.client.post(f"/api/sessions/{sid}/seal", headers=env.headers, json={"request_id": "s"})
    assert env.service.get_turn_context(live)["ok"] is False
    # After sealing the same session cannot receive new patient turns' selections either.
    _, after = _issue(env, sid, version=0)
    assert env.service.submit_fact_selection(after, ["f_hx_htn"])["error"] == "session_sealed"


def test_new_turn_supersedes_old_one_and_repeated_misuse_cancels(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    sid = env.new_session()
    _, first = _issue(env, sid)
    _, second = _issue(env, sid)
    assert env.service.get_turn_context(first)["error"] == "turn_cancelled"
    assert env.service.get_turn_context(second)["ok"]
    for _ in range(3):
        assert env.service.submit_fact_selection(second, ["f_hx_dm"])["error"] == "unauthorized_fact"
    assert env.service.submit_fact_selection(second, ["f_hx_htn"])["error"] == "turn_cancelled"


def test_only_one_concurrent_selection_wins(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    sid = env.new_session()
    _, token = _issue(env, sid)
    results = []
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait()
        results.append(env.service.submit_fact_selection(token, ["f_hx_htn"])["ok"])

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1


def test_turn_store_tokens_do_not_cross_sessions(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    a, b = env.new_session(), env.new_session()
    turn_a, token_a = _issue(env, a)
    turn_b, token_b = _issue(env, b)
    assert env.runtime.turns.resolve(token_a, kind=PATIENT).session_id == a
    assert env.runtime.turns.resolve(token_b, kind=PATIENT).session_id == b
    with pytest.raises(TurnError):
        env.runtime.turns.resolve(token_a.split(".")[0] + "." + token_b.split(".")[1], kind=PATIENT)


# -------------------------------------------------------------------------- feedback chain
def _play_and_seal(env: Env) -> str:
    sid = env.new_session()
    assert env.ask(sid, "疼痛是什么性质", "q1").status_code == 200
    for rid, action, payload in (
        ("o1", "order_exam", {"exam_id": "ex_ecg"}),
        ("w1", "wait", {"minutes": 5}),
        ("d1", "submit_diagnosis", {"diagnosis": "STEMI"}),
        ("t1", "execute_treatment", {"treatment_id": "reperfusion_pathway", "confirm_execution": True}),
    ):
        res = env.client.post(
            f"/api/sessions/{sid}/action", headers=env.headers, json={"request_id": rid, "action": action, "payload": payload}
        )
        assert res.status_code == 200, res.text
    sealed = env.client.post(f"/api/sessions/{sid}/seal", headers=env.headers, json={"request_id": "seal"})
    assert sealed.status_code == 200
    return sid


def feedback_agent(items_factory=None, record=None):
    def agent(request, body, service):
        assert "search_teaching_basis" in body["query"]
        assert "knowledge_base_search" not in body["query"]
        token = token_of(body)
        review = service.get_sealed_review(token)
        assert review["ok"], review
        assert set(review["stage_basis"]) == set(FEEDBACK_STAGES)
        for stage, entry in review["stage_basis"].items():
            assert stage in service.knowledge.entries[entry["basis_id"]].stages
        assert "diagnosis" not in review["submissions"]
        assert all("diagnosis" not in event for event in review["events"])
        assert all(item["description"] == "诊断结论" for item in review["score"]["items"] if item["stage"] == "诊断")
        basis = service.search_teaching_basis(token, "心电图 优先 检查选择", "检查选择")
        assert basis["ok"] and basis["results"]
        assert all("检查选择" in entry["stages"] for entry in basis["results"])
        grouped = service.search_teaching_basis(token, "病史 检查 鉴别 诊断 治疗 反馈")
        assert set(grouped["stage_basis"]) == set(FEEDBACK_STAGES)
        for stage, entry in grouped["stage_basis"].items():
            assert stage in service.knowledge.entries[entry["basis_id"]].stages
        event_ids = [e["event_id"] for e in review["events"]]
        items = (
            items_factory(review, basis, event_ids)
            if items_factory
            else [
                {
                    "stage": stage,
                    "comment": "请结合已记录的操作与本环节评分复核训练过程。",
                    "improvement": "回看本环节事件，补充尚未采集或记录的依据。",
                    "evidence_event_ids": [event_ids[1]],
                    "basis_ids": ["kb_feedback_principles"],
                }
                for stage in FEEDBACK_STAGES
            ]
        )
        if record is not None:
            record.append(service.submit_feedback(token, items))
        else:
            service.submit_feedback(token, items)
        return ok_response("反馈已提交")

    return agent


def test_feedback_chain_is_independent_after_seal_and_idempotent(tmp_path, monkeypatch):
    results = []
    env = Env(tmp_path, monkeypatch, None)
    env.agent = lambda r, b, s: select("f_pain_character")(r, b, s) if b["agent_name"] == "digital_patient" else feedback_agent(record=results)(r, b, s)
    sid = _play_and_seal(env)
    before = env.repository.get(sid, "demo-integration").model_dump_json()
    assert env.client.get(f"/api/sessions/{sid}/feedback", headers=env.headers).status_code == 404
    first = env.client.post(f"/api/sessions/{sid}/feedback", headers=env.headers, json={"request_id": "f1"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["source"] == "nexent"
    assert [item["stage"] for item in body["items"]] == list(FEEDBACK_STAGES)
    assert results == [{"ok": True, "accepted_items": 5}]
    again = env.client.post(f"/api/sessions/{sid}/feedback", headers=env.headers, json={"request_id": "f2"})
    assert again.json() == body
    fetched = env.client.get(f"/api/sessions/{sid}/feedback", headers=env.headers)
    assert fetched.json() == body
    assert len([r for r in env.runs if r["agent_name"] == "feedback_reviewer"]) == 1
    # Sealed record, including the score snapshot, is untouched by feedback generation.
    assert env.repository.get(sid, "demo-integration").model_dump_json() == before
    text = json.dumps(body, ensure_ascii=False)
    assert HIDDEN not in text and "turn_token" not in text and API_KEY not in text


def test_feedback_rejected_before_seal(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    sid = env.new_session()
    response = env.client.post(f"/api/sessions/{sid}/feedback", headers=env.headers, json={"request_id": "f"})
    assert response.status_code == 409
    assert env.runs == []
    session = env.repository.get(sid, "demo-integration")
    _, token = env.runtime.turns.issue(
        kind=FEEDBACK, owner_id="demo-integration", session_id=sid, case_id=session.case_id,
        case_version=session.case_version, request_id="f", state_version=0,
    )
    assert env.service.get_sealed_review(token)["error"] == "not_sealed"


@pytest.mark.parametrize("mode", ["nexnet", "bogus"])
def test_feedback_invalid_mode_never_falls_back_to_local_rules(tmp_path, monkeypatch, mode):
    env = Env(tmp_path, monkeypatch, agent=select("f_pain_character"))
    sid = _play_and_seal(env)
    before = env.repository.get(sid, "demo-integration").model_dump_json()
    calls_before = len(env.runs)
    env.runtime.__dict__["settings"] = settings_for(tmp_path, mode=mode)
    endpoint = f"/api/sessions/{sid}/feedback"
    response = env.client.post(endpoint, headers=env.headers, json={"request_id": "invalid-mode"})
    assert response.status_code == 502
    assert env.runtime.feedback_store.get(sid, "demo-integration") is None
    assert len(env.runs) == calls_before
    assert env.repository.get(sid, "demo-integration").model_dump_json() == before


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item, ids: item.update(score=10),
        lambda item, ids: item.update(evidence_event_ids=["not-an-event"]),
        lambda item, ids: item.update(basis_ids=["kb_unknown"]),
        lambda item, ids: item.update(stage="不存在的环节"),
        lambda item, ids: item.update(comment="正确诊断是" + HIDDEN),
        lambda item, ids: item.update(comment="x" * 601),
        lambda item, ids: item.update(improvement="请使用 api_key 访问"),
    ],
)
def test_feedback_validation_rejects_unsafe_items(tmp_path, monkeypatch, mutate):
    outcome = []

    def factory(review, basis, ids):
        item = {
            "stage": "诊断",
            "comment": "诊断结论明确。",
            "improvement": "补充心电图依据。",
            "evidence_event_ids": [ids[-3]],
            "basis_ids": [basis["results"][0]["basis_id"]],
        }
        mutate(item, ids)
        return [item if stage == "诊断" else {
            "stage": stage, "comment": "复核本环节训练记录。", "improvement": "补充可核对的依据。",
            "evidence_event_ids": [], "basis_ids": ["kb_feedback_principles"],
        } for stage in FEEDBACK_STAGES]

    env = Env(tmp_path, monkeypatch, None)
    env.agent = lambda r, b, s: select("f_pain_character")(r, b, s) if b["agent_name"] == "digital_patient" else feedback_agent(factory, outcome)(r, b, s)
    sid = _play_and_seal(env)
    response = env.client.post(f"/api/sessions/{sid}/feedback", headers=env.headers, json={"request_id": "f"})
    assert outcome and outcome[0]["ok"] is False and outcome[0]["error"] == "invalid_feedback"
    assert response.status_code == 502
    assert env.client.get(f"/api/sessions/{sid}/feedback", headers=env.headers).status_code == 404


def test_feedback_failure_stores_nothing_and_can_be_retried(tmp_path, monkeypatch):
    mode = {"fail": True}
    env = Env(tmp_path, monkeypatch, None)

    def agent(request, body, service):
        if body["agent_name"] == "digital_patient":
            return select("f_pain_character")(request, body, service)
        if mode["fail"]:
            raise httpx.ReadTimeout("slow", request=request)
        return feedback_agent()(request, body, service)

    env.agent = agent
    sid = _play_and_seal(env)
    failed = env.client.post(f"/api/sessions/{sid}/feedback", headers=env.headers, json={"request_id": "f"})
    assert failed.status_code == 504
    assert env.client.get(f"/api/sessions/{sid}/feedback", headers=env.headers).status_code == 404
    mode["fail"] = False
    retry = env.client.post(f"/api/sessions/{sid}/feedback", headers=env.headers, json={"request_id": "f"})
    assert retry.status_code == 200


def test_feedback_other_owner_is_denied(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, None, mode="simulator")
    sid = _play_and_seal(env)
    response = env.client.post(
        f"/api/sessions/{sid}/feedback", headers={"X-Owner-Id": "demo-other"}, json={"request_id": "f"}
    )
    assert response.status_code == 404


def test_simulator_feedback_is_deterministic_and_labelled_local(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, None, mode="simulator")
    sid = _play_and_seal(env)
    response = env.client.post(f"/api/sessions/{sid}/feedback", headers=env.headers, json={"request_id": "f"})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "local_rules"
    assert {item["stage"] for item in body["items"]} == {"病史采集", "检查选择", "鉴别诊断", "诊断", "治疗"}
    assert env.runs == []
    assert HIDDEN not in json.dumps(body, ensure_ascii=False)


# --------------------------------------------------------------------------- client protocol
def make_client(tmp_path, handler) -> NexentClient:
    return NexentClient(settings_for(tmp_path), httpx.MockTransport(handler))


@pytest.mark.parametrize("status,event,kind", [(403, None, "auth"), (200, "run_error", "rejected")])
def test_nexent_rejection_log_omits_remote_error_text(tmp_path, caplog, status, event, kind):
    remote_detail = "private-provider-error"
    if event:
        payload = json.dumps({"type": event, "content": remote_detail})
        response = httpx.Response(status, text=f"data: {payload}\n\n")
    else:
        response = httpx.Response(status, text=remote_detail)
    client = make_client(tmp_path, lambda _: response)
    with pytest.raises(NexentError) as raised:
        client.run(agent_name="agent", query="private-query")
    assert raised.value.kind == kind
    assert f"kind={kind} http_status={status} error_event={event}" in caplog.text
    assert remote_detail not in caplog.text
    assert "private-query" not in caplog.text


def test_client_complete_and_delta_modes(tmp_path):
    complete = make_client(
        tmp_path,
        lambda r: httpx.Response(
            200,
            content=sse({"type": "final_answer", "answer_phase": "delta", "content": "你"},
                        {"type": "final_answer", "answer_phase": "delta", "content": "好"},
                        {"type": "final_answer", "answer_phase": "complete", "content": ""}),
        ),
    ).run(agent_name="a", query="q")
    assert complete.final_text == "你好"
    revised = make_client(
        tmp_path,
        lambda r: httpx.Response(
            200,
            content=sse({"type": "final_answer", "answer_phase": "delta", "content": "草"},
                        {"type": "final_answer", "answer_phase": "complete", "content": "终稿"}),
        ),
    ).run(agent_name="a", query="q")
    assert revised.final_text == "终稿"


def test_client_sends_idempotency_key_and_never_retries(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    client = make_client(tmp_path, handler)
    with pytest.raises(NexentError) as raised:
        client.run(agent_name="a", query="q", idempotency_key="k1")
    assert raised.value.kind == "unavailable"
    assert len(calls) == 1
    assert calls[0].headers["idempotency-key"] == "k1"
    assert API_KEY not in str(raised.value)


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        (sse({"type": "model_output_thinking", "content": "x"}), "protocol"),
        (sse({"type": "error", "content": "boom"}), "rejected"),
        (b"", "protocol"),
    ],
)
def test_client_requires_a_final_answer(tmp_path, content, kind):
    client = make_client(tmp_path, lambda r: httpx.Response(200, content=content))
    with pytest.raises(NexentError) as raised:
        client.run(agent_name="a", query="q")
    assert raised.value.kind == kind


def test_client_conflict_header_is_not_retried(tmp_path):
    client = make_client(tmp_path, lambda r: httpx.Response(200, headers={"x-stream-status": "conflict"}, content=b""))
    with pytest.raises(NexentError) as raised:
        client.run(agent_name="a", query="q")
    assert raised.value.kind == "conflict"


def test_client_requires_configuration(tmp_path):
    with pytest.raises(NexentError):
        NexentClient(settings_for(tmp_path, nexent_api_key=""))
    with pytest.raises(NexentError):
        make_client(tmp_path, lambda r: httpx.Response(200)).run(agent_name="", query="q")


def test_unconfigured_nexent_mode_fails_closed(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, None)
    env.runtime.__dict__["settings"] = settings_for(tmp_path, nexent_api_key="")
    sid = env.new_session()
    response = env.ask(sid, "有高血压吗", "r1")
    assert response.status_code == 502
    assert env.stored_events(sid) == []
    env.runtime.__dict__["settings"] = settings_for(tmp_path, mode="bogus")
    assert env.ask(sid, "有高血压吗", "r2").status_code == 502


def test_settings_validation(monkeypatch):
    monkeypatch.setenv("NEXENT_TOTAL_TIMEOUT_S", "abc")
    with pytest.raises(ValueError):
        IntegrationSettings.from_env()
    monkeypatch.setenv("NEXENT_TOTAL_TIMEOUT_S", "-1")
    with pytest.raises(ValueError):
        IntegrationSettings.from_env()
    monkeypatch.setenv("NEXENT_TOTAL_TIMEOUT_S", "30")
    monkeypatch.setenv("NEXENT_MODE", " NEXENT ")
    assert IntegrationSettings.from_env().uses_nexent
    monkeypatch.setenv("NEXENT_FEEDBACK_PROTOCOL", "compact")
    assert IntegrationSettings.from_env().feedback_protocol == "compact"
    monkeypatch.setenv("NEXENT_FEEDBACK_PROTOCOL", "unknown")
    with pytest.raises(ValueError, match="NEXENT_FEEDBACK_PROTOCOL"):
        IntegrationSettings.from_env()


# ------------------------------------------------------------------------- MCP transport auth
def test_bearer_gate_requires_service_token():
    from app.mcp_server import BearerGate

    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    client = TestClient(BearerGate(inner, "t" * 32))
    assert client.post("/mcp").status_code == 401
    assert client.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/mcp", headers={"Authorization": "Basic " + "t" * 32}).status_code == 401
    assert seen == []
    assert client.post("/mcp", headers={"Authorization": "Bearer " + "t" * 32}).status_code == 200
    with pytest.raises(ValueError):
        BearerGate(inner, "")
    with pytest.raises(ValueError):
        BearerGate(inner, "short")


def test_mcp_http_app_requires_token_and_serves_tools(tmp_path, monkeypatch):
    from app.mcp_server import BearerGate, build_mcp

    env = Env(tmp_path, monkeypatch)
    app = BearerGate(build_mcp(env.service).http_app(path="/mcp", transport="streamable-http"), "t" * 32)
    with TestClient(app) as client:
        init = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
        }
        accept = {"Accept": "application/json, text/event-stream"}
        assert client.post("/mcp", json=init, headers=accept).status_code == 401
        good = client.post("/mcp", json=init, headers={**accept, "Authorization": "Bearer " + "t" * 32})
        assert good.status_code == 200
        assert "ai-digital-patient-trusted-tools" in good.text


def test_second_specialty_uses_the_same_trusted_chain(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, select("f_fever", final="考虑阑尾炎"))
    sid = env.client.post("/api/sessions?case_id=CASE-002", headers=env.headers).json()["session_id"]
    response = env.ask(sid, "有没有发热，体温多少", "r1")
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "自己量过体温，37.8℃，有点发热。"
    assert "阑尾" not in response.text
    session = env.repository.get(sid, "demo-integration")
    assert session.case_id == "APPY-001"
    assert session.events[0].result["adapter"]["source"] == "nexent"


def test_compact_feedback_binds_sealed_references_without_model_ids(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, agent=select("f_pain_character"), feedback_protocol="compact")
    sid = _play_and_seal(env)
    original = env.repository.get(sid, env.headers["X-Owner-Id"])
    case = CaseStore(ROOT / "cases").get(original.case_id, original.case_version)

    def agent(_, body, service):
        assert "use_stage_basis=True" in body["query"]
        normalized_query = "".join(body["query"].casefold().split())
        assert all(term not in normalized_query for term in diagnosis_leak_terms(case))
        assert all(stage in body["query"] for stage in FEEDBACK_STAGES)
        items = [
            {"stage": stage, "comment": "复核本环节记录。", "improvement": "补充可核对的依据。"}
            for stage in FEEDBACK_STAGES
        ]
        invalid = [dict(items[0], basis_ids=["bad"]), *items[1:]]
        assert service.submit_feedback(token_of(body), invalid, True)["error"] == "invalid_feedback"
        assert service.submit_feedback(token_of(body), items, True) == {"ok": True, "accepted_items": 5}
        return ok_response("反馈已提交")

    env.agent = agent
    endpoint = f"/api/sessions/{sid}/feedback"
    response = env.client.post(endpoint, headers=env.headers, json={"request_id": "compact"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["source"] == "nexent"
    for item in result["items"]:
        assert item["basis_ids"]
        for basis_id in item["basis_ids"]:
            assert item["stage"] in env.service.knowledge.entries[basis_id].stages
        expected = {
            event_id for score in original.score_snapshot.items if score.stage == item["stage"]
            for event_id in score.evidence_event_ids
        }
        assert set(item["evidence_event_ids"]) == expected
    assert env.client.get(endpoint, headers=env.headers).json() == result
    assert env.client.post(endpoint, headers=env.headers, json={"request_id": "compact"}).json() == result
    assert env.repository.get(sid, env.headers["X-Owner-Id"]) == original

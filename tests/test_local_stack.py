"""Local rehearsal over real sockets: backend -> fake Nexent (HTTP/SSE) -> trusted MCP server (HTTP + bearer).

Everything here is local. The "agent" is a scripted fake that speaks the real MCP protocol to the real
trusted MCP service. It is a protocol rehearsal, not evidence of a real Nexent instance or model.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from app import main
from app.case_store import CaseStore
from app.integration.config import IntegrationSettings
from app.integration.knowledge import KnowledgeStore
from app.integration.runtime import IntegrationRuntime
from app.integration.tools import FEEDBACK_STAGES, TrustedToolService
from app.integration.turns import TurnStore
from app.mcp_server import BearerGate, build_mcp
from app.state_machine import StateMachine
from app.storage import SessionRepository

ROOT = Path(__file__).parents[1]
NEXENT_KEY = "rehearsal-nexent-key-0000"
MCP_TOKEN = "rehearsal-mcp-token-000000000000"


class LiveServer:
    def __init__(self, app):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 15
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.02)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def sse(*events):
    for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def build_fake_nexent(mcp_url: str, script: dict):
    app = FastAPI()
    log: list[dict] = []

    async def drive(role: str, token: str):
        transport = StreamableHttpTransport(mcp_url, headers={"Authorization": f"Bearer {MCP_TOKEN}"})
        async with Client(transport) as client:
            if role == "patient":
                context = (await client.call_tool("get_turn_context", {"turn_token": token})).data
                chosen = [c["fact_id"] for c in context["candidates"][: script.get("take", 1)]]
                await client.call_tool("submit_fact_selection", {"turn_token": token, "fact_ids": chosen})
            else:
                review = (await client.call_tool("get_sealed_review", {"turn_token": token})).data
                basis = (await client.call_tool(
                    "search_teaching_basis", {"turn_token": token, "query": "心电图 优先", "stage": "检查选择"}
                )).data
                ecg_event = next(e["event_id"] for e in review["events"] if e["action"] == "order_exam")
                await client.call_tool(
                    "submit_feedback",
                    {
                        "turn_token": token,
                        "items": [
                            {
                                "stage": stage,
                                "comment": "请结合本环节的评分与操作记录复核。",
                                "improvement": "补充本环节尚缺失的依据并复核操作时间。",
                                "evidence_event_ids": [ecg_event],
                                "basis_ids": ["kb_feedback_principles"],
                            }
                            for stage in FEEDBACK_STAGES
                        ],
                    },
                )

    @app.post("/nb/v1/chat/run")
    async def run(request: Request, authorization: str | None = Header(default=None)):
        if authorization != f"Bearer {NEXENT_KEY}":
            raise HTTPException(status_code=401, detail="bad key")
        body = await request.json()
        log.append(body)
        if script.get("hang"):
            await asyncio.sleep(script["hang"])
        meta = body["metadata"]
        await drive(meta["role"], meta["turn_token"])
        return StreamingResponse(
            sse(
                {"type": "model_output_thinking", "content": "…", "unit_index": 1},
                {"type": "final_answer", "content": "自由文本，不会展示给学生 STEMI", "unit_index": 2},
            ),
            media_type="text/event-stream",
            headers={"conversation_id": "101"},
        )

    @app.get("/nb/v1/chat/stop/{conversation_id}")
    def stop(conversation_id: int):
        log.append({"stop": conversation_id})
        return {"message": "success"}

    return app, log


@pytest.fixture()
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    repository = SessionRepository(tmp_path / "sessions.db")
    cases = CaseStore(ROOT / "cases")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(cases, repository))
    base = IntegrationSettings(
        mode="nexent", nexent_base_url="", nexent_api_key=NEXENT_KEY, patient_agent_name="digital_patient",
        feedback_agent_name="feedback_reviewer", connect_timeout_s=2.0, first_event_timeout_s=1.5,
        total_timeout_s=10.0, turn_ttl_s=60.0, mcp_service_token=MCP_TOKEN,
        integration_db_path=tmp_path / "integration.db", knowledge_dir=ROOT / "knowledge",
    )
    # Build the trusted service on its own handles to the same files, as a separate MCP process would.
    service = TrustedToolService(
        TurnStore(base.integration_db_path),
        SessionRepository(tmp_path / "sessions.db"),
        CaseStore(ROOT / "cases"),
        KnowledgeStore(ROOT / "knowledge"),
    )
    mcp_app = BearerGate(build_mcp(service).http_app(path="/mcp", transport="streamable-http"), MCP_TOKEN)
    script: dict = {}
    with LiveServer(mcp_app) as mcp_server:
        fake, log = build_fake_nexent(f"http://127.0.0.1:{mcp_server.port}/mcp", script)
        with LiveServer(fake) as nexent_server:
            settings = IntegrationSettings(**{**base.__dict__, "nexent_base_url": f"http://127.0.0.1:{nexent_server.port}"})
            monkeypatch.setattr(main, "RUNTIME", IntegrationRuntime(settings))
            yield (
                TestClient(main.app), {"X-Owner-Id": "demo-stack"}, script, log, repository,
                f"http://127.0.0.1:{mcp_server.port}/mcp",
            )


def test_full_loop_over_real_sockets(stack):
    client, headers, script, log, repository, _ = stack
    sid = client.post("/api/sessions?case_id=CASE-001", headers=headers).json()["session_id"]

    asked = client.post(
        f"/api/sessions/{sid}/ask", headers=headers, json={"request_id": "q1", "question": "请问既往史和用药情况"}
    )
    assert asked.status_code == 200, asked.text
    assert asked.json()["answer"] == "有高血压10年。"
    assert "STEMI" not in asked.text

    for rid, action, payload in (
        ("o1", "order_exam", {"exam_id": "ex_ecg"}),
        ("w1", "wait", {"minutes": 5}),
        ("d1", "submit_diagnosis", {"diagnosis": "STEMI"}),
        ("t1", "execute_treatment", {"treatment_id": "reperfusion_pathway", "confirm_execution": True}),
    ):
        res = client.post(f"/api/sessions/{sid}/action", headers=headers, json={"request_id": rid, "action": action, "payload": payload})
        assert res.status_code == 200, res.text
    assert client.post(f"/api/sessions/{sid}/seal", headers=headers, json={"request_id": "s"}).status_code == 200
    blocked = client.post(f"/api/sessions/{sid}/ask", headers=headers, json={"request_id": "late", "question": "有糖尿病吗"})
    assert blocked.status_code == 409

    feedback = client.post(f"/api/sessions/{sid}/feedback", headers=headers, json={"request_id": "f1"})
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["source"] == "nexent"
    assert [r.get("metadata", {}).get("role") for r in log if "metadata" in r] == ["patient", "feedback"]

    state = repository.get(sid, "demo-stack")
    trace = state.events[0].result["adapter"]["trace"]
    assert trace["conversation_id"] == 101
    assert [c["tool"] for c in trace["tool_calls"]] == ["get_turn_context", "submit_fact_selection"]
    assert trace["first_event_ms"] is not None


def test_slow_platform_times_out_and_registers_nothing(stack):
    client, headers, script, log, repository, _ = stack
    sid = client.post("/api/sessions?case_id=CASE-001", headers=headers).json()["session_id"]
    script["hang"] = 4
    started = time.time()
    response = client.post(f"/api/sessions/{sid}/ask", headers=headers, json={"request_id": "q1", "question": "有高血压吗"})
    assert response.status_code == 504
    assert time.time() - started < 4
    assert repository.get(sid, "demo-stack").events == []
    script["hang"] = 0
    retry = client.post(f"/api/sessions/{sid}/ask", headers=headers, json={"request_id": "q1", "question": "有高血压吗"})
    assert retry.status_code == 200 and retry.json()["answer"] == "有高血压10年。"


def test_mcp_service_rejects_missing_or_wrong_bearer(stack):
    import httpx

    *_, mcp_url = stack
    init = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    }
    accept = {"Accept": "application/json, text/event-stream"}
    assert httpx.post(mcp_url, json=init, headers=accept).status_code == 401
    assert httpx.post(mcp_url, json=init, headers={**accept, "Authorization": "Bearer nope"}).status_code == 401
    good = httpx.post(mcp_url, json=init, headers={**accept, "Authorization": f"Bearer {MCP_TOKEN}"})
    assert good.status_code == 200

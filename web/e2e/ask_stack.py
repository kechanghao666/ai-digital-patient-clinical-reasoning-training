"""Isolated local stack for the saved-ask browser regression.

Browser -> FastAPI -> fake Nexent -> trusted MCP, all on loopback sockets. The
case store is the repository's synthetic teaching cases; every database lives in
a temporary directory. No real model endpoint or credential is read or used.

Run from the ask-saved-answer.mjs driver. The READY line contains the API and
fake-Nexent control base URLs. POST /__control accepts {"hang": seconds}; GET
/__control reports local fake calls for evidence.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

NEXENT_KEY = "ask-regression-local-key"
MCP_TOKEN = "ask-regression-local-mcp-token"


class LiveServer:
    def __init__(self, app):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.time() + 20
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("local server did not start")
            time.sleep(0.02)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self


def sse(*events):
    for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def build_fake_nexent(mcp_url: str, script: dict):
    app = FastAPI()

    async def drive(role: str, token: str):
        if role != "patient":
            return
        transport = StreamableHttpTransport(mcp_url, headers={"Authorization": f"Bearer {MCP_TOKEN}"})
        async with Client(transport) as client:
            context = (await client.call_tool("get_turn_context", {"turn_token": token})).data
            chosen = [c["fact_id"] for c in context["candidates"][: script.get("take", 1)]]
            await client.call_tool("submit_fact_selection", {"turn_token": token, "fact_ids": chosen})

    @app.post("/nb/v1/chat/run")
    async def run(request: Request, authorization: str | None = Header(default=None)):
        if authorization != f"Bearer {NEXENT_KEY}":
            raise HTTPException(status_code=401, detail="bad local key")
        script["calls"] += 1
        body = await request.json()
        if script.get("hang"):
            await asyncio.sleep(script["hang"])
        await drive(body["metadata"]["role"], body["metadata"]["turn_token"])
        return StreamingResponse(
            sse(
                {"type": "model_output_thinking", "content": "local fake", "unit_index": 1},
                {"type": "final_answer", "content": "untrusted free text must not be shown", "unit_index": 2},
            ),
            media_type="text/event-stream",
            headers={"conversation_id": "local-ask-regression"},
        )

    @app.get("/__control")
    async def control_status():
        return dict(script)

    @app.post("/__control")
    async def control(request: Request):
        patch = await request.json()
        for key in ("hang", "take"):
            if key in patch:
                script[key] = patch[key]
        return dict(script)

    return app


def main():
    os.environ["APP_ENV"] = "development"
    os.environ["APP_AUTH_MODE"] = "demo"
    data = tempfile.mkdtemp(prefix="ask-saved-answer-")
    os.environ["APP_DATA_DIR"] = data
    os.environ["INTEGRATION_DB_PATH"] = str(Path(data) / "integration.db")

    from app import main as app_main
    from app.department_config import save_department_config
    save_department_config(Path(data) / "department-catalog.json", {"03", "04"},
                           {"03.01", "03.02", "03.03", "03.04", "03.05", "03.07", "04.01"},
                           "synthetic-browser-e2e")
    from app.case_store import CaseStore
    from app.integration.config import IntegrationSettings
    from app.integration.knowledge import KnowledgeStore
    from app.integration.runtime import IntegrationRuntime
    from app.integration.tools import TrustedToolService
    from app.integration.turns import TurnStore
    from app.mcp_server import BearerGate, build_mcp
    from app.state_machine import StateMachine
    from app.storage import SessionRepository

    cases = CaseStore(ROOT / "cases")
    repository = SessionRepository(Path(data) / "sessions.db")
    app_main.REPOSITORY = repository
    app_main.MACHINE = StateMachine(cases, repository)
    service = TrustedToolService(
        TurnStore(Path(data) / "integration.db"), repository, cases, KnowledgeStore(ROOT / "knowledge")
    )
    mcp_app = BearerGate(build_mcp(service).http_app(path="/mcp", transport="streamable-http"), MCP_TOKEN)
    mcp_server = LiveServer(mcp_app).start()

    script = {"hang": 0, "take": 1, "calls": 0}
    fake = build_fake_nexent(f"http://127.0.0.1:{mcp_server.port}/mcp", script)
    nexent_server = LiveServer(fake).start()
    settings = IntegrationSettings(
        mode="nexent",
        nexent_base_url=f"http://127.0.0.1:{nexent_server.port}",
        nexent_api_key=NEXENT_KEY,
        patient_agent_name="digital_patient",
        feedback_agent_name="feedback_reviewer",
        connect_timeout_s=2.0,
        first_event_timeout_s=1.5,
        total_timeout_s=10.0,
        turn_ttl_s=60.0,
        mcp_service_token=MCP_TOKEN,
        integration_db_path=Path(data) / "integration.db",
        knowledge_dir=ROOT / "knowledge",
    )
    app_main.RUNTIME = IntegrationRuntime(settings)
    api_server = LiveServer(app_main.app).start()
    print(
        f"READY http://127.0.0.1:{api_server.port} http://127.0.0.1:{nexent_server.port}",
        flush=True,
    )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        for server in (api_server, nexent_server, mcp_server):
            server.server.should_exit = True


if __name__ == "__main__":
    main()

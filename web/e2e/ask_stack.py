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
import shutil
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


def build_fake_nexent(mcp_url: str, script: dict, shutdown: threading.Event, repository=None):
    app = FastAPI()
    # Non-serializable barrier control for the controlled-wait browser probe. ``pause_at`` is one of
    # "before_submission" / "after_submission"; "after_validation" is wired in main() on the backend.
    barrier = {"pause_at": None, "paused": threading.Event(), "release": threading.Event()}

    async def _pause(position: str) -> None:
        if barrier["pause_at"] == position:
            barrier["paused"].set()
            await asyncio.to_thread(barrier["release"].wait)
            barrier["paused"].clear()

    async def drive(role: str, token: str):
        if role != "patient":
            return
        transport = StreamableHttpTransport(mcp_url, headers={"Authorization": f"Bearer {MCP_TOKEN}"})
        async with Client(transport) as client:
            context = (await client.call_tool("get_turn_context", {"turn_token": token})).data
            chosen = [c["fact_id"] for c in context["candidates"][: script.get("take", 1)]]
            await _pause("before_submission")
            await client.call_tool("submit_fact_selection", {"turn_token": token, "fact_ids": chosen})
            await _pause("after_submission")

    @app.post("/nb/v1/chat/run")
    async def run(request: Request, authorization: str | None = Header(default=None)):
        if authorization != f"Bearer {NEXENT_KEY}":
            raise HTTPException(status_code=401, detail="bad local key")
        script["calls"] += 1
        body = await request.json()
        role = body["metadata"]["role"]
        token = body["metadata"]["turn_token"]

        async def stream():
            if script.get("hang"):
                await asyncio.sleep(script["hang"])
            # Establish the SSE stream with a thinking event *before* the model tool calls, so the
            # after_submission barrier sits after the response headers/stream exist (like the real
            # platform), not before the stream is returned.
            yield f"data: {json.dumps({'type': 'model_output_thinking', 'content': 'local fake', 'unit_index': 1}, ensure_ascii=False)}\n\n".encode()
            await drive(role, token)
            yield f"data: {json.dumps({'type': 'execution_result', 'content': 'submitted', 'unit_index': 2}, ensure_ascii=False)}\n\n".encode()
            yield f"data: {json.dumps({'type': 'final_answer', 'content': 'untrusted free text must not be shown', 'unit_index': 3}, ensure_ascii=False)}\n\n".encode()

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"conversation_id": "local-ask-regression"}
        )

    @app.get("/__control")
    async def control_status():
        return {"hang": script.get("hang"), "take": script.get("take"), "calls": script.get("calls"),
                "pause_at": barrier["pause_at"], "paused": barrier["paused"].is_set()}

    @app.post("/__control")
    async def control(request: Request):
        patch = await request.json()
        for key in ("hang", "take"):
            if key in patch:
                script[key] = patch[key]
        if "pause_at" in patch:
            barrier["pause_at"] = patch["pause_at"]
            barrier["release"].clear()
            barrier["paused"].clear()
        if patch.get("release"):
            barrier["release"].set()
        return {"pause_at": barrier["pause_at"], "paused": barrier["paused"].is_set()}

    @app.post("/__shutdown")
    async def shutdown_endpoint():
        # Graceful stop so main() can release server threads and delete the temporary data directory.
        shutdown.set()
        return {"shutting_down": True}

    @app.get("/__trace/{session_id}")
    async def trace(session_id: str):
        # Raw adapter trace of the latest ask event; safe_event strips it from the student API, so the
        # probe reads it here (isolated loopback, temporary DB only).
        if repository is None:
            return {}
        try:
            state = repository.get(session_id, "demo-controlled-wait")
        except KeyError:
            return {}
        for event in reversed(state.events):
            result = event.result if isinstance(event.result, dict) else {}
            adapter = result.get("adapter")
            if isinstance(adapter, dict):
                return adapter
        return {}

    return app, barrier


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
    shutdown = threading.Event()
    fake, barrier = build_fake_nexent(f"http://127.0.0.1:{mcp_server.port}/mcp", script, shutdown, repository)
    nexent_server = LiveServer(fake).start()

    # Wire the "after_validation" pause into the backend's answer authorization gate. main.py
    # imports authorized_answer_or_fallback by name, so patching main's module global is enough.
    original = app_main.authorized_answer_or_fallback

    def gated(*args, **kwargs):
        result = original(*args, **kwargs)
        if barrier["pause_at"] == "after_validation":
            barrier["paused"].set()
            barrier["release"].wait()
            barrier["paused"].clear()
        return result

    app_main.authorized_answer_or_fallback = gated

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
        f"READY http://127.0.0.1:{api_server.port} http://127.0.0.1:{nexent_server.port} {data}",
        flush=True,
    )
    try:
        shutdown.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for server in (api_server, nexent_server, mcp_server):
            server.server.should_exit = True
        # Best-effort removal of this run's temporary databases and config; never touches the
        # repository data directories or any historical database.
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    main()

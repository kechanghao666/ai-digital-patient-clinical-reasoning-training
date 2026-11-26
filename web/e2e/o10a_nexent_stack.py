"""Isolated stack for measuring a REAL Nexent patient agent from the browser (O10a).

Browser -> FastAPI (NEXENT_MODE=nexent) -> the configured Nexent instance -> the trusted MCP
server (get_turn_context / submit_fact_selection) running here in-process. Every database lives in
a temporary directory owned by this process. No simulator and no
loopback fake Nexent are started; a missing base URL / API key / patient agent fails fast.

Run from web/e2e/o10a-latency.mjs (set NEXENT_MODE=nexent). The READY line contains the API base
URL. Real model calls happen only once a student question is sent through the browser.

Required environment (from the protected service config, never from source):
  NEXENT_BASE_URL, NEXENT_API_KEY, NEXENT_PATIENT_AGENT, MCP_SERVICE_TOKEN
Optional:
  MCP_PORT (default 8765; must match the address the Nexent platform uses to reach this MCP server),
  NEXENT_PATIENT_PROTOCOL (default dual), NEXENT_TOTAL_TIMEOUT_S.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn


class LiveServer:
    def __init__(self, app, port: int):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.time() + 30
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("local server did not start")
            time.sleep(0.02)
        return self

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} 未配置；真实 Nexent 测量必须从受保护服务配置加载")
    return value


def main():
    os.environ["APP_ENV"] = "development"
    os.environ["APP_AUTH_MODE"] = "demo"
    os.environ.setdefault("NEXENT_MODE", "nexent")
    _required("NEXENT_BASE_URL")
    _required("NEXENT_API_KEY")
    _required("NEXENT_PATIENT_AGENT")
    _required("MCP_SERVICE_TOKEN")

    with tempfile.TemporaryDirectory(prefix="o10a-nexent-") as data:
        serve(data)


def serve(data: str):
    os.environ["APP_DATA_DIR"] = data
    os.environ["INTEGRATION_DB_PATH"] = str(Path(data) / "integration.db")
    os.environ["NEXENT_PATIENT_PROTOCOL"] = "dual"

    from app import main as app_main
    from app.department_config import save_department_config
    save_department_config(
        Path(os.environ["APP_DATA_DIR"]) / "department-catalog.json",
        {"03", "04"},
        {"03.01", "03.02", "03.03", "03.04", "03.05", "03.07", "04.01"},
        "synthetic-o10a-nexent",
    )

    # Trusted MCP service, in-process, on the fixed port the Nexent platform reaches.
    # The API's own RUNTIME reads NEXENT_* from the environment (NEXENT_MODE=nexent) lazily, so no
    # simulator or loopback fake Nexent is ever started here.
    from app.integration.config import IntegrationSettings
    from app.mcp_server import build_asgi_app

    mcp_port = int(os.getenv("MCP_PORT", "8765"))
    mcp_app = build_asgi_app(IntegrationSettings.from_env())
    mcp_server = LiveServer(mcp_app, mcp_port).start()

    api_server = LiveServer(app_main.app, 0).start()
    api_port = api_server.server.servers[0].sockets[0].getsockname()[1]
    print(f"READY http://127.0.0.1:{api_port}", flush=True)
    try:
        sys.stdin.read()
    except KeyboardInterrupt:
        pass
    finally:
        for server in (api_server, mcp_server):
            server.stop()


if __name__ == "__main__":
    main()

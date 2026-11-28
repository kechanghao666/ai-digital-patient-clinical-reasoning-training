"""Isolated simulator-mode stack for the O10a click-to-visible latency measurement.

Browser -> FastAPI (NEXENT_MODE=simulator), all on loopback. The case store is the
repository's synthetic teaching cases; every database lives in a temporary directory.
No real model endpoint or credential is read or used.

Run from web/e2e/o10a-latency.mjs. The READY line contains the API base URL.
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

    def stop(self):
        self.server.should_exit = True


def main():
    os.environ["APP_ENV"] = "development"
    os.environ["APP_AUTH_MODE"] = "demo"
    os.environ["NEXENT_MODE"] = "simulator"
    data = tempfile.mkdtemp(prefix="o10a-latency-")
    os.environ["APP_DATA_DIR"] = data
    os.environ["INTEGRATION_DB_PATH"] = str(Path(data) / "integration.db")

    from app import main as app_main
    from app.department_config import save_department_config
    save_department_config(
        Path(data) / "department-catalog.json",
        {"03", "04"},
        {"03.01", "03.02", "03.03", "03.04", "03.05", "03.07", "04.01"},
        "synthetic-o10a",
    )
    server = LiveServer(app_main.app).start()
    print(f"READY http://127.0.0.1:{server.port}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()

"""Run the complete teacher/student browser workflow in a fresh local simulator.

Usage: .venv/Scripts/python.exe web/e2e/demo.py [fresh-output-directory]
Uses the built web frontend and an installed Edge/Chrome. No existing server,
database or account is used. Temporary data and the local API are closed on exit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

from o10a_stack import LiveServer, ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?")
    parser.add_argument("--record", action="store_true", help="Record a 3–8 minute simulator demo using installed ffmpeg")
    args = parser.parse_args()
    node = shutil.which("node")
    browser = next((p for p in [
        os.environ.get("CHROME", ""),
        "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
    ] if p and Path(p).is_file()), None)
    if not node or not browser:
        raise RuntimeError("Install Node and Edge/Chrome, or set CHROME to an installed browser")
    if not (ROOT / "web/dist/index.html").is_file():
        raise RuntimeError("Build the frontend first: cd web; npm ci; npm run build")
    ffmpeg = shutil.which("ffmpeg") if args.record else None
    if args.record and not ffmpeg:
        raise RuntimeError("Recording requires an existing ffmpeg installation on PATH")
    out = (Path(args.output).resolve() if args.output else
           ROOT / "local-only" / ("isolated-demo-" + secrets.token_hex(6)))
    out.mkdir(parents=True, exist_ok=False)
    evidence = {"mode": "simulator", "auth": "local", "real_model_calls": 0,
                "isolated_data": True, "temporary_data_removed": False,
                "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "dirty_worktree": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
                "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                  ("web/e2e/demo.py", "web/e2e/smoke.mjs", "web/e2e/o10a_stack.py",
                                   "web/dist/index.html", "web/dist/assets/main.js")}}
    temporary = tempfile.TemporaryDirectory(prefix="patient-browser-demo-")
    data = temporary.name
    try:
        # Drop inherited provider settings so optional services cannot call a real model.
        for key in list(os.environ):
            if key.startswith(("NEXENT_", "EMBEDDING_", "RERANK_", "OCR_", "MCP_")):
                del os.environ[key]
        password = secrets.token_urlsafe(24)
        os.environ.update(APP_ENV="development", APP_AUTH_MODE="local", NEXENT_MODE="simulator",
                          APP_DATA_DIR=data, INTEGRATION_DB_PATH=str(Path(data) / "integration.db"),
                          KNOWLEDGE_DIR=str(ROOT / "knowledge"), WEB_DIST_DIR=str(ROOT / "web/dist"))
        if args.record:
            manifest = json.loads((ROOT / "web/node_modules/playwright-core/browsers.json").read_text("utf-8"))
            revision = next(item["revision"] for item in manifest["browsers"] if item["name"] == "ffmpeg")
            runtime = Path(data) / "browser-runtime"
            binary = runtime / f"ffmpeg-{revision}" / ("ffmpeg-win64.exe" if sys.platform == "win32" else
                                                       "ffmpeg-mac" if sys.platform == "darwin" else "ffmpeg-linux")
            binary.parent.mkdir(parents=True)
            shutil.copy2(ffmpeg, binary)
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(runtime)
        from app import main as app_main
        from app.department_config import save_department_config
        save_department_config(Path(data) / "department-catalog.json", {"03", "04"},
                               {"03.01", "03.02", "03.03", "03.04", "03.05", "03.07", "04.01"},
                               "synthetic-browser-demo")
        app_main.ACCOUNTS.create("demo_teacher", password, "teacher", "合成演示教师")
        assert Path(app_main.REPOSITORY.database_path).is_relative_to(Path(data))
        assert app_main.RUNTIME.settings.mode == "simulator"
        server = LiveServer(app_main.app)
        try:
            server.start()
            env = dict(os.environ, BASE=f"http://127.0.0.1:{server.port}", OUT=str(out),
                       TEACHER="demo_teacher", TEACHER_PW=password, CHROME=browser,
                       RECORD_DEMO="1" if args.record else "0")
            result = subprocess.run([node, str(ROOT / "web/e2e/smoke.mjs")], cwd=ROOT, env=env,
                                    timeout=480, capture_output=True, text=True, encoding="utf-8")
            (out / "browser.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            print(result.stdout, end="")
            evidence["browser_exit_code"] = result.returncode
            if (out / "run.json").is_file():
                run = json.loads((out / "run.json").read_text(encoding="utf-8"))
                evidence["checks_passed"] = sum(step["ok"] for step in run["steps"])
                evidence["checks_total"] = len(run["steps"])
                assert result.returncode or all(step["ok"] for step in run["steps"])
            returncode = result.returncode
        finally:
            server.stop()
            server.thread.join(timeout=15)
            assert not server.thread.is_alive(), "Temporary API failed to shut down"
    except Exception as exc:
        evidence["error_type"] = type(exc).__name__
        raise
    finally:
        try:
            temporary.cleanup()
        finally:
            evidence["temporary_data_removed"] = not Path(data).exists()
            (out / "isolation.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    assert evidence["temporary_data_removed"]
    print(f"Evidence: {out}")
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())

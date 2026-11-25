"""Run a real-browser department-catalog probe using temporary synthetic state only."""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / "docs" / "evidence" / "2026-11-25-department-catalog-browser.json"


def safe_process_environment() -> dict[str, str]:
    # Pass only OS process-launch variables, never inherited model/service credentials.
    result = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
    result["PYTHONUTF8"] = "1"
    return result


def find_browser(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.is_file():
            return path
        raise FileNotFoundError("指定浏览器可执行文件不存在")
    candidates = (
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    )
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("未找到 Edge/Chrome；请通过 --browser 指定可执行文件")


def safe_browser_diagnostic(output: str, passwords: tuple[str, ...]) -> str:
    """Keep a short, credential-scrubbed browser error without server logs."""
    for password in passwords:
        output = output.replace(password, "[REDACTED]")
    output = re.sub(r"(?i)bearer\s+[^\s\"']+", "Bearer [REDACTED]", output)
    output = re.sub(
        r"(?i)(password|token|secret|api[_-]?key)([\"'\s:=]+)[^,\s\"'}`]+",
        r"\1\2[REDACTED]",
        output,
    )
    prefix = "browser check failed: "
    lines = output.splitlines()
    for line in lines:
        if prefix in line:
            return line.split(prefix, 1)[1].strip()[:160]
    timeout_line = next((line.strip() for line in lines if "Timeout" in line), None)
    waiting_line = next((line.strip() for line in lines if "waiting for" in line), None)
    if timeout_line:
        return " | ".join(part for part in (timeout_line, waiting_line) if part)[:360]
    for line in lines:
        if any(marker in line for marker in ("Error:", "ERR_", "Cannot find", "not found")):
            return line.strip()[:240]
    return "no classified child diagnostic"


def wait_for_health(base_url: str, process: subprocess.Popen, log_path: Path) -> dict:
    deadline = time.monotonic() + 30
    last_error = "未收到健康响应"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            with urlopen(base_url + "/health", timeout=1) as response:
                if response.status == 200:
                    health = json.loads(response.read().decode("utf-8"))
                    if health.get("status") == "ok":
                        return health
        except (OSError, URLError, TimeoutError) as exc:
            last_error = type(exc).__name__
        time.sleep(0.25)
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    raise RuntimeError(f"隔离服务未通过健康检查：{last_error}; server_log_bytes={len(log)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Edge/Chrome executable; defaults to the installed Edge path")
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()

    evidence_path = args.evidence.resolve()
    browser_path = find_browser(args.browser)
    node = shutil.which("node")
    if not node:
        raise SystemExit("node 未安装或不在 PATH 中")

    browser_result = None
    health = None
    failure = None
    with tempfile.TemporaryDirectory(prefix="department-catalog-browser-") as temp_name:
        temp_root = Path(temp_name)
        data_dir = temp_root / "data"
        integration_path = temp_root / "runtime" / "integration.db"
        data_dir.mkdir()
        integration_path.parent.mkdir()

        child_env = safe_process_environment()
        child_env.update({
            "APP_ENV": "development",
            "APP_AUTH_MODE": "local",
            "APP_DATA_DIR": str(data_dir),
            "INTEGRATION_DB_PATH": str(integration_path),
            "NEXENT_MODE": "simulator",
            "KNOWLEDGE_DIR": str(ROOT / "knowledge"),
        })
        for name in (
            "NEXENT_API_KEY", "NEXENT_BASE_URL", "NEXENT_PATIENT_AGENT", "NEXENT_FEEDBACK_AGENT",
            "MCP_SERVICE_TOKEN", "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL",
            "RERANK_API_KEY", "RERANK_BASE_URL", "RERANK_MODEL", "OCR_API_KEY", "OCR_BASE_URL", "OCR_MODEL",
        ):
            child_env.pop(name, None)

        sys.path.insert(0, str(ROOT / "backend"))
        os.environ.update(child_env)
        from app.accounts import AccountStore
        from app.department_config import save_department_config

        save_department_config(
            data_dir / "department-catalog.json",
            {"03", "04", "05"},
            {"03.01", "03.02", "03.03", "03.04", "03.05", "03.07", "04.01", "05.01"},
            "synthetic-browser-operator",
        )
        teacher_password = secrets.token_urlsafe(24)
        student_password = secrets.token_urlsafe(24)
        accounts = AccountStore(data_dir / "accounts.db")
        accounts.create("catalog-teacher", teacher_password, "teacher", "合成教师")
        accounts.create("catalog-student", student_password, "student", "合成学生")

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        log_path = temp_root / "uvicorn.log"
        with log_path.open("w", encoding="utf-8") as log_stream:
            server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT / "backend"),
                 "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT,
                env=child_env,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
            )
            try:
                health = wait_for_health(base_url, server, log_path)
                auth_config = json.loads(urlopen(base_url + "/api/auth/config", timeout=3).read().decode("utf-8"))
                if auth_config.get("mode") != "local" or health.get("patient_adapter") != "simulator":
                    raise RuntimeError("隔离服务未使用 local 认证和本地模拟器")
                browser_env = dict(child_env)
                browser_env.update({
                    "BASE": base_url,
                    "BROWSER_BIN": str(browser_path),
                    "E2E_TEACHER_PASSWORD": teacher_password,
                    "E2E_STUDENT_PASSWORD": student_password,
                })
                result = subprocess.run(
                    [node, "e2e/department-catalog.mjs"],
                    cwd=ROOT / "web",
                    env=browser_env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                if result.returncode != 0:
                    failure = {
                        "kind": "browser_process_failed",
                        "exit_code": result.returncode,
                        "diagnostic": safe_browser_diagnostic(
                            result.stdout + "\n" + result.stderr,
                            (teacher_password, student_password),
                        ),
                    }
                else:
                    marker = next((line[len("BROWSER_RESULT="): ] for line in result.stdout.splitlines()
                                   if line.startswith("BROWSER_RESULT=")), None)
                    if not marker:
                        failure = {"kind": "browser_result_missing", "exit_code": result.returncode}
                    else:
                        browser_result = json.loads(marker)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)

    git_state = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT, text=True
    ).strip()
    evidence = {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "worktree_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "worktree_clean": not bool(git_state),
        "operating_system": platform.platform(),
        "python": platform.python_version(),
        "node": subprocess.check_output([node, "--version"], cwd=ROOT, text=True).strip(),
        "browser_executable": browser_path.name,
        "application_mode": {"APP_ENV": "development", "APP_AUTH_MODE": "local", "NEXENT_MODE": "simulator"},
        "synthetic_enablement": {"department_count": 3, "subspecialty_count": 8},
        "health": health,
        "browser_result": browser_result,
        "failure": failure,
        "temporary_data_removed": True,
        "evidence_boundary": "仅本机临时目录、合成账号和仓库草稿夹具的真实页面操作；未保存教师分类选择。生产草稿隐藏/发布/API 开训门禁由 tests/test_department_catalog.py 的合成认证回归验证。本证据不代表生产部署、临床审核或真实学生试用。",
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "evidence_path": str(evidence_path.relative_to(ROOT)),
        "browser_result": browser_result,
        "failure": failure,
        "temporary_data_removed": True,
    }, ensure_ascii=False))
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())

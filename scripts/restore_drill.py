#!/usr/bin/env python3
"""M5/M6 recovery drill: offline backup → isolated restore → the restored system keeps teaching.

Scope is deliberately explicit: this drill runs on ONE machine, restoring into a NEW directory of
the same machine. It is not a container migration or a cross-machine restore.

Phases:
1. Build a synthetic source environment (teacher, class, assignment, one sealed training with rule
   scores and feedback) in a temporary APP_DATA_DIR.
2. Stop all writers, create the offline allowlist backup, restore it into a second new directory.
3. Launch the restored application as a subprocess and verify over real HTTP with authenticated
   accounts: the teacher sees the original class, the student sees the original assignment, the
   case version and training id match, the sealed score and feedback are identical, the sealed
   session still refuses writes — and a NEW training can be started and answered (teaching
   continues after recovery).
4. Failure drill: a tampered backup manifest is refused and leaves the intended target absent.

All data is synthetic; the simulator issues no real model calls and the business database is never
touched. Usage: restore_drill.py OUT-DIRECTORY (must not exist).
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

OUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
if OUT is None or OUT.exists():
    raise SystemExit(f"Usage: restore_drill.py NEW-OUTPUT-DIRECTORY (not existing); got {OUT}")
OUT.mkdir(parents=True)
TIMINGS: dict[str, float] = {}

# ------------------------------------------------------------------ phase 1: source env
SOURCE = OUT / "source-data"
os.environ.update({
    "APP_ENV": "development",
    "APP_AUTH_MODE": "local",
    "APP_DATA_DIR": str(SOURCE),
    "INTEGRATION_DB_PATH": str(SOURCE / "integration.db"),
    "NEXENT_MODE": "simulator",
    "NEXENT_BASE_URL": "", "NEXENT_API_KEY": "", "NEXENT_AGENT_NAME": "",
    "NEXENT_PATIENT_AGENT": "", "NEXENT_FEEDBACK_AGENT": "", "NEXENT_PATIENT_AGENT_SINGLE": "",
    "MCP_SERVICE_TOKEN": "", "EMBEDDING_BASE_URL": "", "EMBEDDING_API_KEY": "", "EMBEDDING_MODEL": "",
    "RERANK_BASE_URL": "", "RERANK_API_KEY": "", "RERANK_MODEL": "",
    "OCR_BASE_URL": "", "OCR_API_KEY": "", "OCR_MODEL": "",
})
import json as _json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402  (import AFTER the env pins the source data directory)
from app.backup_restore import BackupRestoreError, create_backup, restore_backup  # noqa: E402
from app.department_config import save_department_config  # noqa: E402

save_department_config(SOURCE / "department-catalog.json", {"03", "04"},
                       {"03.01", "03.02", "03.03", "03.04", "03.05", "03.07", "04.01"},
                       "synthetic-restore-drill")
client = TestClient(main.app)
password = secrets.token_urlsafe(24)
teacher_id = main.ACCOUNTS.create("drill_teacher", password, "teacher", "恢复演练教师（合成）")
login = client.post("/api/auth/login", json={"username": "drill_teacher", "password": password})
assert login.status_code == 200, login.text
teacher_headers = {"Authorization": "Bearer " + login.json()["token"]}
klass = client.post("/api/teacher/classes", headers=teacher_headers, json={"name": "恢复演练班"}).json()
roster_login_headers = teacher_headers
issued = client.post(f"/api/teacher/classes/{klass['id']}/students", headers=teacher_headers,
                     json={"students": [{"username": "drill_student", "display_name": "恢复演练学生"}]}).json()
assert issued["results"][0]["status"] == "created", issued["results"]
student_password = issued["results"][0]["temporary_password"]
assert student_password, "名册导入未返回初始密码"
assignment = client.post(f"/api/teacher/classes/{klass['id']}/assignments", headers=teacher_headers,
                         json={"case_public_id": "CASE-001", "note": "恢复演练任务"}).json()

student_login = client.post("/api/auth/login", json={"username": "drill_student", "password": student_password})
assert student_login.status_code == 200, student_login.text
changed = client.post("/api/auth/password", headers={"Authorization": "Bearer " + student_login.json()["token"]},
                      json={"current_password": student_password, "new_password": "Drill-Student-2026"})
assert changed.status_code == 200, changed.text
student_token = changed.json()["token"]
student_headers = {"Authorization": "Bearer " + student_token}

started = client.post(f"/api/assignments/{assignment['id']}/start", headers=student_headers).json()
session_id = started["session_id"]
for request_id, question in (("drill-1", "怎么了？"), ("drill-2", "有高血压吗")):
    response = client.post(f"/api/sessions/{session_id}/ask", headers=student_headers,
                           json={"request_id": request_id, "question": question})
    assert response.status_code == 200, response.text
client.post(f"/api/sessions/{session_id}/action", headers=student_headers,
            json={"request_id": "drill-order", "action": "order_exam", "payload": {"exam_id": "ex_ecg"}})
client.post(f"/api/sessions/{session_id}/action", headers=student_headers,
            json={"request_id": "drill-wait", "action": "wait", "payload": {"minutes": 5}})
client.post(f"/api/sessions/{session_id}/action", headers=student_headers,
            json={"request_id": "drill-diagnosis", "action": "submit_diagnosis",
                  "payload": {"diagnosis": "急性ST段抬高型心肌梗死"}})
sealed = client.post(f"/api/sessions/{session_id}/seal", headers=student_headers,
                     json={"request_id": "drill-seal"})
assert sealed.status_code == 200, sealed.text
score_before = client.get(f"/api/sessions/{session_id}/score", headers=student_headers).json()
feedback_before = client.post(f"/api/sessions/{session_id}/feedback", headers=student_headers,
                              json={"request_id": "drill-feedback"}).json()
assert feedback_before["items"], "源环境封存反馈未生成"
client.close()  # stop the only writer

# ------------------------------------------------------ phase 2: backup then restore
BACKUP = OUT / "backup"
started_at = time.monotonic()
manifest = create_backup(SOURCE, SOURCE / "integration.db", BACKUP, confirm_services_stopped=True)
TIMINGS["backup_s"] = round(time.monotonic() - started_at, 2)
RESTORED = OUT / "restored-data"
started_at = time.monotonic()
restore_backup(BACKUP, RESTORED, confirm_services_stopped=True)
TIMINGS["restore_s"] = round(time.monotonic() - started_at, 2)

# ------------------------------------------- phase 3: the restored system keeps teaching
SERVE = OUT / "serve_restored.py"
SERVE.write_text(f'''
"""Subprocess server for the restored data directory (drill-internal)."""
import os, sys
os.environ.update({{
    "APP_ENV": "development", "APP_AUTH_MODE": "local", "APP_DATA_DIR": r"{RESTORED}",
    "INTEGRATION_DB_PATH": r"{RESTORED / 'integration.db'}", "NEXENT_MODE": "simulator",
    "NEXENT_BASE_URL": "", "NEXENT_API_KEY": "", "NEXENT_AGENT_NAME": "",
    "NEXENT_PATIENT_AGENT": "", "NEXENT_FEEDBACK_AGENT": "", "NEXENT_PATIENT_AGENT_SINGLE": "",
    "MCP_SERVICE_TOKEN": "", "EMBEDDING_BASE_URL": "", "EMBEDDING_API_KEY": "", "EMBEDDING_MODEL": "",
    "RERANK_BASE_URL": "", "RERANK_API_KEY": "", "RERANK_MODEL": "",
    "OCR_BASE_URL": "", "OCR_API_KEY": "", "OCR_MODEL": "",
}})
sys.path.insert(0, r"{ROOT / 'backend'}")
import threading, uvicorn
from app import main
server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=0, log_level="error"))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
deadline = __import__("time").time() + 20
while not server.started:
    if __import__("time").time() > deadline:
        raise SystemExit("restored server did not start")
    __import__("time").sleep(0.02)
print("READY", server.servers[0].sockets[0].getsockname()[1], flush=True)
try:
    for _ in sys.stdin:
        pass
except KeyboardInterrupt:
    pass
server.should_exit = True
thread.join(timeout=10)
''', encoding="utf-8")

server = subprocess.Popen([sys.executable, str(SERVE)], cwd=str(ROOT), stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUNBUFFERED": "1"})
line = server.stdout.readline()
assert line.startswith("READY"), line
base = f"http://127.0.0.1:{line.split()[1]}"

http_checks = []
def check(name: str, condition: bool, detail: str = "") -> None:
    http_checks.append({"name": name, "ok": bool(condition), "detail": detail})
    print(("✓" if condition else "✗"), name, detail, flush=True)

import httpx  # noqa: E402

with httpx.Client(base_url=base, timeout=30) as http:
    login = http.post("/api/auth/login", json={"username": "drill_teacher", "password": password})
    check("恢复后教师可登录", login.status_code == 200)
    teacher = {"Authorization": "Bearer " + login.json()["token"]}
    classes = http.get("/api/teacher/classes", headers=teacher).json()
    check("教师看到原班级", any(c["id"] == klass["id"] and c["name"] == "恢复演练班" for c in classes), str(classes)[:120])
    detail = http.get(f"/api/teacher/classes/{klass['id']}", headers=teacher).json()
    check("班级名册随备份恢复", any(m["username"] == "drill_student" for m in detail["members"]))
    tasks = [t for t in detail["assignments"] if t["id"] == assignment["id"]]
    check("教师看到原任务且病例版本一致",
          tasks and tasks[0]["case_public_id"] == "CASE-001" and tasks[0]["case"]["title"] != "病例不可用")

    student_login = http.post("/api/auth/login", json={"username": "drill_student",
                                                       "password": "Drill-Student-2026"})
    check("恢复后学生可登录", student_login.status_code == 200)
    student = {"Authorization": "Bearer " + student_login.json()["token"]}
    mine = http.get("/api/assignments", headers=student).json()
    check("学生看到原任务", any(t["id"] == assignment["id"] for t in mine), str(mine)[:120])

    # The sealed session's score and feedback must be identical to the source environment.
    score_after = http.get(f"/api/sessions/{session_id}/score", headers=student)
    check("恢复后封存评分可访问", score_after.status_code == 200)
    score_after = score_after.json()
    check("封存评分逐项一致",
          score_after["total_score"] == score_before["total_score"]
          and score_after["max_score"] == score_before["max_score"]
          and [(i["item_id"], i["score"], i["max_score"]) for i in score_after["items"]]
          == [(i["item_id"], i["score"], i["max_score"]) for i in score_before["items"]],
          f"{score_after['total_score']}/{score_after['max_score']}")
    feedback_after = http.get(f"/api/sessions/{session_id}/feedback", headers=student)
    check("恢复后封存反馈可访问且环节一致", feedback_after.status_code == 200
          and feedback_after.json()["items"] == feedback_before["items"],
          f"items={len(feedback_after.json()['items']) if feedback_after.status_code == 200 else 'n/a'}")

    rejected = http.post(f"/api/sessions/{session_id}/ask", headers=student,
                         json={"request_id": "drill-after-restore", "question": "还能改吗"})
    check("已封存训练继续拒写", rejected.status_code == 409, f"HTTP {rejected.status_code}")

    # Teaching continues: starting the assignment again opens a NEW training on the restored data.
    again = http.post(f"/api/assignments/{assignment['id']}/start", headers=student).json()
    new_session = again.get("session_id")
    check("恢复后可开新训练且为新会话", bool(new_session) and new_session != session_id)
    answered = http.post(f"/api/sessions/{new_session}/ask", headers=student,
                         json={"request_id": "drill-new-1", "question": "怎么了？"})
    check("新训练问诊正常作答", answered.status_code == 200 and answered.json().get("answer"))

# ------------------------------------------------------------------- phase 4: failure drill
TAMPERED = OUT / "backup-tampered"
import shutil  # noqa: E402

shutil.copytree(BACKUP, TAMPERED)
manifest_path = TAMPERED / "manifest.json"
manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
for entry in manifest["files"]:
    if entry["path"] == "app.db":
        entry["sha256"] = "0" * 64
manifest_path.write_text(_json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
TAMPERED_TARGET = OUT / "tampered-target"
refused = False
try:
    restore_backup(TAMPERED, TAMPERED_TARGET, confirm_services_stopped=True)
except BackupRestoreError:
    refused = True
check("清单哈希不符的备份被拒绝恢复", refused)
check("被拒绝的目标目录保持不存在", not TAMPERED_TARGET.exists())

# ------------------------------------------------------------------------------- cleanup
server.stdin.close()
try:
    server.wait(timeout=15)
except subprocess.TimeoutExpired:
    server.kill()
check("恢复后的服务进程正常退出", server.returncode in (0, None) or server.returncode == 0)

passed = all(item["ok"] for item in http_checks)
report = {
    "schema_version": 1,
    "scope": "同一台机器、同机新目录恢复；非容器迁移或异机恢复",
    "source_data": str(SOURCE), "backup": str(BACKUP), "restored_data": str(RESTORED),
    "session_id": session_id, "case_public_id": "CASE-001",
    "timings_seconds": TIMINGS,
    "backup_files": [entry["path"] for entry in manifest["files"]],
    "http_checks": http_checks, "passed": passed,
    "failure_drill": {"tampered_backup_refused": refused, "target_absent": not TAMPERED_TARGET.exists()},
    "real_model_calls": 0,
    "cleanup": "演练目录由调用方决定保留或删除；本脚本不删除任何数据",
}
(OUT / "report.json").write_text(_json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(_json.dumps({"passed": passed, "timings_seconds": TIMINGS}, ensure_ascii=False))
raise SystemExit(0 if passed else 1)

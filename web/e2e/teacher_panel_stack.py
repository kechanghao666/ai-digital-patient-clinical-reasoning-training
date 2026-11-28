"""Isolated local-auth simulator stack for the teacher observability/export browser probe.

Creates a teacher (with a class + student + one training + one trusted turn), a second teacher with
no class, then serves the API for a real browser. All data is in a temporary directory; the
simulator mode issues no real model call. Run from web/e2e/teacher-panel.mjs.
"""
from __future__ import annotations

import os
import sys
import tempfile
import secrets
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn
from fastapi.testclient import TestClient


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
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("teacher probe server did not stop")


def main():
    with tempfile.TemporaryDirectory(prefix="teacher-panel-") as data:
        serve(data)


def serve(data):
    for key in list(os.environ):
        if key.startswith(("NEXENT_", "EMBEDDING_", "RERANK_", "OCR_", "MCP_")):
            del os.environ[key]
    os.environ["APP_ENV"] = "development"
    os.environ["APP_AUTH_MODE"] = "local"
    os.environ["NEXENT_MODE"] = "simulator"
    os.environ["WEB_DIST_DIR"] = str(ROOT / "web" / "dist")
    os.environ["APP_DATA_DIR"] = data
    os.environ["INTEGRATION_DB_PATH"] = str(Path(data) / "integration.db")

    from app import main as app_main
    from app.department_config import save_department_config
    from app.models import ActionKind, Event, ScoreItem, ScoreSnapshot, SessionState
    save_department_config(Path(data) / "department-catalog.json", {"03", "04"},
                           {"03.01", "03.02", "03.03", "03.04", "03.05", "03.07", "04.01"},
                           "synthetic-teacher-panel")

    password = secrets.token_urlsafe(24)
    teacher_id = app_main.ACCOUNTS.create("tpanel_teacher", password, "teacher", "面板教师（合成身份，非真实医学审核）")
    app_main.ACCOUNTS.create("tpanel_empty", password, "teacher", "空班级教师")

    client = TestClient(app_main.app)
    login = client.post("/api/auth/login", json={"username": "tpanel_teacher", "password": password}).json()
    headers = {"Authorization": "Bearer " + login["token"]}
    qualification = app_main.ACCOUNTS.create_reviewer_qualification(
        "tpanel_teacher", "03", "03.04", "2020-01-01", "2099-12-31",
    )
    source = client.get("/api/teacher/cases/STEMI-001/versions/3", headers=headers).json()
    source.update(case_id="TEACH-QUAL-PROBE", title="合成资质发布探针", source_case_id="STEMI-001", source_version=3)
    source.pop("public_id")
    saved = client.put("/api/teacher/cases/TEACH-QUAL-PROBE", headers=headers, json=source)
    assert saved.status_code == 200, saved.text
    for action in ("validate", "submit-review"):
        response = client.post(f"/api/teacher/cases/TEACH-QUAL-PROBE/versions/1/{action}", headers=headers)
        assert response.status_code == 200, response.text
    klass = client.post("/api/teacher/classes", headers=headers, json={"name": "面板班"}).json()
    client.post(f"/api/teacher/classes/{klass['id']}/students", headers=headers,
                json={"students": [{"username": "tpanel_student", "display_name": "面板学生"},
                                   {"username": "tpanel_student2", "display_name": "面板学生二"}]})
    client.post("/api/teacher/classes", headers=headers, json={"name": "面板班二"})
    student_id = app_main.ACCOUNTS.find("tpanel_student")["id"]

    case = app_main.CASE_STORE.get_by_public_id("CASE-001")
    sid = f"sid-panel-{student_id}"
    app_main.REPOSITORY.create(SessionState(
        session_id=sid, owner_id=student_id, case_id=case.case_id, case_version=case.version,
        events=[Event(request_id="req-internal", seq=1, actor="student", action=ActionKind.ASK,
                      intent="我的手机号 13800001111", payload={},
                      result={"answer": "有高血压10年。", "fact_ids": ["f_hx_htn"]},
                      sim_time=0, state_before={}, state_after={})],
        case_notes="姓名张三", sealed=True, seal_seq=1,
        score_snapshot=ScoreSnapshot(session_id=sid, seal_seq=1, total_score=1.0, max_score=5.0,
                                     status="complete",
                                     items=[ScoreItem(item_id="i1", stage="病史采集", hit=True,
                                                      score=1.0, max_score=1.0,
                                                      evidence_event_ids=[], reason="命中")]),
    ))

    turn, _ = app_main.RUNTIME.turns.issue(
        kind="patient", owner_id=student_id, session_id=sid, case_id=case.case_id,
        case_version=case.version, request_id="r1", state_version=0, question="有高血压吗",
        candidate_fact_ids=["f_hx_htn"], mode="simulator")
    app_main.RUNTIME.turns.record_call(turn.turn_id, "get_turn_context", ok=True)
    app_main.RUNTIME.turns.record_call(turn.turn_id, "submit_fact_selection", ok=True)
    app_main.RUNTIME.turns.submit(turn.turn_id, {"fact_ids": ["f_hx_htn"]})
    app_main.RUNTIME.turns.finalize(turn.turn_id)
    from app.turn_budget import write_budget
    write_budget(Path(data) / "accounts.db", 1)

    server = LiveServer(app_main.app).start()
    print(f"READY http://127.0.0.1:{server.port} {password} {data}", flush=True)
    try:
        for line in sys.stdin:
            command = line.strip()
            if command == "stop":
                break
            if command in {"activate-reviewer", "revoke-reviewer"}:
                app_main.ACCOUNTS.set_reviewer_qualification_status(
                    qualification, "active" if command == "activate-reviewer" else "revoked",
                    confirmed_external_verification=command == "activate-reviewer",
                )
                print(f"CONTROL {command}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        client.close()


if __name__ == "__main__":
    main()

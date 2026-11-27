"""Export failures must not release data; audit survives concurrency and offline backup."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.export import ExportAudit, build_deidentified_records
from app.models import ScoreItem, ScoreSnapshot, SessionState
from app.teacher_export_api import build_router


def sample():
    return SessionState(session_id="private-session", owner_id="private-student", case_id="private-case",
                        case_version=1, case_notes="张三 13800001111",
                        score_snapshot=ScoreSnapshot(session_id="private-session", seal_seq=1,
                            total_score=1, max_score=2, items=[ScoreItem(item_id="private-item",
                                stage="张三 13800001111", hit=True, score=1, max_score=2,
                                evidence_event_ids=[], reason="private-reason")]))


def make_client(audit, *, case_error=False, roster_error=False):
    class Roster:
        def classes(self, teacher_id):
            if roster_error:
                raise RuntimeError("private-database-path")
            return [{"id": "owned-class"}]

        def member_ids(self, class_id):
            return ["private-student"]

        def owned_class(self, class_id, teacher_id):
            if class_id != "owned-class":
                raise KeyError(class_id)

    def lookup(*args):
        if case_error:
            raise KeyError("private-case")
        return SimpleNamespace(case_id="private-case", version=1)

    app = FastAPI()
    app.include_router(build_router(lambda: SimpleNamespace(list_all=lambda: [sample()]),
                                   lambda: SimpleNamespace(get=lookup), Roster,
                                   lambda: "private-teacher", lambda: audit))
    return TestClient(app)


def test_export_whitelist_replaces_free_stage_and_case_identifier():
    records = build_deidentified_records([sample()], lambda *args: SimpleNamespace(case_id="private-case", version=1))
    serialized = json.dumps(records, ensure_ascii=False)
    assert "private" not in serialized and "张三" not in serialized and "13800001111" not in serialized
    assert records[0]["score"]["items"][0]["stage"] == "未分类"
    assert records[0]["case_index"] == 1
    assert records[0]["score"]["ratio"] == 0.5


@pytest.mark.parametrize("failure", ["case", "roster", "audit"])
def test_export_failure_never_returns_records(tmp_path, monkeypatch, failure):
    audit = ExportAudit(tmp_path / "accounts.db")
    if failure == "audit":
        def broken(*args):
            raise OSError("private-database-path")
        monkeypatch.setattr(audit, "append", broken)
    with make_client(audit, case_error=failure == "case", roster_error=failure == "roster") as client:
        response = client.post("/api/teacher/export", json={})
    assert response.status_code == 503
    assert "records" not in response.json() and "private" not in response.text
    if failure != "audit":
        assert audit.recent()[0]["result"] == "failed"
        assert audit.recent()[0]["record_count"] == 0


def test_export_rejects_extra_scope_and_does_not_audit_untrusted_class_text(tmp_path):
    audit = ExportAudit(tmp_path / "accounts.db")
    with make_client(audit) as client:
        assert client.post("/api/teacher/export", json={"session_ids": ["private-session"]}).status_code == 422
        assert client.post("/api/teacher/export", json={"class_id": "untrusted-class-text"}).status_code == 404
        response = client.post("/api/teacher/export", json={"class_id": "owned-class"})
    assert response.status_code == 200 and response.json()["record_count"] == 1
    assert "untrusted-class-text" not in json.dumps(audit.recent())
    assert [entry["result"] for entry in audit.recent()] == ["rejected", "success"]


def test_export_audit_concurrent_writes_are_complete(tmp_path):
    audit = ExportAudit(tmp_path / "accounts.db")
    assert audit.recent() == [] and not audit.path.exists()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: audit.append(f"teacher-{i}", {}, i, "success"), range(24)))
    records = audit.recent()
    assert len(records) == 24
    assert {row["record_count"] for row in records} == set(range(24))

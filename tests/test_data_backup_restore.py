from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import app.backup_restore as backup_restore
from app.accounts import AccountStore
from app.backup_restore import BackupRestoreError, create_backup, restore_backup
from app.case_store import CaseStore, load_case_file
from app.department_config import load_department_config, save_department_config
from app.integration.case_training import CaseTrainingStore
from app.integration.feedback import FeedbackStore
from app.integration.score_reviews import ScoreReviewStore
from app.integration.turns import TurnStore
from app.models import ActionKind, Event, ReviewStatus, ScoreItem, ScoreSnapshot, SessionState
from app.storage import SessionRepository

ROOT = Path(__file__).parents[1]


def _seed_snapshot(tmp_path: Path) -> tuple[Path, Path, str, str, list[dict]]:
    data_dir = tmp_path / "source-data"
    data_dir.mkdir()
    external_db = tmp_path / "separate-integration" / "runtime.sqlite3"
    external_db.parent.mkdir()

    repository = SessionRepository(data_dir / "app.db")
    source_case = load_case_file(ROOT / "cases" / "stemi_case_v3.json")
    teacher_cases = data_dir / "teacher_cases"
    case_store = CaseStore(ROOT / "cases", teacher_cases)
    case = source_case.model_copy(update={
        "case_id": "SYNTH-BACKUP-CASE",
        "public_id": "CASE-SYNTH-BACKUP",
        "title": "合成备份演练病例（测试夹具）",
        "source_case_id": source_case.case_id,
        "source_version": source_case.version,
        "department_code": "03",
        "subspecialty_code": "03.04",
    })
    saved_case = case_store.save_draft(case, "synthetic-teacher")
    case_store.transition(saved_case.case_id, saved_case.version, ReviewStatus.VALIDATED, "synthetic-teacher")
    case_store.transition(saved_case.case_id, saved_case.version, ReviewStatus.UNDER_REVIEW, "synthetic-teacher")

    save_department_config(data_dir / "department-catalog.json", {"03"}, {"03.04"}, "synthetic-operator")
    save_department_config(
        data_dir / "department-catalog.json",
        {"03", "04"},
        {"03.01", "03.02", "03.03", "03.04", "03.07", "04.01"},
        "synthetic-operator",
    )

    session_id = "synthetic-sealed-training"
    event = Event(
        event_id="synthetic-event-1",
        request_id="synthetic-ask-1",
        seq=1,
        actor="student",
        action=ActionKind.ASK,
        intent="synthetic question",
        result={"answer": "synthetic answer"},
        sim_time=0,
        state_before={"workflow": "history_taking"},
        state_after={"workflow": "history_taking"},
    )
    snapshot = ScoreSnapshot(
        session_id=session_id,
        seal_seq=1,
        total_score=4.0,
        max_score=5.0,
        items=[ScoreItem(
            item_id="synthetic-score-1",
            stage="病史采集",
            hit=True,
            score=4.0,
            max_score=5.0,
            evidence_event_ids=[event.event_id],
            reason="合成演练评分",
        )],
    )
    state = SessionState(
        session_id=session_id,
        owner_id="synthetic-student",
        case_id=saved_case.case_id,
        case_version=saved_case.version,
        events=[event],
        sealed=True,
        seal_seq=1,
        score_snapshot=snapshot,
    )
    repository.create(state)

    accounts = AccountStore(data_dir / "accounts.db")
    accounts.create(
        "synthetic_teacher",
        "synthetic-only-password-for-backup-test",
        "teacher",
        "合成教师（测试夹具）",
        must_change=False,
        actor_id="local-ops",
        entry="cli",
    )
    expected_account_audit = accounts.account_audit_log()
    account_id = accounts.find("synthetic_teacher")["id"]
    with accounts.connect() as connection:
        connection.execute(
            "INSERT INTO login_sessions(token_hash, account_id, expires_at) VALUES (?, ?, ?)",
            ("synthetic-token-hash", account_id, 4_000_000_000),
        )

    TurnStore(external_db)
    feedback = FeedbackStore(external_db)
    feedback.save_once(
        session_id,
        "synthetic-student",
        "rules",
        [{"stage": "病史采集", "score": 4.0}],
        "synthetic",
        {"event_ids": [event.event_id]},
    )
    CaseTrainingStore(external_db).stop(
        saved_case.case_id,
        saved_case.version,
        "synthetic-teacher",
        "synthetic stop record",
        "synthetic-stop-request",
    )
    ScoreReviewStore(external_db).append(
        session_id,
        "synthetic-student",
        "synthetic-teacher",
        "synthetic-score-review-request",
        0,
        "合成评分复核记录",
        {"total_score": 4.0, "source": "synthetic fixture"},
    )
    return data_dir, external_db, session_id, saved_case.case_id, expected_account_audit


def test_offline_backup_restore_preserves_all_service_data(tmp_path):
    data_dir, external_db, session_id, case_id, expected_account_audit = _seed_snapshot(tmp_path)
    expected_state = SessionRepository(data_dir / "app.db").get(session_id, "synthetic-student")
    expected_case_store = CaseStore(ROOT / "cases", data_dir / "teacher_cases")
    expected_case = expected_case_store.get(case_id, 1)
    expected_audit = expected_case_store.audit_events(case_id, 1)
    expected_current_config = load_department_config(data_dir / "department-catalog.json")
    expected_previous_config = load_department_config(data_dir / "department-catalog.json.previous")
    expected_current_config_bytes = (data_dir / "department-catalog.json").read_bytes()
    expected_previous_config_bytes = (data_dir / "department-catalog.json.previous").read_bytes()

    backup_dir = tmp_path / "secure-backups" / "snapshot-001"
    manifest = create_backup(
        data_dir,
        external_db,
        backup_dir,
        confirm_services_stopped=True,
    )
    indexed = {entry["path"] for entry in manifest["files"]}
    assert {"app.db", "accounts.db", "integration.db"} <= indexed
    assert f"teacher_cases/{case_id}__v1.json" in indexed
    assert "teacher_cases/case-audit.jsonl" in indexed
    assert {"department-catalog.json", "department-catalog.json.previous"} <= indexed
    assert not any(".env" in path.lower() or "upload" in path.lower() or path.lower().endswith(".png") for path in indexed)
    assert not list(backup_dir.rglob("*.png"))
    assert not list(backup_dir.rglob("*.tmp"))

    restored_dir = tmp_path / "recovered-data"
    restore_backup(backup_dir, restored_dir, confirm_services_stopped=True)

    restored_state = SessionRepository(restored_dir / "app.db").get(session_id, "synthetic-student")
    assert restored_state == expected_state
    assert restored_state.sealed is True
    assert restored_state.events[0].event_id == "synthetic-event-1"
    assert restored_state.score_snapshot.total_score == 4.0

    restored_accounts = AccountStore(restored_dir / "accounts.db")
    assert restored_accounts.find("synthetic_teacher")["display_name"] == "合成教师（测试夹具）"
    assert restored_accounts.account_audit_log() == expected_account_audit
    with restored_accounts.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM login_sessions").fetchone()[0] == 0
    assert CaseTrainingStore(restored_dir / "integration.db").get(case_id, 1)["request_id"] == "synthetic-stop-request"
    assert ScoreReviewStore(restored_dir / "integration.db").list(session_id, "synthetic-student")[0]["snapshot"]["total_score"] == 4.0
    assert FeedbackStore(restored_dir / "integration.db").get(session_id, "synthetic-student")["items"][0]["score"] == 4.0

    restored_case_store = CaseStore(ROOT / "cases", restored_dir / "teacher_cases")
    restored_case = restored_case_store.get(case_id, 1)
    assert restored_case.source_case_id == expected_case.source_case_id
    assert restored_case.source_version == expected_case.source_version
    assert restored_case.model_dump() == expected_case.model_dump()
    assert restored_case_store.audit_events(case_id, 1) == expected_audit
    assert (restored_dir / "department-catalog.json").read_bytes() == expected_current_config_bytes
    assert (restored_dir / "department-catalog.json.previous").read_bytes() == expected_previous_config_bytes
    assert load_department_config(restored_dir / "department-catalog.json") == expected_current_config
    assert load_department_config(restored_dir / "department-catalog.json.previous") == expected_previous_config


def test_backup_refuses_uploads_unknown_files_and_missing_offline_confirmation(tmp_path):
    data_dir = tmp_path / "source-data"
    data_dir.mkdir()
    external_db = tmp_path / "external" / "integration.db"
    external_db.parent.mkdir()
    SessionRepository(data_dir / "app.db")
    TurnStore(external_db)
    backup_dir = tmp_path / "backup"

    with pytest.raises(BackupRestoreError, match="停止"):
        create_backup(data_dir, external_db, backup_dir, confirm_services_stopped=False)
    (data_dir / "uploads").mkdir()
    (data_dir / "uploads" / "synthetic-upload.png").write_bytes(b"synthetic image")
    with pytest.raises(BackupRestoreError, match="未纳入白名单"):
        create_backup(data_dir, external_db, backup_dir, confirm_services_stopped=True)
    assert not backup_dir.exists()
    assert not list(tmp_path.glob(".*.staging-*"))


def test_restore_rejects_corruption_and_never_overwrites_an_existing_target(tmp_path):
    data_dir = tmp_path / "source-data"
    data_dir.mkdir()
    external_db = tmp_path / "external" / "integration.db"
    external_db.parent.mkdir()
    SessionRepository(data_dir / "app.db")
    TurnStore(external_db)
    backup_dir = tmp_path / "backup"
    create_backup(data_dir, external_db, backup_dir, confirm_services_stopped=True)

    corrupted = tmp_path / "corrupted-backup"
    shutil.copytree(backup_dir, corrupted)
    database = corrupted / "payload" / "app.db"
    database.write_bytes(database.read_bytes() + b"corruption")
    failed_target = tmp_path / "must-not-appear"
    with pytest.raises(BackupRestoreError, match="校验失败"):
        restore_backup(corrupted, failed_target, confirm_services_stopped=True)
    assert not failed_target.exists()
    assert not list(tmp_path.glob(".must-not-appear.restore-*"))

    existing_target = tmp_path / "existing-target"
    existing_target.mkdir()
    sentinel = existing_target / "keep.txt"
    sentinel.write_text("pre-existing synthetic data", encoding="utf-8")
    with pytest.raises(BackupRestoreError, match="拒绝覆盖"):
        restore_backup(backup_dir, existing_target, confirm_services_stopped=True)
    assert sentinel.read_text(encoding="utf-8") == "pre-existing synthetic data"
    assert list(existing_target.iterdir()) == [sentinel]


def test_restore_staging_failure_does_not_publish_or_modify_existing_data(tmp_path, monkeypatch):
    data_dir = tmp_path / "source-data"
    data_dir.mkdir()
    external_db = tmp_path / "external" / "integration.db"
    external_db.parent.mkdir()
    SessionRepository(data_dir / "app.db")
    TurnStore(external_db)
    backup_dir = tmp_path / "backup"
    create_backup(data_dir, external_db, backup_dir, confirm_services_stopped=True)

    protected_dir = tmp_path / "existing-data"
    protected_dir.mkdir()
    sentinel = protected_dir / "keep.txt"
    sentinel.write_text("pre-existing synthetic data", encoding="utf-8")
    original_copy = backup_restore._copy_file

    def copy_then_fail(source, destination):
        original_copy(source, destination)
        raise OSError("synthetic restore staging failure")

    monkeypatch.setattr(backup_restore, "_copy_file", copy_then_fail)
    target = tmp_path / "restore-target"
    with pytest.raises(BackupRestoreError, match="恢复失败"):
        restore_backup(backup_dir, target, confirm_services_stopped=True)

    assert not target.exists()
    assert not list(tmp_path.glob(".restore-target.restore-*"))
    assert sentinel.read_text(encoding="utf-8") == "pre-existing synthetic data"
    assert (backup_dir / "manifest.json").is_file()


def test_documented_cli_backup_and_restore_flow_uses_only_isolated_data(tmp_path):
    data_dir, external_db, session_id, _, _ = _seed_snapshot(tmp_path)
    backup_dir = tmp_path / "cli-backups" / "snapshot"
    restored_dir = tmp_path / "cli-restored-data"
    command = [
        sys.executable,
        str(ROOT / "scripts" / "backup_restore.py"),
    ]

    backup = subprocess.run(
        [
            *command,
            "backup",
            "--data-dir", str(data_dir),
            "--integration-db", str(external_db),
            "--destination", str(backup_dir),
            "--confirm-services-stopped",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert backup.returncode == 0, backup.stderr
    assert json.loads(backup.stdout)["file_count"] >= 7

    restore = subprocess.run(
        [
            *command,
            "restore",
            "--source", str(backup_dir),
            "--target-data-dir", str(restored_dir),
            "--confirm-services-stopped",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert restore.returncode == 0, restore.stderr
    recovered = SessionRepository(restored_dir / "app.db").get(session_id, "synthetic-student")
    assert recovered.sealed and recovered.score_snapshot.total_score == 4.0
    assert (restored_dir / "integration.db").is_file()


def test_backup_rejects_corrupt_source_database_without_publishing_output(tmp_path):
    data_dir = tmp_path / "source-data"
    data_dir.mkdir()
    (data_dir / "app.db").write_bytes(b"not sqlite")
    external_db = tmp_path / "external" / "integration.db"
    external_db.parent.mkdir()
    TurnStore(external_db)
    backup_dir = tmp_path / "backup"

    with pytest.raises(BackupRestoreError, match="SQLite"):
        create_backup(data_dir, external_db, backup_dir, confirm_services_stopped=True)
    assert not backup_dir.exists()

"""Offline, allowlist-based snapshots for the application's persistent data.

The API and MCP writers must be stopped before backup so separate SQLite files and
filesystem assets represent one stable service state. Restore always publishes into a
new data directory; this module never merges into or overwrites an existing directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .case_store import load_case_file
from .department_config import load_department_config

BACKUP_FORMAT = "ai-digital-patient-data-backup"
BACKUP_VERSION = 1
DATABASE_NAMES = ("app.db", "accounts.db", "integration.db")
REQUIRED_DATABASES = {"app.db", "integration.db"}
CONFIG_NAMES = {"department-catalog.json", "department-catalog.json.previous"}
CASE_AUDIT_NAME = "case-audit.jsonl"
_CASE_FILENAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_-]{2,39})__v([1-9][0-9]*)\.json$")


class BackupRestoreError(ValueError):
    """A safe-to-display backup or restore rejection."""


def _resolved(path: str | Path) -> Path:
    value = Path(path).expanduser()
    if value.is_symlink():
        raise BackupRestoreError(f"拒绝符号链接路径：{value.name}")
    return value.resolve()


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _require_regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise BackupRestoreError(f"{label}不是普通文件，拒绝继续：{path.name}")


def _secure_mode(path: Path, mode: int) -> None:
    if os.name != "nt":
        os.chmod(path, mode)


def _sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _sqlite_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _sqlite_integrity(path: Path) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(_sqlite_uri(path), uri=True, timeout=10)
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            detail = result[0] if result else "no result"
            raise BackupRestoreError(f"SQLite 完整性检查失败：{path.name}: {detail}")
    except sqlite3.Error as exc:
        raise BackupRestoreError(f"SQLite 数据库不可读或损坏：{path.name}") from exc
    finally:
        if connection is not None:
            connection.close()


def _sqlite_backup(source: Path, destination: Path) -> None:
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(_sqlite_uri(source), uri=True, timeout=10)
        destination_connection = sqlite3.connect(str(destination), timeout=10)
        source_connection.backup(destination_connection)
        destination_connection.commit()
        journal_mode = destination_connection.execute("PRAGMA journal_mode=DELETE").fetchone()
        if journal_mode is None or journal_mode[0].lower() != "delete":
            raise BackupRestoreError(f"无法将备份副本转换为独立 SQLite 文件：{source.name}")
    except sqlite3.Error as exc:
        raise BackupRestoreError(f"SQLite 备份失败：{source.name}") from exc
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
    _sqlite_integrity(destination)


def _validate_case_audit(path: Path) -> None:
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise BackupRestoreError(f"病例审计记录必须为对象：{path.name}:{line_number}")
    except BackupRestoreError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupRestoreError(f"病例审计文件不可读取：{path.name}") from exc


def _validate_case_file(path: Path) -> None:
    match = _CASE_FILENAME.fullmatch(path.name)
    if match is None:
        raise BackupRestoreError(f"教师病例文件名不符合版本格式：{path.name}")
    try:
        case = load_case_file(path)
    except Exception as exc:
        raise BackupRestoreError(f"教师病例版本不可读取：{path.name}") from exc
    if path.name != f"{case.case_id}__v{case.version}.json":
        raise BackupRestoreError(f"教师病例文件名与病例编号/版本不符：{path.name}")


def _validate_config_file(path: Path) -> None:
    try:
        load_department_config(path)
    except Exception as exc:
        raise BackupRestoreError(f"科室配置无效：{path.name}") from exc


def _source_inventory(data_directory: str | Path, integration_database: str | Path) -> list[tuple[str, Path, str]]:
    data_dir = _resolved(data_directory)
    integration_db = _resolved(integration_database)
    if not data_dir.is_dir():
        raise BackupRestoreError("APP_DATA_DIR 必须指向已存在的数据目录")
    if integration_db in {data_dir / "app.db", data_dir / "accounts.db"}:
        raise BackupRestoreError("集成数据库不能与应用或账号数据库共用同一文件")
    if _is_within(integration_db, data_dir) and integration_db != data_dir / "integration.db":
        raise BackupRestoreError("位于 APP_DATA_DIR 内的集成数据库必须使用 integration.db 文件名")

    root_allowed = {"app.db", "accounts.db", *CONFIG_NAMES, "teacher_cases"}
    if integration_db.parent == data_dir:
        root_allowed.add("integration.db")
    sidecars: set[str] = set()
    for database in (data_dir / "app.db", data_dir / "accounts.db", integration_db):
        if database.parent == data_dir:
            sidecars.update(f"{database.name}{suffix}" for suffix in ("-wal", "-shm", "-journal"))

    try:
        for entry in data_dir.iterdir():
            if entry.name not in root_allowed and entry.name not in sidecars:
                raise BackupRestoreError(
                    f"数据目录含未纳入白名单的项目 {entry.name}；为避免漏备份或收录临时/敏感文件，已拒绝备份"
                )
            if entry.name in sidecars and (entry.is_symlink() or not entry.is_file()):
                raise BackupRestoreError(f"SQLite 临时侧文件不是普通文件：{entry.name}")
    except OSError as exc:
        raise BackupRestoreError("无法检查应用数据目录") from exc

    sources: list[tuple[str, Path, str]] = []
    for name, source, required in (
        ("app.db", data_dir / "app.db", True),
        ("accounts.db", data_dir / "accounts.db", False),
        ("integration.db", integration_db, True),
    ):
        if not source.exists():
            if required:
                raise BackupRestoreError(f"缺少必需数据库：{name}")
            continue
        _require_regular_file(source, f"数据库 {name}")
        sources.append((name, source, "sqlite"))

    for name in sorted(CONFIG_NAMES):
        source = data_dir / name
        if source.exists():
            _require_regular_file(source, f"配置 {name}")
            _validate_config_file(source)
            sources.append((name, source, "file"))

    teacher_dir = data_dir / "teacher_cases"
    if teacher_dir.exists():
        if teacher_dir.is_symlink() or not teacher_dir.is_dir():
            raise BackupRestoreError("teacher_cases 必须是普通目录")
        try:
            for source in sorted(teacher_dir.iterdir()):
                _require_regular_file(source, "教师病例资产")
                if source.name == CASE_AUDIT_NAME:
                    _validate_case_audit(source)
                elif source.suffix == ".json":
                    _validate_case_file(source)
                else:
                    raise BackupRestoreError(
                        f"teacher_cases 含未纳入白名单的项目 {source.name}；拒绝备份"
                    )
                sources.append((f"teacher_cases/{source.name}", source, "file"))
        except OSError as exc:
            raise BackupRestoreError("无法检查教师病例目录") from exc

    if not {"app.db", "integration.db"}.issubset({name for name, _, kind in sources if kind == "sqlite"}):
        raise BackupRestoreError("应用数据库或集成数据库缺失")
    return sources


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _secure_mode(destination.parent, 0o700)
    shutil.copyfile(source, destination)
    _secure_mode(destination, 0o600)


def create_backup(
    data_directory: str | Path,
    integration_database: str | Path,
    destination: str | Path,
    *,
    confirm_services_stopped: bool,
) -> dict[str, Any]:
    """Create an allowlist snapshot; refuse unknown source contents and existing output."""
    if not confirm_services_stopped:
        raise BackupRestoreError("备份前必须停止 API、MCP 及所有写入者并确认停机")
    data_dir = _resolved(data_directory)
    integration_db = _resolved(integration_database)
    backup_dir = _resolved(destination)
    if backup_dir.exists():
        raise BackupRestoreError("备份目标已存在；拒绝覆盖")
    if _is_within(backup_dir, data_dir) or _is_within(data_dir, backup_dir):
        raise BackupRestoreError("备份目标必须与应用数据目录分离")
    if backup_dir == integration_db or _is_within(integration_db, backup_dir):
        raise BackupRestoreError("备份目标不能覆盖集成数据库或其父目录")

    sources = _source_inventory(data_dir, integration_db)
    try:
        backup_dir.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{backup_dir.name}.staging-", dir=backup_dir.parent))
    except OSError as exc:
        raise BackupRestoreError("无法创建临时备份目录") from exc

    try:
        payload_dir = stage / "payload"
        payload_dir.mkdir()
        entries = []
        for relative, source, kind in sources:
            output = payload_dir / PurePosixPath(relative)
            output.parent.mkdir(parents=True, exist_ok=True)
            if kind == "sqlite":
                _sqlite_backup(source, output)
                _secure_mode(output, 0o600)
            else:
                _copy_file(source, output)
            size, digest = _sha256(output)
            entries.append({"path": relative, "size_bytes": size, "sha256": digest})

        manifest = {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "consistency": "offline; all service writers stopped",
            "files": sorted(entries, key=lambda item: item["path"]),
        }
        manifest_path = stage / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _secure_mode(manifest_path, 0o600)
        _secure_mode(payload_dir, 0o700)
        _secure_mode(stage, 0o700)
        if backup_dir.exists():
            raise BackupRestoreError("备份目标在操作期间出现；拒绝覆盖")
        os.rename(stage, backup_dir)
        return manifest
    except BackupRestoreError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise BackupRestoreError("备份未完成；临时快照已清理或保留为隐藏 staging 目录") from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def _valid_payload_path(value: Any) -> str:
    if not isinstance(value, str) or "\\" in value:
        raise BackupRestoreError("备份清单含无效路径")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupRestoreError("备份清单含越界路径")
    normalized = path.as_posix()
    if normalized != value:
        raise BackupRestoreError("备份清单含非规范路径")
    if normalized in {"app.db", "accounts.db", "integration.db", *CONFIG_NAMES}:
        return normalized
    if len(path.parts) == 2 and path.parts[0] == "teacher_cases":
        name = path.parts[1]
        if name == CASE_AUDIT_NAME or _CASE_FILENAME.fullmatch(name):
            return normalized
    raise BackupRestoreError("备份清单含未允许的数据文件")


def _read_and_validate_backup(backup_dir: Path) -> tuple[dict[str, Any], Path]:
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise BackupRestoreError("备份来源必须是普通目录")
    manifest_path = backup_dir / "manifest.json"
    payload_dir = backup_dir / "payload"
    _require_regular_file(manifest_path, "备份清单")
    if payload_dir.is_symlink() or not payload_dir.is_dir():
        raise BackupRestoreError("备份缺少有效 payload 目录")
    try:
        if {entry.name for entry in backup_dir.iterdir()} != {"manifest.json", "payload"}:
            raise BackupRestoreError("备份目录含额外项目，拒绝恢复")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except BackupRestoreError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupRestoreError("备份清单不可读取或格式无效") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT or manifest.get("version") != BACKUP_VERSION:
        raise BackupRestoreError("备份格式或版本不受支持")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise BackupRestoreError("备份清单缺少文件索引")

    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "size_bytes", "sha256"}:
            raise BackupRestoreError("备份清单含无效文件项")
        relative = _valid_payload_path(entry["path"])
        if relative in indexed:
            raise BackupRestoreError("备份清单包含重复路径")
        size = entry["size_bytes"]
        digest = entry["sha256"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BackupRestoreError("备份清单含无效文件大小")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BackupRestoreError("备份清单含无效 SHA-256")
        indexed[relative] = entry

    if not REQUIRED_DATABASES.issubset(indexed):
        raise BackupRestoreError("备份缺少必需的应用数据库或集成数据库")
    try:
        actual_files: set[str] = set()
        for entry in payload_dir.rglob("*"):
            if entry.is_symlink():
                raise BackupRestoreError("备份 payload 不允许符号链接")
            relative = entry.relative_to(payload_dir).as_posix()
            if entry.is_dir():
                if relative != "teacher_cases":
                    raise BackupRestoreError("备份 payload 含未允许的目录")
            elif entry.is_file():
                actual_files.add(_valid_payload_path(relative))
            else:
                raise BackupRestoreError("备份 payload 含非普通文件")
        if actual_files != set(indexed):
            raise BackupRestoreError("备份文件与清单不一致")
        for relative, entry in indexed.items():
            path = payload_dir / PurePosixPath(relative)
            size, digest = _sha256(path)
            if size != entry["size_bytes"] or digest != entry["sha256"]:
                raise BackupRestoreError(f"备份校验失败：{relative}")
    except BackupRestoreError:
        raise
    except OSError as exc:
        raise BackupRestoreError("无法读取备份 payload") from exc

    _validate_payload_content(payload_dir, set(indexed))
    return manifest, payload_dir


def _invalidate_restored_login_sessions(accounts_database: Path) -> None:
    """Restored data must not revive bearer sessions held by clients elsewhere."""
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(accounts_database), timeout=10)
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='login_sessions'"
        ).fetchone()
        if exists:
            connection.execute("DELETE FROM login_sessions")
            connection.commit()
    except sqlite3.Error as exc:
        raise BackupRestoreError("无法安全清除恢复副本中的旧登录会话") from exc
    finally:
        if connection is not None:
            connection.close()


def _validate_payload_content(payload_dir: Path, indexed_paths: set[str]) -> None:
    for name in REQUIRED_DATABASES | (indexed_paths & {"accounts.db"}):
        _sqlite_integrity(payload_dir / name)
    for name in CONFIG_NAMES & indexed_paths:
        _validate_config_file(payload_dir / name)
    teacher_dir = payload_dir / "teacher_cases"
    if teacher_dir.exists():
        for path in teacher_dir.iterdir():
            if path.name == CASE_AUDIT_NAME:
                _validate_case_audit(path)
            else:
                _validate_case_file(path)


def restore_backup(
    source: str | Path,
    target_data_directory: str | Path,
    *,
    confirm_services_stopped: bool,
) -> dict[str, Any]:
    """Validate then atomically publish a snapshot into a previously absent directory."""
    if not confirm_services_stopped:
        raise BackupRestoreError("恢复前必须确认目标服务已停止，且目标目录仅用于隔离恢复")
    backup_dir = _resolved(source)
    target_dir = _resolved(target_data_directory)
    if target_dir.exists():
        raise BackupRestoreError("恢复目标已存在（无论为空或非空）；拒绝覆盖现存数据")
    if _is_within(backup_dir, target_dir) or _is_within(target_dir, backup_dir):
        raise BackupRestoreError("备份来源与恢复目标不能互相包含")

    manifest, payload_dir = _read_and_validate_backup(backup_dir)
    try:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{target_dir.name}.restore-", dir=target_dir.parent))
    except OSError as exc:
        raise BackupRestoreError("无法创建隔离恢复暂存目录") from exc

    try:
        for entry in manifest["files"]:
            relative = _valid_payload_path(entry["path"])
            _copy_file(payload_dir / PurePosixPath(relative), stage / PurePosixPath(relative))
        restored_paths = {entry["path"] for entry in manifest["files"]}
        if "accounts.db" in restored_paths:
            _invalidate_restored_login_sessions(stage / "accounts.db")
        _validate_payload_content(stage, restored_paths)
        if target_dir.exists():
            raise BackupRestoreError("恢复目标在操作期间出现；拒绝覆盖")
        _secure_mode(stage, 0o700)
        os.rename(stage, target_dir)
        return manifest
    except BackupRestoreError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise BackupRestoreError("恢复失败；现有目标数据未修改，暂存目录将清理") from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)

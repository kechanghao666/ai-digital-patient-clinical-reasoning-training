"""Protected, single-institution enablement for the reference department catalog.

The web application is read-only for this configuration. A deployment operator edits it
through the CLI under the service data directory; student/teacher tokens cannot change it.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .department_catalog import get_parent_department
from .department_catalog_data import DEPARTMENT_NAMES, SUBSPECIALTY_NAMES

CONFIG_FILENAME = "department-catalog.json"
CONFIG_SCHEMA_VERSION = 1


class DepartmentConfigError(ValueError):
    """Invalid or unreadable department configuration; callers should fail closed."""


@dataclass(frozen=True)
class DepartmentConfig:
    enabled_departments: frozenset[str]
    enabled_subspecialties: frozenset[str]
    configured: bool
    updated_by: str | None = None
    updated_at: str | None = None


def config_path_for_data_dir(data_dir: str | Path) -> Path:
    return Path(data_dir) / CONFIG_FILENAME


def config_path_for_case_store(store: Any) -> Path:
    teacher_dir = getattr(store, "teacher_dir", None)
    if teacher_dir is not None:
        return config_path_for_data_dir(Path(teacher_dir).parent)
    from .integration.config import data_directory

    return config_path_for_data_dir(data_directory())


def _validate_config(raw: Any) -> DepartmentConfig:
    if not isinstance(raw, dict) or raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise DepartmentConfigError("科室配置版本无效")
    departments = raw.get("enabled_departments")
    subspecialties = raw.get("enabled_subspecialties")
    if not isinstance(departments, list) or not all(isinstance(code, str) for code in departments):
        raise DepartmentConfigError("enabled_departments 必须是编码数组")
    if not isinstance(subspecialties, list) or not all(isinstance(code, str) for code in subspecialties):
        raise DepartmentConfigError("enabled_subspecialties 必须是编码数组")
    if len(set(departments)) != len(departments) or len(set(subspecialties)) != len(subspecialties):
        raise DepartmentConfigError("科室配置不能包含重复编码")

    known_departments = set(DEPARTMENT_NAMES)
    known_subspecialties = set(SUBSPECIALTY_NAMES)
    unknown_departments = set(departments) - known_departments
    unknown_subspecialties = set(subspecialties) - known_subspecialties
    if unknown_departments or unknown_subspecialties:
        raise DepartmentConfigError("科室配置包含未收录编码")
    if any(get_parent_department(code) not in departments for code in subspecialties):
        raise DepartmentConfigError("已启用的亚专科必须同时启用其父科室")

    updated_by = raw.get("updated_by")
    updated_at = raw.get("updated_at")
    if not isinstance(updated_by, str) or not updated_by.strip():
        raise DepartmentConfigError("科室配置缺少操作者标识")
    if not isinstance(updated_at, str) or not updated_at.strip():
        raise DepartmentConfigError("科室配置缺少更新时间")
    return DepartmentConfig(
        enabled_departments=frozenset(departments),
        enabled_subspecialties=frozenset(subspecialties),
        configured=True,
        updated_by=updated_by,
        updated_at=updated_at,
    )


def load_department_config(path: str | Path) -> DepartmentConfig:
    """Read every time so an operator's atomic config replacement takes effect immediately.

    Missing config is intentionally an empty enablement set: reference entries are not
    presumed to be a particular institution's departments.
    """
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DepartmentConfig(frozenset(), frozenset(), configured=False)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DepartmentConfigError("科室配置不可读取或不是有效 JSON") from exc
    return _validate_config(raw)


def _validated_payload(
    enabled_departments: set[str],
    enabled_subspecialties: set[str],
    operator: str,
    updated_at: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "enabled_departments": sorted(enabled_departments),
        "enabled_subspecialties": sorted(enabled_subspecialties),
        "updated_by": operator.strip(),
        "updated_at": updated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _validate_config(payload)
    return payload


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Windows ACLs, rather than POSIX mode bits, control access on Windows.
            pass
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def save_department_config(
    path: str | Path,
    enabled_departments: set[str],
    enabled_subspecialties: set[str],
    operator: str,
) -> DepartmentConfig:
    """Atomically replace config, keeping the last valid content as a rollback file."""
    config_path = Path(path)
    payload = _validated_payload(enabled_departments, enabled_subspecialties, operator)
    serialized = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if config_path.exists():
        try:
            current = config_path.read_bytes()
            _validate_config(json.loads(current.decode("utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, DepartmentConfigError) as exc:
            raise DepartmentConfigError("当前科室配置无效；拒绝覆盖，请先人工备份并修复") from exc
        _atomic_write(config_path.with_name(config_path.name + ".previous"), current)
    _atomic_write(config_path, serialized)
    return _validate_config(payload)


def restore_previous_department_config(path: str | Path, operator: str) -> DepartmentConfig:
    """Restore the last valid configuration, preserving the current version as rollback."""
    config_path = Path(path)
    previous_path = config_path.with_name(config_path.name + ".previous")
    try:
        previous = previous_path.read_bytes()
        previous_payload = json.loads(previous.decode("utf-8"))
        _validate_config(previous_payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, DepartmentConfigError) as exc:
        raise DepartmentConfigError("没有可恢复的有效上一版科室配置") from exc
    previous_payload["updated_by"] = operator.strip()
    previous_payload["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _validate_config(previous_payload)
    current = config_path.read_bytes() if config_path.exists() else None
    if current is not None:
        try:
            _validate_config(json.loads(current.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, DepartmentConfigError) as exc:
            raise DepartmentConfigError("当前配置损坏；拒绝自动回滚覆盖，请先人工备份") from exc
        _atomic_write(previous_path, current)
    _atomic_write(config_path, (json.dumps(previous_payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return _validate_config(previous_payload)

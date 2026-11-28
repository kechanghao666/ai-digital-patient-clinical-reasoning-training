"""Configure the single institution's enabled department reference items.

This is an offline deployment-operator command, not a teacher API. The configuration is
stored beside application data as department-catalog.json; it never opens app.db.
"""
from __future__ import annotations

import argparse
import json
import sys
from getpass import getuser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.department_config import (  # noqa: E402
    DepartmentConfigError,
    config_path_for_data_dir,
    load_department_config,
    restore_previous_department_config,
    save_department_config,
)
from app.integration.config import data_directory  # noqa: E402


def _codes(value: str) -> set[str]:
    return {part.strip() for part in value.split(",") if part.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--show", action="store_true", help="显示当前启用配置，不读取业务数据库")
    mode.add_argument("--restore-previous", action="store_true", help="恢复上一份有效配置")
    parser.add_argument("--enabled-departments", help="完整替换 L1 启用编码，如 03,04；空字符串表示全部停用")
    parser.add_argument("--enabled-subspecialties", help="完整替换 L2 启用编码，如 03.04,04.01；可为空")
    args = parser.parse_args()
    path = config_path_for_data_dir(data_directory())

    try:
        if args.show:
            config = load_department_config(path)
            print(json.dumps({
                "path": str(path),
                "configured": config.configured,
                "enabled_departments": sorted(config.enabled_departments),
                "enabled_subspecialties": sorted(config.enabled_subspecialties),
                "updated_by": config.updated_by,
                "updated_at": config.updated_at,
            }, ensure_ascii=False, indent=2))
            return 0
        if args.restore_previous:
            config = restore_previous_department_config(path, getuser())
        else:
            if args.enabled_departments is None or args.enabled_subspecialties is None:
                parser.error("设置时须同时提供 --enabled-departments 和 --enabled-subspecialties")
            config = save_department_config(
                path,
                _codes(args.enabled_departments),
                _codes(args.enabled_subspecialties),
                getuser(),
            )
    except DepartmentConfigError as exc:
        parser.error(str(exc))

    print(json.dumps({
        "path": str(path),
        "enabled_departments": sorted(config.enabled_departments),
        "enabled_subspecialties": sorted(config.enabled_subspecialties),
        "updated_by": config.updated_by,
        "updated_at": config.updated_at,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

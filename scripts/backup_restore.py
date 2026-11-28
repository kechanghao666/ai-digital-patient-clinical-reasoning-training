"""Create or restore an offline allowlist snapshot of application data.

This command reads only the paths supplied on the command line. It does not load
application environment settings or model credentials.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.backup_restore import BackupRestoreError, create_backup, restore_backup  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup", help="离线创建白名单数据备份")
    backup.add_argument("--data-dir", required=True, type=Path, help="服务当前 APP_DATA_DIR")
    backup.add_argument("--integration-db", type=Path, help="自定义 INTEGRATION_DB_PATH；缺省为 data-dir/integration.db")
    backup.add_argument("--destination", required=True, type=Path, help="必须是新的备份目录，且在数据目录之外")
    backup.add_argument("--confirm-services-stopped", action="store_true", help="确认 API/MCP 及所有写入者均已停止")

    restore = commands.add_parser("restore", help="校验备份并恢复到全新的数据目录")
    restore.add_argument("--source", required=True, type=Path, help="备份目录")
    restore.add_argument("--target-data-dir", required=True, type=Path, help="必须不存在的新隔离数据目录")
    restore.add_argument("--confirm-services-stopped", action="store_true", help="确认目标没有运行服务或其他写入者")

    args = parser.parse_args()
    try:
        if args.command == "backup":
            if not args.confirm_services_stopped:
                raise BackupRestoreError("必须显式确认 API、MCP 及所有写入者均已停止")
            integration_db = args.integration_db or (args.data_dir / "integration.db")
            manifest = create_backup(
                args.data_dir,
                integration_db,
                args.destination,
                confirm_services_stopped=True,
            )
            print(json.dumps({"backup": str(args.destination), "file_count": len(manifest["files"])}, ensure_ascii=False))
        else:
            if not args.confirm_services_stopped:
                raise BackupRestoreError("必须显式确认目标服务已停止")
            manifest = restore_backup(
                args.source,
                args.target_data_dir,
                confirm_services_stopped=True,
            )
            print(json.dumps({"restored_data_dir": str(args.target_data_dir), "file_count": len(manifest["files"])}, ensure_ascii=False))
    except BackupRestoreError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

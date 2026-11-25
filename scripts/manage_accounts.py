"""Provision local accounts without putting passwords in shell arguments or logs."""
import argparse
import getpass
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.accounts import AccountStore
from app.integration.config import data_directory


def main():
    parser = argparse.ArgumentParser(description="管理本项目独立账号；使用当前 APP_DATA_DIR")
    parser.add_argument("action", choices=["create", "reset-password", "disable", "audit-log"])
    parser.add_argument("username", nargs="?")
    parser.add_argument("--role", choices=["student", "teacher"], default="student")
    parser.add_argument("--name")
    parser.add_argument("--limit", type=int, default=100, help="audit-log 输出的最新记录数（1–500）")
    args = parser.parse_args()
    if args.action == "audit-log":
        if args.username is not None:
            parser.error("audit-log 不接受 username")
        try:
            records = AccountStore.read_account_audit(data_directory() / "accounts.db", args.limit)
            print(json.dumps(records, ensure_ascii=False))
        except (ValueError, sqlite3.Error) as exc:
            parser.exit(1, str(exc) + "\n")
        return
    store = AccountStore(data_directory() / "accounts.db")
    if args.username is None:
        parser.error(f"{args.action} 需要 username")
    try:
        if args.action == "disable":
            changed = store.disable(args.username, actor_id="local-ops", entry="cli")
            if changed:
                print("账号已停用并记录审计。")
            else:
                print("账号已处于停用状态；未新增状态变更审计。")
            return
        password = getpass.getpass("密码（12–128 个字符）: ")
        if password != getpass.getpass("再次输入密码: "):
            raise ValueError("两次密码不一致")
        if args.action == "create":
            store.create(args.username, password, args.role, args.name or args.username,
                         actor_id="local-ops", entry="cli")
        else:
            store.reset_password(args.username, password, actor_id="local-ops", entry="cli")
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")
    print("账号操作已完成；密码重置会使已有登录立即失效。")


if __name__ == "__main__":
    main()

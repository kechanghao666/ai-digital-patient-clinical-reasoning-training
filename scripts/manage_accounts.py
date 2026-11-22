"""Provision local accounts without putting passwords in shell arguments or logs."""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.accounts import AccountStore
from app.integration.config import data_directory


def main():
    parser = argparse.ArgumentParser(description="管理本项目独立账号；使用当前 APP_DATA_DIR")
    parser.add_argument("action", choices=["create", "reset-password", "disable"])
    parser.add_argument("username")
    parser.add_argument("--role", choices=["student", "teacher"], default="student")
    parser.add_argument("--name")
    args = parser.parse_args()
    store = AccountStore(data_directory() / "accounts.db")
    try:
        if args.action == "disable":
            store.disable(args.username)
        else:
            password = getpass.getpass("密码（12–128 个字符）: ")
            if password != getpass.getpass("再次输入密码: "):
                raise ValueError("两次密码不一致")
            if args.action == "create":
                store.create(args.username, password, args.role, args.name or args.username)
            else:
                store.reset_password(args.username, password)
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")
    print("账号操作已完成；密码重置或停用会使已有登录立即失效。")


if __name__ == "__main__":
    main()

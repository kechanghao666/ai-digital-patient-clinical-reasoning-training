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
from app.sensitive_access import SensitiveAccessAudit, record_audit_read

AUDIT_LOG_NAMES = {"audit-log": "account_management_audit",
                   "qualification-audit": "reviewer_qualification_audit",
                   "sensitive-audit": "sensitive_access_audit"}


def main():
    parser = argparse.ArgumentParser(description="管理本项目独立账号；使用当前 APP_DATA_DIR")
    parser.add_argument("action", choices=["create", "reset-password", "disable", "audit-log",
                                          "qualification-create", "qualification-status", "qualification-audit",
                                          "sensitive-audit"])
    parser.add_argument("username", nargs="?", help="账号名；qualification-status 时传入资质记录编号")
    parser.add_argument("--role", choices=["student", "teacher"], default="student")
    parser.add_argument("--name")
    parser.add_argument("--limit", type=int, default=100, help="audit-log 输出的最新记录数（1–500）")
    parser.add_argument("--department-code")
    parser.add_argument("--subspecialty-code")
    parser.add_argument("--valid-from")
    parser.add_argument("--valid-until")
    parser.add_argument("--status", choices=["active", "suspended", "revoked"])
    parser.add_argument("--confirm-external-verification", action="store_true",
                        help="由获授权本机操作人员确认已在系统外人工核验；程序不会核验医学执业资格")
    args = parser.parse_args()
    if args.action in AUDIT_LOG_NAMES:
        if args.username is not None:
            parser.error(f"{args.action} 不接受 username")
        accounts_db = data_directory() / "accounts.db"
        audit = SensitiveAccessAudit(accounts_db)
        try:
            if args.action == "sensitive-audit":
                records = audit.recent(args.limit)
            else:
                reader = (AccountStore.read_account_audit if args.action == "audit-log"
                          else AccountStore.read_reviewer_qualification_audit)
                records = reader(accounts_db, args.limit)
            # Reading any audit log is itself a sensitive access and is recorded. A missing
            # accounts.db keeps the historic guarantee: a read of nothing creates nothing.
            record_audit_read(audit, AUDIT_LOG_NAMES[args.action], "local-ops", args.limit)
            print(json.dumps(records, ensure_ascii=False))
        except (ValueError, sqlite3.Error) as exc:
            parser.exit(1, str(exc) + "\n")
        return
    if args.username is None:
        parser.error(f"{args.action} 需要 username")
    if args.action == "qualification-status" and args.status is None:
        parser.error("qualification-status 需要 --status")
    store = AccountStore(data_directory() / "accounts.db")
    try:
        if args.action == "qualification-create":
            qualification_id = store.create_reviewer_qualification(
                args.username, args.department_code, args.subspecialty_code,
                args.valid_from, args.valid_until,
            )
            print(f"已登记待外部核验资质：{qualification_id}")
            return
        if args.action == "qualification-status":
            changed = store.set_reviewer_qualification_status(
                args.username, args.status,
                confirmed_external_verification=args.confirm_external_verification,
            )
            print("资质状态已更新并记录审计。" if changed else "状态未变化；未新增审计。")
            return
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
    except (ValueError, sqlite3.Error) as exc:
        parser.exit(1, str(exc) + "\n")
    print("账号操作已完成；密码重置会使已有登录立即失效。")


if __name__ == "__main__":
    main()

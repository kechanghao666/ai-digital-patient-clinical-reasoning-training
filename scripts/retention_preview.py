"""Read-only retention preview over the three persistent stores (dry run, never deletes).

Reports what a future retention policy would consider removable — aged sealed/unsealed trainings,
expired login sessions and attempts, aged finalized/expired/cancelled trusted turns and their tool
calls — together with the age range of each scope. Every invocation is recorded in the
sensitive-access audit; if that record cannot be written the preview is not shown (fail closed).
Deletion execution is deliberately not implemented here.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.integration.config import data_directory
from app.sensitive_access import SensitiveAccessAudit, preview_retention


def main() -> None:
    parser = argparse.ArgumentParser(description="留存/删除预览（只读干跑，不删除任何数据）")
    parser.add_argument("--app-db", default=None, help="应用库路径（默认 APP_DATA_DIR/app.db）")
    parser.add_argument("--accounts-db", default=None, help="账号库路径（默认 APP_DATA_DIR/accounts.db）")
    parser.add_argument("--integration-db", default=None, help="集成库路径（默认 INTEGRATION_DB_PATH）")
    parser.add_argument("--session-age-days", type=int, default=None,
                        help="训练留存天数：封存或最后活动早于该天数的会话计入预览")
    parser.add_argument("--turn-age-days", type=int, default=None,
                        help="可信回合留存天数：终态回合早于该天数的计入预览")
    parser.add_argument("--skip-expired-logins", action="store_true",
                        help="不统计过期登录会话与限流记录")
    args = parser.parse_args()

    data_dir = data_directory()
    app_db = Path(args.app_db) if args.app_db else data_dir / "app.db"
    accounts_db = Path(args.accounts_db) if args.accounts_db else data_dir / "accounts.db"
    integration_db = Path(args.integration_db) if args.integration_db else Path(
        os.getenv("INTEGRATION_DB_PATH") or data_dir / "integration.db")

    try:
        result = preview_retention(app_db, accounts_db, integration_db,
                                   session_age_days=args.session_age_days,
                                   turn_age_days=args.turn_age_days,
                                   include_expired_logins=not args.skip_expired_logins)
    except ValueError as exc:
        print(str(exc) + "\n", file=sys.stderr)
        sys.exit(1)

    audit = SensitiveAccessAudit(accounts_db)
    scope = {"session_age_days": args.session_age_days, "turn_age_days": args.turn_age_days,
             "include_expired_logins": not args.skip_expired_logins}
    try:
        audit.append("retention_preview", "local-ops", scope, "success")
    except Exception as exc:
        print(f"预览已完成，但敏感访问审计写入失败（{type(exc).__name__}）；按失败处理，不输出预览。", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

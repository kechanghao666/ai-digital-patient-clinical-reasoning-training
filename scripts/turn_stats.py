"""Print observability statistics over the trusted turn / tool-call store (read-only).

Opens the store with a read-only SQLite connection: it never creates the file, never initialises
tables, never switches journal mode and never migrates. A missing path, an uninitialised file or a
store that predates the observability columns is reported explicitly. Optional case/date filters
narrow the aggregate (UTC day boundaries, inclusive). The configured daily turn budget, when
present, is merged with today's count; token usage and billing are reported as "unknown" and are
never invented.
"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.integration.turns import read_stats
from app.turn_budget import TurnBudgetError, budget_status, read_budget


def main() -> None:
    parser = argparse.ArgumentParser(description="打印可信回合与工具调用的观测统计（只读）")
    parser.add_argument("--db", default=os.getenv("INTEGRATION_DB_PATH"),
                        help="集成库路径（默认取 INTEGRATION_DB_PATH）")
    parser.add_argument("--case-id", action="append", default=None,
                        help="按病例内部编号筛选（可重复）")
    parser.add_argument("--date-from", help="起始 UTC 日期（YYYY-MM-DD，含当天）")
    parser.add_argument("--date-to", help="结束 UTC 日期（YYYY-MM-DD，含当天）")
    parser.add_argument("--accounts-db", default=None,
                        help="账号库路径（默认取 APP_DATA_DIR/accounts.db），用于读取每日回合预算")
    args = parser.parse_args()
    if not args.db:
        parser.error("需要 --db 或设置 INTEGRATION_DB_PATH")
    try:
        result = read_stats(args.db, case_ids=args.case_id, date_from=args.date_from, date_to=args.date_to)
    except ValueError as exc:
        parser.error(str(exc))
    if not result.get("ok"):
        # Short, client-safe error: reason + message, never the absolute path or SQL detail.
        print(json.dumps({"ok": False, "reason": result.get("reason"), "error": result.get("error")},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
    payload: dict = {"ok": True, "stats": result["stats"]}
    if args.accounts_db is None:
        data_dir = os.getenv("APP_DATA_DIR")
        args.accounts_db = str(Path(data_dir) / "accounts.db") if data_dir else None
    if args.accounts_db:
        try:
            config = read_budget(args.accounts_db)
            daily = result
            if config is not None and (args.case_id or args.date_from or args.date_to):
                daily = read_stats(args.db)
            count = daily["stats"].get("turns_today") if daily.get("ok") else None
            budget = budget_status(config, count)
            if budget is not None:
                budget["scope"] = "all recorded turns; independent of case/date filters"
            if not daily.get("ok"):
                payload["budget_note"] = "今日回合统计不可用，预算状态未知"
        except (TurnBudgetError, sqlite3.Error):
            budget = None
            payload["budget_note"] = "预算配置不可用"
        if budget is not None:
            payload["budget"] = budget
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

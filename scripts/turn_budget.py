"""Set / clear / show the daily trusted-turn budget (local operations only).

The budget is a count alert over trusted turns issued today, not a cost figure: token usage and
billing stay "unknown" until a real bill exists. Every set/clear writes a sensitive-access audit
entry in the same transaction as the configuration change.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.integration.config import data_directory
from app.turn_budget import TurnBudgetError, budget_status, clear_budget, read_budget, write_budget


def main() -> None:
    parser = argparse.ArgumentParser(description="管理每日可信回合预算（本机运维）")
    parser.add_argument("action", choices=["set", "clear", "show"])
    parser.add_argument("--limit", type=int, help="set：每日可信回合上限（1–1000000）")
    args = parser.parse_args()
    accounts_db = data_directory() / "accounts.db"
    try:
        if args.action == "set":
            if args.limit is None:
                parser.error("set 需要 --limit")
            config = write_budget(accounts_db, args.limit)
            print(f"已设置每日可信回合预算 {config['daily_turn_limit']}，审计已同事务记录。")
        elif args.action == "clear":
            changed = clear_budget(accounts_db)
            print("已清除预算并记录审计。" if changed else "当前没有预算配置；未新增审计。")
        else:
            config = read_budget(accounts_db)
            if config is None:
                print(json.dumps({"configured": False, "notice": "未配置每日回合预算；token 与费用仍为 unknown。"},
                                 ensure_ascii=False))
            else:
                print(json.dumps({"configured": True, "budget": config}, ensure_ascii=False, indent=2))
    except (TurnBudgetError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()

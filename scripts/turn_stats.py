"""Print observability statistics over the trusted turn / tool-call store.

Counts by kind/mode/status, tool-call failure rate and finalized-turn latency. ``mode`` is whatever
the adapter recorded at issue time (nexent = real platform, simulator = rule engine). Token usage and
billing are intentionally reported as "unknown" and are never invented. Read-only; never writes.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.integration.turns import TurnStore


def main() -> None:
    parser = argparse.ArgumentParser(description="打印可信回合与工具调用的观测统计")
    parser.add_argument("--db", default=os.getenv("INTEGRATION_DB_PATH"),
                        help="集成库路径（默认取 INTEGRATION_DB_PATH）")
    args = parser.parse_args()
    if not args.db:
        parser.error("需要 --db 或设置 INTEGRATION_DB_PATH")
    stats = TurnStore(args.db).stats()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

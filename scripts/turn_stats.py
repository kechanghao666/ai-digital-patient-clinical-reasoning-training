"""Print observability statistics over the trusted turn / tool-call store (read-only).

Opens the store with a read-only SQLite connection: it never creates the file, never initialises
tables, never switches journal mode and never migrates. A missing path, an uninitialised file or a
store that predates the observability columns is reported explicitly. Token usage and billing are
reported as "unknown" and are never invented.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.integration.turns import read_stats


def main() -> None:
    parser = argparse.ArgumentParser(description="打印可信回合与工具调用的观测统计（只读）")
    parser.add_argument("--db", default=os.getenv("INTEGRATION_DB_PATH"),
                        help="集成库路径（默认取 INTEGRATION_DB_PATH）")
    args = parser.parse_args()
    if not args.db:
        parser.error("需要 --db 或设置 INTEGRATION_DB_PATH")
    result = read_stats(args.db)
    if not result.get("ok"):
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result["stats"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

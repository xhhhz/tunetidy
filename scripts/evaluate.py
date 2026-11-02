"""输出一份整理的评估数字（Markdown），用于 README。

    uv run python scripts/evaluate.py <整理 id> [--db 数据库路径]

以只读方式打开数据库，不写入任何东西；输出只有汇总比例，不含歌名。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from tunetidy.evaluate import evaluate, to_markdown
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import default_db_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plan_id")
    ap.add_argument("--db", type=Path, default=None, help="默认是 tunetidy 的数据库")
    args = ap.parse_args()
    db = args.db or default_db_path()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        plan = plan_store.load_plan(conn, args.plan_id)
    except plan_store.PlanNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(to_markdown(evaluate(plan)), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""写回记录列表（网页"历史"页用）与 jobs 表迁移。"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

from tests.apply.test_writer import APPLY, approved_plan, build_library
from tunetidy.apply.writer import Writer
from tunetidy.storage import runs as run_store
from tunetidy.storage.db import connect


def test_list_runs_newest_first(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    ids = []
    for i in range(2):
        b = build_library()
        plan = approved_plan(b, conn)
        times = iter([f"2026-11-0{i + 1}T00:00:0{n}+00:00" for n in range(9)] * 50)
        w = Writer(
            b, conn, APPLY, sleep=lambda _s: None, rng=random.Random(0), now=lambda t=times: next(t)
        )
        ids.append(w.execute(plan).run_id)
    assert [r.id for r in run_store.list_runs(conn)] == list(reversed(ids))
    assert [r.id for r in run_store.list_runs(conn, limit=1)] == [ids[1]]
    conn.close()


def test_jobs_table_accepts_undo_and_result(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO jobs (id, kind, status, pid, heartbeat_at, created_at, updated_at, result)"
        " VALUES ('j', 'undo', 'done', 1, 't', 't', 't', '{}')"
    )
    assert tuple(conn.execute("SELECT kind, result FROM jobs").fetchone()) == ("undo", "{}")
    conn.close()


def test_migration_keeps_existing_jobs(tmp_path: Path) -> None:
    """升级前已有的分析、写回任务记录，迁移后还在。"""
    from tunetidy.storage import db as db_mod

    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    for script in db_mod.MIGRATIONS[:-1]:
        for stmt in db_mod._statements(script):
            raw.execute(stmt)
    raw.execute(f"PRAGMA user_version = {len(db_mod.MIGRATIONS) - 1}")
    raw.execute(
        "INSERT INTO jobs (id, kind, status, pid, heartbeat_at, created_at, updated_at)"
        " VALUES ('old', 'apply', 'done', 1, 't', 't', 't')"
    )
    raw.commit()
    raw.close()
    conn = connect(path)
    rows = conn.execute("SELECT id, kind, status FROM jobs").fetchall()
    assert [tuple(r) for r in rows] == [("old", "apply", "done")]
    conn.close()

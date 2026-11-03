"""写回记录列表（网页"历史"页用）与 jobs 表迁移。"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

import pytest

from tests.apply.test_writer import APPLY, approved_plan, build_library
from tests.classify.helpers import song
from tests.fakes import FakeBackend
from tests.test_analyze import CFG, Clock
from tunetidy.analyze import analyze
from tunetidy.apply.writer import Writer
from tunetidy.models import User
from tunetidy.storage import plans as plan_store
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


# ------------------------------------------------------------------ 上次那批


def _two_sources() -> FakeBackend:
    b = build_library()
    b.add_playlist("200", "开车听", [song(1), song(2)])
    return b


def _build(
    conn: sqlite3.Connection, b: FakeBackend, order: list[str], account: str = "X"
) -> tuple[str, str]:
    """按 order 的来源整理一次并建好，返回 (plan_id, run_id)。"""
    b.user = User("netease", account, account)
    by_id = {p.id: p for p in b.list_playlists()}
    plan = analyze(b, conn, CFG, [by_id[i] for i in order], now=Clock())
    plan_store.record_approval(conn, plan.id, plan.content_hash, "t")
    w = Writer(b, conn, APPLY, sleep=lambda _s: None, rng=random.Random(0))
    return plan.id, w.execute(plan_store.load_plan(conn, plan.id)).run_id


def test_previous_batch_same_sources_any_order(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    b = _two_sources()
    _, run_a = _build(conn, b, ["100", "200"])
    plan_b, _ = _build(conn, b, ["200", "100"])
    prev = run_store.previous_batch(conn, plan_b, "X")
    assert prev is not None and prev.id == run_a


def test_previous_batch_only_current_account(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    b = _two_sources()
    _build(conn, b, ["100"], account="X")
    plan_b, _ = _build(conn, b, ["100"], account="Y")
    assert run_store.previous_batch(conn, plan_b, "Y") is None


def test_previous_batch_skips_runs_without_account(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    b = _two_sources()
    _, run_a = _build(conn, b, ["100"])
    conn.execute("UPDATE runs SET account_id = NULL WHERE id = ?", (run_a,))
    plan_b, _ = _build(conn, b, ["100"])
    assert run_store.previous_batch(conn, plan_b, "X") is None


@pytest.mark.parametrize("status", ["undone", "undoing", "interrupted", "running"])
def test_previous_batch_skips_unfinished_or_undone(tmp_path: Path, status: str) -> None:
    conn = connect(tmp_path / "t.db")
    b = _two_sources()
    _, run_a = _build(conn, b, ["100"])
    run_store.set_run_status(conn, run_a, status, "t")
    plan_b, _ = _build(conn, b, ["100"])
    assert run_store.previous_batch(conn, plan_b, "X") is None


def test_previous_batch_needs_same_source_set(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    b = _two_sources()
    _build(conn, b, ["100", "200"])
    plan_b, _ = _build(conn, b, ["100"])
    assert run_store.previous_batch(conn, plan_b, "X") is None


def test_previous_batch_is_the_latest(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    b = _two_sources()
    _build(conn, b, ["100"])
    _, run_a2 = _build(conn, b, ["100"])
    plan_b, _ = _build(conn, b, ["100"])
    prev = run_store.previous_batch(conn, plan_b, "X")
    assert prev is not None and prev.id == run_a2


def test_previous_batch_only_after_this_one_is_built(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    b = _two_sources()
    _build(conn, b, ["100"])
    draft = analyze(b, conn, CFG, [b.list_playlists()[0]], now=Clock())
    assert run_store.previous_batch(conn, draft.id, "X") is None

"""plans 表的更新时间：首页按它找出每个来源最近的一份整理。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.apply.test_writer import build_library
from tests.test_analyze import CFG, Clock
from tunetidy.analyze import analyze, move_songs
from tunetidy.models import SongKey
from tunetidy.plan import Plan, PlanStatus
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import connect


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def new_plan(conn: sqlite3.Connection) -> Plan:
    b = build_library()
    return analyze(b, conn, CFG, [b.list_playlists()[0]], now=Clock())


def updated_at(conn: sqlite3.Connection, plan_id: str) -> str:
    row = conn.execute("SELECT updated_at FROM plans WHERE id = ?", (plan_id,)).fetchone()
    return str(row["updated_at"])


def test_updated_at_starts_at_created_at(conn: sqlite3.Connection) -> None:
    plan = new_plan(conn)
    assert updated_at(conn, plan.id) == plan.created_at


def test_edit_bumps_updated_at(conn: sqlite3.Connection) -> None:
    plan = new_plan(conn)
    move_songs(conn, plan.id, [SongKey("netease", "1")], "R&B")
    assert updated_at(conn, plan.id) > plan.created_at


def test_approval_and_transition_bump_updated_at(conn: sqlite3.Connection) -> None:
    a, b = new_plan(conn), new_plan(conn)
    plan_store.record_approval(conn, a.id, a.content_hash, "t")
    assert updated_at(conn, a.id) > a.created_at
    plan_store.transition(conn, b.id, (PlanStatus.DRAFT,), PlanStatus.APPROVED)
    assert updated_at(conn, b.id) > b.created_at


def test_list_orders_by_updated_at(conn: sqlite3.Connection) -> None:
    first, second = new_plan(conn), new_plan(conn)
    move_songs(conn, first.id, [SongKey("netease", "1")], "R&B")
    rows = plan_store.list_plans(conn)
    assert [r["id"] for r in rows][0] == first.id
    assert {r["id"] for r in rows} == {first.id, second.id}
    assert rows[0]["updated_at"] > rows[0]["created_at"]

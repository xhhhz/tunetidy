"""手动清理旧整理：只删同一来源、还没建歌单的草稿，保留正在看的这份。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.classify.helpers import song
from tests.fakes import FakeBackend
from tests.test_analyze import CFG, Clock, library
from tunetidy.analyze import analyze
from tunetidy.plan import Plan, PlanStatus
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import connect


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def b() -> FakeBackend:
    lib = library()
    lib.add_playlist("200", "开车听", [song(1), song(2)])
    return lib


def make(conn: sqlite3.Connection, b: FakeBackend, order: list[str]) -> Plan:
    by_id = {p.id: p for p in b.list_playlists()}
    return analyze(b, conn, CFG, [by_id[i] for i in order], now=Clock())


def set_status(conn: sqlite3.Connection, plan_id: str, status: PlanStatus) -> None:
    conn.execute("UPDATE plans SET status = ? WHERE id = ?", (status.value, plan_id))


def ids(conn: sqlite3.Connection) -> set[str]:
    return {r["id"] for r in conn.execute("SELECT id FROM plans")}


def test_deletes_other_drafts_of_same_source(conn: sqlite3.Connection, b: FakeBackend) -> None:
    keep = make(conn, b, ["100", "200"])
    old = [make(conn, b, ["100", "200"]), make(conn, b, ["200", "100"])]  # 顺序不同也算同一来源
    built = {}
    for status in (PlanStatus.APPROVED, PlanStatus.APPLYING, PlanStatus.APPLIED, PlanStatus.UNDONE):
        p = make(conn, b, ["100", "200"])
        set_status(conn, p.id, status)
        built[status] = p.id
    other = make(conn, b, ["100"])  # 别的来源
    assert plan_store.delete_drafts(conn, ["200", "100"], keep=keep.id) == 2
    assert ids(conn) == {keep.id, other.id, *built.values()}
    gone = [p.id for p in old]
    left = conn.execute(
        f"SELECT COUNT(*) FROM assignments WHERE plan_id IN ({','.join('?' * len(gone))})", gone
    ).fetchone()[0]
    assert left == 0


def test_keeps_draft_approved_in_the_meantime(conn: sqlite3.Connection, b: FakeBackend) -> None:
    keep = make(conn, b, ["100"])
    raced = make(conn, b, ["100"])
    plan_store.record_approval(conn, raced.id, raced.content_hash, "t")  # 终端里刚确认
    assert plan_store.delete_drafts(conn, ["100"], keep=keep.id) == 0
    assert raced.id in ids(conn)


def test_nothing_to_delete(conn: sqlite3.Connection, b: FakeBackend) -> None:
    keep = make(conn, b, ["100"])
    assert plan_store.delete_drafts(conn, ["100"], keep=keep.id) == 0
    assert ids(conn) == {keep.id}

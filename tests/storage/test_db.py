import sqlite3
import threading
from pathlib import Path

import pytest

from tunetidy.storage import db
from tunetidy.storage.db import MIGRATIONS, connect

TABLES = {
    "songs", "song_signals", "plans", "assignments", "runs", "run_playlists", "run_tracks",
    "style_trees", "jobs",
}  # fmt: skip


def tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r["name"] for r in rows}


def test_fresh_database_has_schema_and_wal(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    assert tables(conn) == TABLES
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    conn.close()


def test_reopen_is_idempotent(tmp_path: Path) -> None:
    connect(tmp_path / "t.db").close()
    conn = connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    conn.close()


def test_concurrent_first_open_does_not_fail(tmp_path: Path) -> None:
    """多个进程同时首次打开数据库，不能因为重复建表报错。"""
    path = tmp_path / "t.db"
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def open_db() -> None:
        try:
            barrier.wait()
            connect(path).close()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=open_db) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    conn = connect(path)
    assert tables(conn) == TABLES
    conn.close()


def test_failed_migration_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connect(tmp_path / "t.db").close()
    monkeypatch.setattr(db, "MIGRATIONS", [*MIGRATIONS, "CREATE TABLE extra (x); BROKEN SQL"])
    with pytest.raises(sqlite3.OperationalError):
        connect(tmp_path / "t.db")
    monkeypatch.setattr(db, "MIGRATIONS", MIGRATIONS)
    conn = connect(tmp_path / "t.db")
    assert "extra" not in tables(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    conn.close()


def test_constraints_reject_bad_values(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO songs VALUES ('netease', '1', 'x', '[]', NULL, NULL, NULL, NULL,"
            " NULL, NULL, 'grey', '2026-10-30')"
        )
    with pytest.raises(sqlite3.IntegrityError):  # 外键：没有对应的 run_playlists
        conn.execute("INSERT INTO run_tracks VALUES ('r', 'c', 'netease', '1', 0, 'pending', NULL)")
    conn.close()


def test_concurrent_writers_wait_instead_of_failing(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    a = connect(path)
    a.execute("BEGIN IMMEDIATE")
    a.execute(
        "INSERT INTO songs VALUES ('netease', '1', 'x', '[]', NULL, NULL, NULL, NULL,"
        " NULL, NULL, 'normal', 't')"
    )
    done = threading.Event()

    def writer() -> None:
        b = connect(path)  # sqlite3 连接不能跨线程使用
        started.set()
        b.execute(
            "INSERT INTO songs VALUES ('netease', '2', 'y', '[]', NULL, NULL, NULL, NULL,"
            " NULL, NULL, 'normal', 't')"
        )
        b.close()
        done.set()

    started = threading.Event()
    t = threading.Thread(target=writer)
    t.start()
    assert started.wait(5)
    assert not done.wait(0.3)  # b 在等 a 的写锁
    a.execute("COMMIT")
    t.join(5)
    assert done.is_set()
    assert a.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == 2
    a.close()


def test_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    from tunetidy.storage.db import transaction

    conn = connect(tmp_path / "t.db")
    with pytest.raises(RuntimeError), transaction(conn):
        conn.execute(
            "INSERT INTO songs VALUES ('netease', '1', 'x', '[]', NULL, NULL, NULL, NULL,"
            " NULL, NULL, 'normal', 't')"
        )
        raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == 0
    assert not conn.in_transaction
    conn.close()

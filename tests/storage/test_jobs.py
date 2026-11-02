"""后台任务表：跨进程只跑一个、心跳与进程检测、中断恢复。"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tunetidy.storage import jobs
from tunetidy.storage.db import connect

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
ME = os.getpid()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = connect(db)
    yield c
    c.close()


def alive(_pid: int) -> bool:
    return True


def test_create_and_progress(conn: sqlite3.Connection) -> None:
    j = jobs.create_job(conn, "analyze", pid=ME, now=T0)
    assert j.status == "running" and j.kind == "analyze" and j.pid == ME
    jobs.update_progress(conn, j.id, "tags", 3, 10, now=T0 + timedelta(seconds=5))
    got = jobs.load_job(conn, j.id)
    assert (got.phase, got.done, got.total) == ("tags", 3, 10)
    assert got.heartbeat_at > j.heartbeat_at  # 进度更新同时刷新心跳


def test_heartbeat_only(conn: sqlite3.Connection) -> None:
    j = jobs.create_job(conn, "analyze", pid=ME, now=T0)
    jobs.heartbeat(conn, j.id, T0 + timedelta(seconds=30))
    assert jobs.load_job(conn, j.id).heartbeat_at > j.heartbeat_at


def test_finish_and_fail(conn: sqlite3.Connection) -> None:
    a = jobs.create_job(conn, "analyze", pid=ME, now=T0)
    jobs.finish_job(conn, a.id, plan_id="p1", now=T0)
    assert jobs.load_job(conn, a.id).status == "done"
    assert jobs.load_job(conn, a.id).plan_id == "p1"
    b = jobs.create_job(conn, "apply", plan_id="p1", pid=ME, now=T0)
    jobs.fail_job(conn, b.id, "确认已失效", now=T0)
    got = jobs.load_job(conn, b.id)
    assert got.status == "failed" and got.error == "确认已失效"


def test_only_one_running_job(conn: sqlite3.Connection) -> None:
    first = jobs.create_job(conn, "analyze", pid=ME, now=T0, alive=alive)
    with pytest.raises(jobs.JobBusyError, match=first.id):
        jobs.create_job(conn, "apply", plan_id="p", pid=ME, now=T0, alive=alive)
    jobs.finish_job(conn, first.id, now=T0)
    jobs.create_job(conn, "apply", plan_id="p", pid=ME, now=T0, alive=alive)


def test_only_one_running_job_across_connections(db: Path) -> None:
    """互斥以数据库为准：另一个连接（相当于另一个进程）看得到正在运行的任务。"""
    a, b = connect(db), connect(db)
    try:
        jobs.create_job(a, "analyze", pid=ME, now=T0, alive=alive)
        with pytest.raises(jobs.JobBusyError):
            jobs.create_job(b, "analyze", pid=ME, now=T0, alive=alive)
    finally:
        a.close()
        b.close()


def test_stale_heartbeat_is_interrupted(conn: sqlite3.Connection) -> None:
    j = jobs.create_job(conn, "analyze", pid=ME, now=T0, alive=alive)
    assert jobs.mark_orphans(conn, T0 + timedelta(seconds=59), alive=alive) == 0
    assert jobs.mark_orphans(conn, T0 + timedelta(seconds=61), alive=alive) == 1
    assert jobs.load_job(conn, j.id).status == "interrupted"


def test_dead_process_is_interrupted(conn: sqlite3.Connection) -> None:
    j = jobs.create_job(conn, "analyze", pid=ME, now=T0, alive=alive)
    assert jobs.mark_orphans(conn, T0, alive=lambda _pid: False) == 1
    assert jobs.load_job(conn, j.id).status == "interrupted"
    jobs.create_job(conn, "analyze", pid=ME, now=T0, alive=alive)  # 之后能启动新任务


def test_killed_process_frees_the_slot(db: Path) -> None:
    """子进程建了任务后被强杀：本进程再建任务时，旧任务被标为中断，新任务能启动。"""
    script = textwrap.dedent(
        f"""
        import os, sys, time
        from datetime import UTC, datetime
        from pathlib import Path
        from tunetidy.storage import jobs
        from tunetidy.storage.db import connect
        conn = connect(Path({str(db)!r}))
        j = jobs.create_job(conn, "analyze", pid=os.getpid(), now=datetime.now(UTC))
        print(j.id, flush=True)
        time.sleep(60)
        """
    )
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    assert child.stdout is not None
    old_id = child.stdout.readline().strip()
    os.kill(child.pid, signal.SIGKILL)
    child.wait(timeout=10)

    conn = connect(db)
    try:
        new = jobs.create_job(conn, "analyze", pid=ME, now=datetime.now(UTC))
        assert jobs.load_job(conn, old_id).status == "interrupted"
        assert new.status == "running"
    finally:
        conn.close()


def test_unknown_job(conn: sqlite3.Connection) -> None:
    with pytest.raises(jobs.JobNotFoundError):
        jobs.load_job(conn, "nope")

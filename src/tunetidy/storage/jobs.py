"""后台任务（分析、写回）的读写。

同一时间只允许一个 running 任务，跨进程以数据库为准：检查和插入在同一个 BEGIN IMMEDIATE
写事务里完成。进程退出或心跳超时的 running 任务由 mark_orphans 标为 interrupted；任何入口
启动时、以及每次建任务前都会调用它。
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from tunetidy.storage.db import transaction

Kind = Literal["analyze", "apply", "undo"]
HEARTBEAT_TIMEOUT = timedelta(seconds=60)


@dataclass(frozen=True)
class Job:
    id: str
    kind: Kind
    status: str
    phase: str | None
    done: int
    total: int
    plan_id: str | None
    run_id: str | None
    error: str | None
    pid: int
    heartbeat_at: str
    created_at: str
    updated_at: str
    result: str | None = None  # JSON：分析任务的进度统计，撤销任务的结果


class JobBusyError(RuntimeError):
    pass


class JobNotFoundError(LookupError):
    pass


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # 进程存在，只是不属于当前用户
        return True
    return True


def _mark_orphans(conn: sqlite3.Connection, now: datetime, alive: Callable[[int], bool]) -> int:
    stale_before = _iso(now - HEARTBEAT_TIMEOUT)
    rows = conn.execute(
        "SELECT id, pid, heartbeat_at FROM jobs WHERE status = 'running'"
    ).fetchall()
    orphans = [r["id"] for r in rows if r["heartbeat_at"] < stale_before or not alive(r["pid"])]
    conn.executemany(
        "UPDATE jobs SET status = 'interrupted', updated_at = ? WHERE id = ?",
        [(_iso(now), i) for i in orphans],
    )
    return len(orphans)


def mark_orphans(
    conn: sqlite3.Connection, now: datetime, alive: Callable[[int], bool] = _pid_alive
) -> int:
    """把进程已不在、或心跳超时的 running 任务标为 interrupted，返回条数。"""
    with transaction(conn):
        return _mark_orphans(conn, now, alive)


def create_job(
    conn: sqlite3.Connection,
    kind: Kind,
    *,
    plan_id: str | None = None,
    pid: int,
    now: datetime,
    alive: Callable[[int], bool] = _pid_alive,
) -> Job:
    """建一个 running 任务；已有 running 任务时抛 JobBusyError。"""
    job_id = f"{now:%Y%m%d}-{secrets.token_hex(3)}"
    with transaction(conn):
        _mark_orphans(conn, now, alive)
        busy = conn.execute("SELECT id, kind FROM jobs WHERE status = 'running'").fetchone()
        if busy:
            kind_text = {"analyze": "分析", "apply": "建歌单", "undo": "撤销"}[busy["kind"]]
            raise JobBusyError(f"已有一个{kind_text}任务在运行（{busy['id']}），等它结束后再试")
        t = _iso(now)
        conn.execute(
            "INSERT INTO jobs (id, kind, status, plan_id, pid, heartbeat_at, created_at,"
            " updated_at) VALUES (?, ?, 'running', ?, ?, ?, ?, ?)",
            (job_id, kind, plan_id, pid, t, t, t),
        )
    return load_job(conn, job_id)


def load_job(conn: sqlite3.Connection, job_id: str) -> Job:
    r = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if r is None:
        raise JobNotFoundError(f"找不到任务 {job_id}")
    return Job(**dict(zip(r.keys(), r, strict=True)))


def _update(conn: sqlite3.Connection, job_id: str, now: datetime, **fields: object) -> None:
    sets = "".join(f"{k} = ?, " for k in fields)
    with transaction(conn):
        conn.execute(
            f"UPDATE jobs SET {sets}heartbeat_at = ?, updated_at = ? WHERE id = ?",
            (*fields.values(), _iso(now), _iso(now), job_id),
        )


def heartbeat(conn: sqlite3.Connection, job_id: str, now: datetime) -> None:
    _update(conn, job_id, now)


def update_progress(
    conn: sqlite3.Connection, job_id: str, phase: str, done: int, total: int, *, now: datetime
) -> None:
    _update(conn, job_id, now, phase=phase, done=done, total=total)


def running_job(conn: sqlite3.Connection) -> Job | None:
    r = conn.execute("SELECT id FROM jobs WHERE status = 'running' LIMIT 1").fetchone()
    return load_job(conn, r["id"]) if r else None


def set_result(conn: sqlite3.Connection, job_id: str, result: str, *, now: datetime) -> None:
    _update(conn, job_id, now, result=result)


def set_run(conn: sqlite3.Connection, job_id: str, run_id: str, *, now: datetime) -> None:
    _update(conn, job_id, now, run_id=run_id)


def finish_job(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    plan_id: str | None = None,
    run_id: str | None = None,
    result: str | None = None,
    now: datetime,
) -> None:
    fields: dict[str, object] = {"status": "done"}
    if result is not None:
        fields["result"] = result
    if plan_id is not None:
        fields["plan_id"] = plan_id
    if run_id is not None:
        fields["run_id"] = run_id
    _update(conn, job_id, now, **fields)


def fail_job(conn: sqlite3.Connection, job_id: str, error: str, *, now: datetime) -> None:
    _update(conn, job_id, now, status="failed", error=error)

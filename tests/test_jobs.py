"""后台任务：分析与建歌单在线程里跑，进度写进 jobs 表；写回前在线程里再核对一次确认。"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.apply.test_writer import APPLY, build_library
from tests.fakes import FakeBackend
from tests.test_analyze import Clock
from tunetidy import jobs
from tunetidy.analyze import analyze, move_songs
from tunetidy.apply.approval import ApprovalError
from tunetidy.backends.base import BackendError
from tunetidy.classify.split import SplitSettings
from tunetidy.config import ClassifyConfig, Config
from tunetidy.models import SongKey
from tunetidy.plan import Plan, PlanStatus
from tunetidy.storage import jobs as job_store
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import connect

CONFIG = Config(classify=ClassifyConfig(min_size=2), apply=APPLY)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = connect(db)
    yield c
    c.close()


@pytest.fixture
def backend() -> FakeBackend:
    return build_library()


def approved(b: FakeBackend, conn: sqlite3.Connection) -> Plan:
    plan = analyze(b, conn, CONFIG, [b.list_playlists()[0]], now=Clock())
    plan_store.record_approval(conn, plan.id, plan.content_hash, "t")
    return plan_store.load_plan(conn, plan.id)


def run(
    start: Callable[[], tuple[str, threading.Thread]], conn: sqlite3.Connection
) -> job_store.Job:
    job_id, thread = start()
    thread.join(timeout=30)
    assert not thread.is_alive()
    return job_store.load_job(conn, job_id)


def test_analyze_job_creates_plan(db: Path, conn: sqlite3.Connection, backend: FakeBackend) -> None:
    src = [backend.list_playlists()[0]]
    job = run(lambda: jobs.start_analyze(db, CONFIG, backend, src, SplitSettings()), conn)
    assert job.status == "done" and job.kind == "analyze" and job.plan_id
    plan = plan_store.load_plan(conn, job.plan_id)
    assert {p.category for p in plan.playlists()} == {"流行", "R&B"}
    summary = jobs.job_summary(conn, job)
    assert summary["plan"]["id"] == job.plan_id
    assert summary["plan"]["playlists"][0]["count"] == 7
    assert len(summary["plan"]["playlists"][0]["samples"]) == 3
    assert summary["plan"]["pending"] == 3
    assert f"tunetidy approve {job.plan_id}" in summary["message"]


def test_apply_job_writes_approved_plan(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    plan = approved(backend, conn)
    before = len(backend.playlists)
    job = run(lambda: jobs.start_apply(db, CONFIG, backend, plan.id), conn)
    assert job.status == "done" and job.run_id
    assert len(backend.playlists) == before + 2
    assert plan_store.load_plan(conn, plan.id).status == PlanStatus.APPLIED
    summary = jobs.job_summary(conn, job)
    assert [p["count"] for p in summary["created"]] == [7, 5]
    assert f"tunetidy undo {job.run_id}" in summary["message"]


def test_apply_refuses_draft(db: Path, conn: sqlite3.Connection, backend: FakeBackend) -> None:
    plan = analyze(backend, conn, CONFIG, [backend.list_playlists()[0]], now=Clock())
    with pytest.raises(ApprovalError, match="tunetidy approve"):
        jobs.start_apply(db, CONFIG, backend, plan.id)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_apply_refuses_changed_after_approval(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    plan = approved(backend, conn)
    move_songs(conn, plan.id, [SongKey("netease", "1")], "R&B")  # 改动使确认作废
    with pytest.raises(ApprovalError):
        jobs.start_apply(db, CONFIG, backend, plan.id)


def test_apply_refuses_interrupted_plan_and_says_how_to_resume(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    plan = approved(backend, conn)
    plan_store.transition(conn, plan.id, (PlanStatus.APPROVED,), PlanStatus.APPLYING)
    with pytest.raises(ApprovalError, match=f"tunetidy apply {plan.id}"):
        jobs.start_apply(db, CONFIG, backend, plan.id)


def test_apply_rechecks_in_thread(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """主线程检查通过后、线程开始前，用户在网页上改了整理：线程里核对失败，不写账号。"""
    plan = approved(backend, conn)
    spawn = jobs._spawn

    def edit_then_spawn(target: Callable[[], None]) -> threading.Thread:
        move_songs(conn, plan.id, [SongKey("netease", "1")], "R&B")
        return spawn(target)

    monkeypatch.setattr(jobs, "_spawn", edit_then_spawn)
    before = len(backend.playlists)
    job = run(lambda: jobs.start_apply(db, CONFIG, backend, plan.id), conn)
    assert job.status == "failed" and job.error and "tunetidy approve" in job.error
    assert len(backend.playlists) == before


def test_second_job_is_rejected(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()
    spawn = jobs._spawn

    def held(target: Callable[[], None]) -> threading.Thread:
        return spawn(lambda: (gate.wait(10), target()))  # type: ignore[func-returns-value]

    monkeypatch.setattr(jobs, "_spawn", held)
    src = [backend.list_playlists()[0]]
    job_id, thread = jobs.start_analyze(db, CONFIG, backend, src, SplitSettings())
    try:
        with pytest.raises(job_store.JobBusyError):
            jobs.start_analyze(db, CONFIG, backend, src, SplitSettings())
    finally:
        gate.set()
        thread.join(timeout=30)
    assert job_store.load_job(conn, job_id).status == "done"


def test_two_applies_on_same_plan_one_wins(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    plan = approved(backend, conn)
    run(lambda: jobs.start_apply(db, CONFIG, backend, plan.id), conn)
    with pytest.raises(ApprovalError):
        jobs.start_apply(db, CONFIG, backend, plan.id)


def test_interrupted_apply_summary_tells_how_to_resume(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    plan = approved(backend, conn)
    from datetime import UTC, datetime

    job = job_store.create_job(conn, "apply", plan_id=plan.id, pid=1, now=datetime.now(UTC))
    job_store.mark_orphans(conn, datetime.now(UTC), alive=lambda _p: False)
    summary = jobs.job_summary(conn, job_store.load_job(conn, job.id))
    assert summary["status"] == "interrupted"
    assert f"tunetidy apply {plan.id}" in summary["message"]


class BrokenBackend(FakeBackend):
    def get_tracks(self, playlist_id: str):  # type: ignore[no-untyped-def]
        raise BackendError("网易云返回错误：服务繁忙")


def test_backend_error_fails_job_without_secrets(db: Path, conn: sqlite3.Connection) -> None:
    b = build_library()
    broken = BrokenBackend(style_tree=[])
    broken.playlists = b.playlists
    job = run(
        lambda: jobs.start_analyze(db, CONFIG, broken, [b.list_playlists()[0]], SplitSettings()),
        conn,
    )
    assert job.status == "failed"
    assert job.error == "网易云返回错误：服务繁忙"
    assert "MUSIC_U" not in (job.error or "") and "Traceback" not in (job.error or "")


class SlowBackend(FakeBackend):
    """读歌单时卡住，直到测试放行：模拟分析进行中。"""

    gate = threading.Event()

    def get_tracks(self, playlist_id: str):  # type: ignore[no-untyped-def]
        self.gate.wait(10)
        return super().get_tracks(playlist_id)


def test_heartbeat_keeps_running_job_alive(
    db: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """任务运行期间心跳持续刷新，不会被别的入口误判为中断。"""
    import time

    monkeypatch.setattr(jobs, "HEARTBEAT_EVERY", 0.05)
    b = build_library()
    slow = SlowBackend(style_tree=b.style_tree)
    slow.playlists, slow.tags = b.playlists, b.tags
    src = [b.list_playlists()[0]]
    job_id, thread = jobs.start_analyze(db, CONFIG, slow, src, SplitSettings())
    first = job_store.load_job(conn, job_id).heartbeat_at
    time.sleep(1.2)
    try:
        assert job_store.load_job(conn, job_id).heartbeat_at > first
        assert job_store.load_job(conn, job_id).status == "running"
    finally:
        SlowBackend.gate.set()
        thread.join(timeout=30)
    assert job_store.load_job(conn, job_id).status == "done"

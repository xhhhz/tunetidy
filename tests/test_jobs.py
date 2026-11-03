"""后台任务：分析与建歌单在线程里跑，进度写进 jobs 表；写回前在线程里再核对一次确认。"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.apply.test_writer import APPLY, build_library
from tests.classify.helpers import song
from tests.fakes import FakeBackend
from tests.test_analyze import Clock
from tunetidy import jobs
from tunetidy.analyze import analyze, move_songs
from tunetidy.apply.approval import ApprovalError
from tunetidy.apply.undo import UndoError, plan_undo
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


# ---------------------------------------------------------------- 撤销、续传、每个歌单的进度


def interrupted(b: FakeBackend, conn: sqlite3.Connection, db: Path) -> Plan:
    """整理停在"正在创建"：第 3 批加歌前网络断了。"""
    plan = approved(b, conn)
    b.fail_before["add_tracks"] = {3}
    job = run(lambda: jobs.start_apply(db, CONFIG, b, plan.id), conn)
    assert job.status == "failed"
    b.fail_before["add_tracks"] = set()
    assert plan_store.load_plan(conn, plan.id).status == PlanStatus.APPLYING
    return plan


def test_resume_finishes_without_duplicates(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    plan = interrupted(backend, conn, db)
    job = run(lambda: jobs.start_apply(db, CONFIG, backend, plan.id, resume=True), conn)
    assert job.status == "done"
    assert len(backend.owned_by_tool()) == 2
    assert plan_store.load_plan(conn, plan.id).status == PlanStatus.APPLIED
    statuses = {r[0] for r in conn.execute("SELECT status FROM run_tracks").fetchall()}
    assert statuses == {"added"}


def test_without_resume_flag_interrupted_plan_is_still_refused(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    """MCP 不续传：不带 resume 的调用行为不变。"""
    plan = interrupted(backend, conn, db)
    with pytest.raises(ApprovalError, match=f"tunetidy apply {plan.id}"):
        jobs.start_apply(db, CONFIG, backend, plan.id)


def test_resume_refuses_plan_that_is_not_interrupted(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    plan = approved(backend, conn)
    with pytest.raises(ApprovalError, match="没有中断"):
        jobs.start_apply(db, CONFIG, backend, plan.id, resume=True)


def test_apply_summary_has_per_playlist_progress(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    plan = interrupted(backend, conn, db)
    job = job_store.load_job(conn, conn.execute("SELECT id FROM jobs").fetchone()[0])
    rows = jobs.job_summary(conn, job)["playlists"]
    assert [r["name"] for r in rows] == [p.name for p in plan.playlists()]
    assert all(set(r) == {"name", "added", "total", "status"} for r in rows)
    assert rows[0]["total"] == 7 and 0 < rows[0]["added"] < 7 and rows[0]["status"] == "添加中"
    assert rows[1]["added"] == 0 and rows[1]["status"] == "等待中"


def applied_run(b: FakeBackend, conn: sqlite3.Connection, db: Path) -> tuple[Plan, str]:
    plan = approved(b, conn)
    job = run(lambda: jobs.start_apply(db, CONFIG, b, plan.id), conn)
    assert job.run_id
    return plan, job.run_id


def test_undo_job_deletes_and_marks_plan_undone(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    plan, run_id = applied_run(backend, conn, db)
    fp = jobs.undo_fingerprint(plan_undo(backend, conn, run_id))
    job = run(lambda: jobs.start_undo(db, CONFIG, backend, run_id, fp), conn)
    assert job.status == "done" and job.kind == "undo"
    assert backend.owned_by_tool() == []
    assert plan_store.load_plan(conn, plan.id).status == PlanStatus.UNDONE
    summary = jobs.job_summary(conn, job)
    assert len(summary["deleted"]) == 2 and summary["trimmed"] == []


def test_undo_job_refuses_when_preview_changed(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    """预览之后歌单变了（用户往里加了歌）：指纹不同，任务失败，什么都不删。"""
    plan, run_id = applied_run(backend, conn, db)
    fp = jobs.undo_fingerprint(plan_undo(backend, conn, run_id))
    backend.owned_by_tool()[0].songs.append(song(99))
    job = run(lambda: jobs.start_undo(db, CONFIG, backend, run_id, fp), conn)
    assert job.status == "failed" and "重新预览" in (job.error or "")
    assert len(backend.owned_by_tool()) == 2


def test_undo_of_undone_run_is_refused(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    _plan, run_id = applied_run(backend, conn, db)
    fp = jobs.undo_fingerprint(plan_undo(backend, conn, run_id))
    run(lambda: jobs.start_undo(db, CONFIG, backend, run_id, fp), conn)
    with pytest.raises(UndoError, match="已经撤销"):
        jobs.start_undo(db, CONFIG, backend, run_id, fp)


# ------------------------------------------------------------ 读取进度：曲风统计、剩余时间、取消


def test_live_genres_match_final_result(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    """进度页最后一次统计与生成的整理一致：单一曲风、并列、无标签、云盘、下架，
    以及一首不到 min_size、最后被并进「其他」的摇滚。"""
    from tests.classify.helpers import tags
    from tunetidy.classify.pipeline import summarize

    backend.tags["20"] = tags(20, "摇滚")
    backend.playlists["100"].songs.append(song(20))
    # 先缓存一部分，让统计同时来自缓存和联网
    analyze(backend, conn, CONFIG, [backend.list_playlists()[0]], now=Clock())
    conn.execute("DELETE FROM song_signals WHERE song_id IN ('1', '8', '13', '20')")
    conn.commit()
    src = [backend.list_playlists()[0]]
    job = run(lambda: jobs.start_analyze(db, CONFIG, backend, src, SplitSettings()), conn)
    assert job.status == "done" and job.plan_id
    live = json.loads(job.result or "{}")
    final = summarize(plan_store.load_plan(conn, job.plan_id).assignments)
    assert dict(live["categories"]) == final["categories"]
    assert [c for c, _ in live["categories"]] == list(final["categories"])  # 顺序也一样
    assert "摇滚" not in dict(live["categories"])
    assert live["pending"] == final["pending"]
    assert live["cached"] == 14  # 18 首里 4 首要联网
    assert live["sources"] == ["我喜欢的音乐"]


def _job(result: dict[str, object], done: int, total: int, created: str) -> job_store.Job:
    return job_store.Job(
        id="j", kind="analyze", status="running", phase="tags", done=done, total=total,
        plan_id=None, run_id=None, error=None, pid=1, heartbeat_at=created,
        created_at=created, updated_at=created, result=json.dumps(result),
    )  # fmt: skip


def test_eta_counts_only_network_reads(conn: sqlite3.Connection) -> None:
    """10 首里 5 首已缓存；读歌单阶段很慢（任务 100 秒前创建）；联网读完 2 首用了 4 秒。"""
    started = datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)
    job = _job(
        {"cached": 5, "tags_started_at": started.isoformat(), "categories": [], "pending": 0},
        done=2,
        total=5,
        created=(started - timedelta(seconds=100)).isoformat(),
    )
    s = jobs.job_summary(conn, job, now=started + timedelta(seconds=4))
    assert s["eta_seconds"] == 6  # 剩 3 首 × 每首 2 秒
    assert s["eta_seconds"] != round(3 * 4 / 7)  # 把缓存的歌算进速度会低估
    assert s["eta_seconds"] != round(3 * 104 / 2)  # 按创建时间算会高估
    assert s["cached"] == 5


def test_eta_unknown_before_first_network_read(conn: sqlite3.Connection) -> None:
    started = datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)
    job = _job({"cached": 0, "tags_started_at": started.isoformat()}, 0, 5, started.isoformat())
    assert jobs.job_summary(conn, job, now=started)["eta_seconds"] is None


class BlockingTags(FakeBackend):
    """取到第 3 首时停住，等测试放行。"""

    gate: threading.Event
    reached: threading.Event

    def get_song_tags(self, song_ids):  # type: ignore[no-untyped-def]
        for n, t in enumerate(super().get_song_tags(song_ids), 1):
            if n == 3:
                self.reached.set()
                self.gate.wait(5)
            yield t


def test_cancel_analyze(db: Path, conn: sqlite3.Connection, backend: FakeBackend) -> None:
    b = BlockingTags(style_tree=backend.style_tree, playlists=backend.playlists, tags=backend.tags)
    b.gate, b.reached = threading.Event(), threading.Event()
    src = [b.list_playlists()[0]]
    job_id, thread = jobs.start_analyze(db, CONFIG, b, src, SplitSettings())
    assert b.reached.wait(5)
    jobs.cancel_job(job_id)
    b.gate.set()
    thread.join(5)
    job = job_store.load_job(conn, job_id)
    assert job.status == "failed"
    assert job.error and job.error.startswith("已取消")
    cached = conn.execute("SELECT COUNT(*) FROM song_signals").fetchone()[0]
    assert cached == 3  # 取消前读到的已存进缓存
    # 取消后马上能开始新的读取
    run(lambda: jobs.start_analyze(db, CONFIG, backend, src, SplitSettings()), conn)


def test_cancel_only_own_analyze_jobs(
    db: Path, conn: sqlite3.Connection, backend: FakeBackend
) -> None:
    with pytest.raises(ValueError, match="只能取消"):
        jobs.cancel_job("nope")
    plan = approved(backend, conn)
    job_id, thread = jobs.start_apply(db, CONFIG, backend, plan.id)
    with pytest.raises(ValueError, match="只能取消"):
        jobs.cancel_job(job_id)
    thread.join(10)

"""后台任务：分析和建歌单可能跑几分钟到二十几分钟，超过 AI 助手单次工具调用的等待时间，
所以这里只启动任务、立即返回任务 id，进度写进 jobs 表供查询（MCP 用）。

任务在线程里运行，线程自开数据库连接；另有一个心跳线程定时刷新 heartbeat_at，进程被关掉后
心跳停止，其他入口启动时会把它标为已中断。建歌单只接受已确认且内容未变的整理：启动前在
调用方线程检查一次，线程里由 Writer.execute 按最新内容再核对一次。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tunetidy.analyze import analyze
from tunetidy.apply.approval import ApprovalError, check_executable
from tunetidy.apply.undo import UndoError, UndoPlan, execute_undo, plan_undo
from tunetidy.apply.writer import ApplyError, Writer
from tunetidy.backends.base import BackendError, MusicBackend
from tunetidy.classify.split import SplitError, SplitSettings
from tunetidy.config import Config
from tunetidy.models import PlaylistInfo, SongKey
from tunetidy.plan import PlanStatus
from tunetidy.storage import jobs as job_store
from tunetidy.storage import plans as plan_store
from tunetidy.storage import repo
from tunetidy.storage import runs as run_store
from tunetidy.storage.db import connect

HEARTBEAT_EVERY = 10.0  # 秒；超时判定见 storage.jobs.HEARTBEAT_TIMEOUT
SAMPLES = 3  # 摘要里每个歌单列几首样例
KNOWN_ERRORS = (
    BackendError,
    ApprovalError,
    ApplyError,
    UndoError,
    SplitError,
    ValueError,
    LookupError,
)
PHASES = {"tracks": "读取歌单", "tags": "获取曲风标签"}


def _now() -> datetime:
    return datetime.now(UTC)


def _spawn(target: Callable[[], None]) -> threading.Thread:
    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t


def _error_text(e: BaseException) -> str:
    """只取已知错误的说明文字；其他错误只报类型，不带调用栈和可能含凭据的细节。"""
    if isinstance(e, KNOWN_ERRORS):
        return str(e)
    return f"出现意外错误（{type(e).__name__}），请在终端重新运行对应命令查看"


def _run_job(
    db_path: Path, job_id: str, work: Callable[[sqlite3.Connection], dict[str, Any]]
) -> None:
    """在线程里执行 work（拿到本线程的连接），结束时记录完成或失败；同时维持心跳。"""
    stop = threading.Event()

    def beat() -> None:
        hb = connect(db_path, check_same_thread=False)
        try:
            while not stop.wait(HEARTBEAT_EVERY):
                try:
                    job_store.heartbeat(hb, job_id, _now())
                except sqlite3.Error:  # 数据库一时繁忙：下次再刷，不能让心跳线程退出
                    continue
        finally:
            hb.close()

    ticker = threading.Thread(target=beat, daemon=True)
    ticker.start()
    conn = connect(db_path)
    try:
        result = work(conn)
        job_store.finish_job(conn, job_id, now=_now(), **result)
    except BaseException as e:  # noqa: BLE001 — 线程里的任何失败都要记进任务表
        job_store.fail_job(conn, job_id, _error_text(e), now=_now())
        if not isinstance(e, KNOWN_ERRORS):
            print(f"tunetidy 任务 {job_id} 失败：{type(e).__name__}", file=sys.stderr)
    finally:
        stop.set()
        ticker.join(timeout=5)
        conn.close()


def _progress(conn: sqlite3.Connection, job_id: str) -> Callable[[str, int, int], None]:
    return lambda phase, done, total: job_store.update_progress(
        conn, job_id, phase, done, total, now=_now()
    )


def start_analyze(
    db_path: Path,
    config: Config,
    backend: MusicBackend,
    sources: list[PlaylistInfo],
    split: SplitSettings,
) -> tuple[str, threading.Thread]:
    """启动分析任务；已有任务在运行时抛 JobBusyError。"""
    conn = connect(db_path)
    try:
        job = job_store.create_job(conn, "analyze", pid=os.getpid(), now=_now())
    finally:
        conn.close()

    def work(c: sqlite3.Connection) -> dict[str, Any]:
        plan = analyze(backend, c, config, sources, on_progress=_progress(c, job.id), split=split)
        return {"plan_id": plan.id}

    return job.id, _spawn(lambda: _run_job(db_path, job.id, work))


def start_apply(
    db_path: Path, config: Config, backend: MusicBackend, plan_id: str, *, resume: bool = False
) -> tuple[str, threading.Thread]:
    """启动建歌单任务。只接受已确认且内容未变的整理。

    resume=True 只给网页用（人已经输过确认码）：只接受上次没建完的整理，从中断处继续。
    MCP 不传 resume，遇到没建完的整理仍然拒绝，让用户去终端续传。
    """
    conn = connect(db_path)
    try:
        plan = plan_store.load_plan(conn, plan_id)
        if resume:
            if plan.status != PlanStatus.APPLYING:
                raise ApprovalError(f"整理 {plan_id} 没有中断，无需继续")
            if plan.approved_hash != plan.content_hash:
                raise ApprovalError(f"整理 {plan_id} 的内容在确认后变了，不能继续")
        elif plan.status == PlanStatus.APPLYING:
            raise ApprovalError(
                f"这份整理上次没建完，续传要在终端里确认：请运行 tunetidy apply {plan_id}"
            )
        else:
            check_executable(plan)
        job = job_store.create_job(conn, "apply", plan_id=plan_id, pid=os.getpid(), now=_now())
    finally:
        conn.close()

    def work(c: sqlite3.Connection) -> dict[str, Any]:
        fresh = plan_store.load_plan(c, plan_id)  # 期间可能在网页上被改过，Writer 会再核对
        writer = Writer(backend, c, config.apply, on_progress=_progress(c, job.id))
        result = writer.resume(fresh) if resume else writer.execute(fresh)
        return {"run_id": result.run_id}

    return job.id, _spawn(lambda: _run_job(db_path, job.id, work))


def undo_fingerprint(up: UndoPlan) -> str:
    """撤销预览的指纹：每个歌单怎么处理、要移除哪些歌。确认码绑定它，执行前再核对。"""
    items = [[it.playlist.category, it.action, sorted(it.remove)] for it in up.items]
    return hashlib.sha256(json.dumps([up.run.id, items]).encode()).hexdigest()


def start_undo(
    db_path: Path, config: Config, backend: MusicBackend, run_id: str, expected: str
) -> tuple[str, threading.Thread]:
    """启动撤销任务（网页用，人已经输过确认码）。线程里重新回读，和确认时的预览不一致就不执行。"""
    conn = connect(db_path)
    try:
        run = run_store.load_run(conn, run_id)
        if run.status == "undone":
            raise UndoError(f"这次写回已经撤销过了（{run_id}）")
        job = job_store.create_job(conn, "undo", plan_id=run.plan_id, pid=os.getpid(), now=_now())
        job_store.set_run(conn, job.id, run_id, now=_now())
    finally:
        conn.close()

    def work(c: sqlite3.Connection) -> dict[str, Any]:
        up = plan_undo(backend, c, run_id)
        if undo_fingerprint(up) != expected:
            raise UndoError("歌单在你确认之后又变了，请重新预览后再撤销")
        r = execute_undo(backend, c, up)
        result = {
            "deleted": r.deleted,
            "trimmed": [{"name": n, "removed": k} for n, k in r.trimmed],
            "skipped": r.skipped,
            "warnings": r.warnings,
        }
        return {"run_id": run_id, "result": json.dumps(result, ensure_ascii=False)}

    return job.id, _spawn(lambda: _run_job(db_path, job.id, work))


# ------------------------------------------------------------------ 摘要


def _playlist_progress(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    """建歌单时每个歌单的进度：已加入 / 总数，状态为等待中、添加中、已完成。"""
    rows = []
    for rp in run_store.load_run(conn, run_id).playlists:
        tracks = run_store.tracks(conn, run_id, rp.category)
        added = sum(t.status == "added" for t in tracks)
        settled = sum(t.status in ("added", "unaddable") for t in tracks)
        status = "已完成" if settled == len(tracks) else "添加中" if settled else "等待中"
        rows.append({"name": rp.name, "added": added, "total": len(tracks), "status": status})
    return rows


def _plan_summary(conn: sqlite3.Connection, plan_id: str) -> dict[str, Any]:
    plan = plan_store.load_plan(conn, plan_id)
    playlists = plan.playlists()
    sample_keys = [k for p in playlists for k in p.songs[:SAMPLES]]
    songs = repo.get_songs(conn, sample_keys)

    def label(k: SongKey) -> str:
        s = songs.get(k)
        if s is None:
            return k.id
        artists = " / ".join(a.name for a in s.artists if a.name)
        return f"{s.name} - {artists}" if artists else s.name

    return {
        "id": plan.id,
        "split": SplitSettings.from_dict(plan.settings.get("split", {})).describe(),
        "total": len(plan.assignments),
        "playlists": [
            {
                "category": p.category,
                "name": p.name,
                "count": len(p.songs),
                "samples": [label(k) for k in p.songs[:SAMPLES]],
            }
            for p in playlists
        ],
        "pending": len(plan.pending()),
        "unavailable": len(plan.unavailable()),
        "ungrouped": len(plan.ungrouped()),
    }


def _run_summary(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    run = run_store.load_run(conn, run_id)
    created = []
    unaddable = 0
    for rp in run.playlists:
        tracks = run_store.tracks(conn, run_id, rp.category)
        created.append(
            {
                "name": rp.name,
                "playlist_id": rp.playlist_id,
                "count": sum(t.status == "added" for t in tracks),
            }
        )
        unaddable += sum(t.status == "unaddable" for t in tracks)
    return {"created": created, "unaddable": unaddable}


def job_summary(conn: sqlite3.Connection, job: job_store.Job) -> dict[str, Any]:
    """给 MCP（以及之后的网页）看的任务状态；完成时附整理或写回的摘要。"""
    out: dict[str, Any] = {
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status,
        "phase": PHASES.get(job.phase or "", job.phase),
        "done": job.done,
        "total": job.total,
        "plan_id": job.plan_id,
        "run_id": job.run_id,
    }
    if job.kind == "apply" and job.plan_id:
        run = run_store.load_run(conn, job.run_id) if job.run_id else None
        run = run or run_store.active_run_for_plan(conn, job.plan_id)
        if run is not None:
            out["run_id"] = run.id
            out["playlists"] = _playlist_progress(conn, run.id)
    if job.kind == "undo":
        return _undo_summary(job, out)
    analyzing = job.kind == "analyze"
    if job.status == "running":
        out["message"] = "分析中" if analyzing else "正在建歌单"
    elif job.status == "failed":
        out["error"] = job.error
        out["message"] = f"任务失败：{job.error}"
    elif job.status == "interrupted":
        out["message"] = (
            "分析被中断（启动它的进程已退出）。可以重新调用 create_plan，已读到的曲风有缓存"
            if analyzing
            else "建歌单被中断（启动它的进程已退出）。续传要在终端里确认："
            f"请运行 tunetidy apply {job.plan_id}"
        )
    elif analyzing and job.plan_id:
        out["plan"] = _plan_summary(conn, job.plan_id)
        out["message"] = (
            f"整理 {job.plan_id} 已生成。确认要由你本人在终端运行 tunetidy approve {job.plan_id}；"
            "想先在网页上调整，运行 tunetidy ui"
        )
    elif job.run_id:
        out.update(_run_summary(conn, job.run_id))
        out["message"] = f"歌单已建好。不满意可以在终端运行 tunetidy undo {job.run_id} 撤销"
    return out


def _undo_summary(job: job_store.Job, out: dict[str, Any]) -> dict[str, Any]:
    if job.status == "running":
        out["message"] = "正在撤销"
    elif job.status == "failed":
        out["error"] = job.error
        out["message"] = f"撤销失败：{job.error}"
    elif job.status == "interrupted":
        out["message"] = "撤销被中断（启动它的进程已退出），可以重新预览后再撤销"
    else:
        out.update(json.loads(job.result or "{}"))
        out["message"] = "已撤销"
    return out

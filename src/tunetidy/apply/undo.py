"""撤销一次写回。

- 先回读每个本次新建的歌单，与写入记录对比：歌曲、名称、是否私密、收藏数、简介里的运行标记。
- 完全一致：整个删除歌单。
- 有任何差异（包括找不到运行标记、用户删过其中的歌）：列出差异，只移除本工具加入的歌，保留歌单。
- 只碰写入记录里本次新建的歌单，绝不碰用户原有歌单。
- 撤销会删除歌单，与 approve/apply 一样只能在交互式终端里由用户确认后执行。
- 预览之后、真正删除之前再回读一次；这期间歌单变了就改为只移除本工具加入的歌。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from tunetidy.apply import approval
from tunetidy.apply.approval import Ask, Say, require_interactive
from tunetidy.backends.base import BackendError, MusicBackend
from tunetidy.models import PlaylistSnapshot
from tunetidy.plan import PlanStatus
from tunetidy.storage import plans as plan_store
from tunetidy.storage import runs as run_store
from tunetidy.storage.runs import Run, RunPlaylist

Action = Literal["delete", "remove_ours", "skip"]


class UndoError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlaylistUndo:
    playlist: RunPlaylist
    action: Action
    remove: tuple[str, ...]  # 将移除的歌（本工具加入、且仍在歌单里的）
    differences: tuple[str, ...]
    note: str | None = None


@dataclass(frozen=True)
class UndoPlan:
    run: Run
    items: tuple[PlaylistUndo, ...]


@dataclass
class UndoResult:
    deleted: list[str] = field(default_factory=list)
    trimmed: list[tuple[str, int]] = field(default_factory=list)  # (歌单名, 移除数)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _added_ids(conn: sqlite3.Connection, run: Run, rp: RunPlaylist) -> list[str]:
    return [t.key.id for t in run_store.tracks(conn, run.id, rp.category) if t.status == "added"]


def compare(run: Run, rp: RunPlaylist, snap: PlaylistSnapshot, ours: list[str]) -> tuple[str, ...]:
    """歌单现状与写入记录的差异；空元组表示完全一致。"""
    diffs = []
    if run.playlist_marker(rp.index) not in snap.description:
        diffs.append("简介里找不到运行标记，视为歌单已被修改")
    if snap.name != rp.name:
        diffs.append(f"歌单名被改为「{snap.name}」")
    if rp.private and not snap.private:
        diffs.append("歌单被设为公开")
    if snap.subscribed_count > 0:
        diffs.append(f"已有 {snap.subscribed_count} 人收藏")
    extra = [s for s in snap.track_ids if s not in set(ours)]
    if extra:
        diffs.append(f"你另外加入了 {len(extra)} 首歌（会保留）")
    missing = [s for s in ours if s not in set(snap.track_ids)]
    if missing:
        diffs.append(f"本工具加入的歌里有 {len(missing)} 首已被移除")
    return tuple(diffs)


def _item(
    conn: sqlite3.Connection, backend: MusicBackend, run: Run, rp: RunPlaylist
) -> PlaylistUndo:
    if rp.playlist_id is None or rp.status == "removed":
        note = "已经删除过" if rp.status == "removed" else "没有建成，无需撤销"
        return PlaylistUndo(rp, "skip", (), (), note)
    try:
        snap = backend.get_playlist(rp.playlist_id)
    except BackendError:
        return PlaylistUndo(rp, "skip", (), (), "读取不到这个歌单，可能已被你删除")
    ours = _added_ids(conn, run, rp)
    diffs = compare(run, rp, snap, ours)
    present = set(snap.track_ids)
    remove = tuple(s for s in ours if s in present)
    if not diffs:
        return PlaylistUndo(rp, "delete", remove, ())
    if not remove:
        return PlaylistUndo(rp, "skip", (), diffs, "本工具加入的歌已经都不在了")
    return PlaylistUndo(rp, "remove_ours", remove, diffs)


def plan_undo(backend: MusicBackend, conn: sqlite3.Connection, run_id: str) -> UndoPlan:
    """只读：回读并对比，决定每个歌单怎么撤销。"""
    run = run_store.load_run(conn, run_id)
    if run.status == "undone":
        raise UndoError(f"运行 {run_id} 已经撤销过")
    if run.status == "running":
        raise UndoError(f"运行 {run_id} 正在执行，请等它结束或中断后再撤销")
    return UndoPlan(run, tuple(_item(conn, backend, run, rp) for rp in run.playlists))


def undo_lines(up: UndoPlan) -> list[str]:
    lines = [f"撤销运行 {up.run.id}（方案 {up.run.plan_id}）："]
    for it in up.items:
        name = it.playlist.name
        if it.action == "delete":
            lines.append(f"  删除歌单「{name}」（{len(it.remove)} 首，与写入记录完全一致）")
        elif it.action == "remove_ours":
            lines.append(
                f"  保留歌单「{name}」，只移除本工具加入的 {len(it.remove)} 首；发现差异："
            )
            lines += [f"    - {d}" for d in it.differences]
        else:
            lines.append(f"  跳过「{name}」：{it.note}")
            lines += [f"    - {d}" for d in it.differences]
    lines.append("只处理本次新建的歌单，不会碰你原有的任何歌单。")
    return lines


def execute_undo(
    backend: MusicBackend,
    conn: sqlite3.Connection,
    up: UndoPlan,
    now: Callable[[], str] = _now,
) -> UndoResult:
    run = up.run
    result = UndoResult()
    run_store.set_run_status(conn, run.id, "undoing", now())
    for it in up.items:
        rp = it.playlist
        if it.action == "skip" or rp.playlist_id is None:
            result.skipped.append(rp.name)
            continue
        pid = rp.playlist_id
        action = it.action
        if action == "delete":
            # 预览之后歌单可能又被改了：删除前再核对一次
            snap = backend.get_playlist(pid)
            if compare(run, rp, snap, _added_ids(conn, run, rp)):
                action = "remove_ours"
                result.warnings.append(f"「{rp.name}」在你确认期间被修改，改为只移除本工具加入的歌")
        if action == "delete":
            backend.delete_playlist(pid)
            ours = [
                t.key for t in run_store.tracks(conn, run.id, rp.category) if t.status == "added"
            ]
            run_store.mark_tracks(conn, run.id, rp.category, ours, "removed", None)
            run_store.set_playlist(conn, run.id, rp.category, pid, "removed")
            result.deleted.append(rp.name)
            continue
        present = set(backend.get_playlist(pid).track_ids)
        tracks = run_store.tracks(conn, run.id, rp.category)
        targets = [t.key for t in tracks if t.status == "added" and t.key.id in present]
        size = backend.max_batch_size
        for i in range(0, len(targets), size):
            batch = targets[i : i + size]
            backend.remove_tracks(pid, [k.id for k in batch])
            run_store.mark_tracks(conn, run.id, rp.category, batch, "removed", None)
        result.trimmed.append((rp.name, len(targets)))
    run_store.set_run_status(conn, run.id, "undone", now())
    _mark_plan_undone(conn, run)
    return result


def _mark_plan_undone(conn: sqlite3.Connection, run: Run) -> None:
    plan = plan_store.load_plan(conn, run.plan_id)
    if plan.status in (PlanStatus.APPLIED, PlanStatus.APPLYING):
        plan_store.transition(conn, plan.id, (plan.status,), PlanStatus.UNDONE)


def undo_interactive(
    backend: MusicBackend,
    conn: sqlite3.Connection,
    run_id: str,
    ask: Ask = input,
    say: Say = print,
) -> UndoResult | None:
    """预览 → 确认码 → 执行。没有可撤销的内容时返回 None。"""
    require_interactive("撤销")
    up = plan_undo(backend, conn, run_id)
    for line in undo_lines(up):
        say(line)
    if all(it.action == "skip" for it in up.items):
        say("没有需要撤销的内容。")
        return None
    code = approval.new_code()
    if ask(f"确认撤销请输入 {code}（其他输入取消）：").strip() != code:
        raise UndoError("确认码不符，已取消，没有做任何改动")
    return execute_undo(backend, conn, up)

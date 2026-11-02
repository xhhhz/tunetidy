"""写回：dry-run、执行、续传。

规则：
- 只新建歌单，只往本次新建的歌单里加歌；不修改、不删除任何原有歌单。
- 先记意图：调用任何写接口之前，把运行、将建的歌单和每首歌写进数据库。
- 每个新歌单简介写入运行标记 tunetidy:run-<id>/pNNN；续传按标记找歌单，不按名称。
- 下架或无版权的歌在方案里已是"无法加入"，不会发给接口；"待确认"的歌不写入。
- 新歌会加在歌单顶部且批内倒序（实测），所以把整个列表倒过来按顺序分批写入，
  最终顺序与方案一致；每批写完以回读结果记录每首歌加入与否。

是否交互式由调用方负责（CLI 用 approval.confirm_execution）；这里仍会再做一次执行前检查。
"""

from __future__ import annotations

import random
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tunetidy.apply.approval import check_executable
from tunetidy.backends.base import MusicBackend
from tunetidy.config import ApplyConfig
from tunetidy.models import PlaylistInfo
from tunetidy.plan import Plan, PlanStatus, source_label
from tunetidy.storage import plans as plan_store
from tunetidy.storage import runs as run_store
from tunetidy.storage.runs import Run, RunPlaylist

Progress = Callable[[str, int, int], None]  # (歌单名, 已写入, 总数)
# 续传时发现"同名、空、没有标记"的歌单（建好歌单但标记没写进去就中断了）：
# 交给调用方让用户确认是否沿用；返回选中的歌单，或 None 表示另建新的
ConfirmAdopt = Callable[[str, list[PlaylistInfo]], PlaylistInfo | None]


class ApplyError(RuntimeError):
    pass


@dataclass
class ApplyResult:
    run_id: str
    created: list[tuple[str, str, int]] = field(default_factory=list)  # (歌单名, id, 加入数)
    unaddable: int = 0
    warnings: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def description(source: str, marker: str) -> str:
    return f"由 TuneTidy 按曲风整理自「{source}」，可用 tunetidy undo 撤销。{marker}"


# ------------------------------------------------------------------ dry-run


def dry_run_lines(plan: Plan, cfg: ApplyConfig) -> list[str]:
    """只描述将要做的事，不访问平台、不写数据库，不要求批准。"""
    playlists = plan.playlists()
    lines = [f"[dry-run] 方案 {plan.id}，状态 {plan.status.value}，不会写入任何内容"]
    for i, p in enumerate(playlists, start=1):
        batches = -(-len(p.songs) // cfg.batch_size)
        privacy = "私密" if cfg.private else "公开"
        lines.append(
            f"  新建{privacy}歌单「{p.name}」，分 {batches} 批写入 {len(p.songs)} 首"
            f"（标记 tunetidy:run-<运行id>/p{i:03d}）"
        )
    lines.append(
        f"  不写入：待确认 {len(plan.pending())} 首，无法加入 {len(plan.unavailable())} 首"
        + (f"，未分组 {len(plan.ungrouped())} 首" if plan.ungrouped() else "")
    )
    lines.append("  不会修改或删除任何原有歌单。")
    return lines


# ------------------------------------------------------------------ 执行与续传


class Writer:
    def __init__(
        self,
        backend: MusicBackend,
        conn: sqlite3.Connection,
        cfg: ApplyConfig,
        *,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        now: Callable[[], str] = _now,
        on_progress: Progress | None = None,
        confirm_adopt: ConfirmAdopt | None = None,
    ) -> None:
        self.backend, self.conn, self.cfg = backend, conn, cfg
        self.sleep, self.rng, self.now = sleep, rng or random.Random(), now
        self.on_progress = on_progress
        self.confirm_adopt = confirm_adopt
        self.batch_size = min(cfg.batch_size, backend.max_batch_size)

    def execute(self, plan: Plan) -> ApplyResult:
        """执行一个已批准的方案。上次中断的方案（状态为执行中）走 resume。"""
        check_executable(plan)
        plan_store.transition(self.conn, plan.id, (PlanStatus.APPROVED,), PlanStatus.APPLYING)
        intent = [(p.category, p.name, p.songs) for p in plan.playlists()]
        run = run_store.create_run(
            self.conn,
            plan.id,
            run_store.new_run_id(datetime.now(UTC)),
            self.now(),
            intent,
            private=self.cfg.private,
        )
        return self._run(plan, run, resuming=False)

    def resume(self, plan: Plan) -> ApplyResult:
        if plan.status != PlanStatus.APPLYING:
            raise ApplyError(f"方案 {plan.id} 不在执行中，无需续传")
        if plan.approved_hash != plan.content_hash:
            raise ApplyError(f"方案 {plan.id} 的内容在批准后发生了变化，不能续传")
        run = run_store.active_run_for_plan(self.conn, plan.id)
        if run is None:
            raise ApplyError(f"找不到方案 {plan.id} 未完成的运行记录")
        run_store.set_run_status(self.conn, run.id, "running", self.now())
        return self._run(plan, run, resuming=True)

    # -------------------------------------------------------------- 内部

    def _run(self, plan: Plan, run: Run, resuming: bool) -> ApplyResult:
        result = ApplyResult(run.id)
        source = source_label(plan.sources)
        try:
            for rp in run.playlists:
                pid = rp.playlist_id or self._ensure_playlist(run, rp, source, resuming, result)
                added, rejected = self._fill(run, rp, pid, resuming)
                run_store.set_playlist(self.conn, run.id, rp.category, pid, "filled")
                result.created.append((rp.name, pid, added))
                result.unaddable += rejected
        except BaseException:
            run_store.set_run_status(self.conn, run.id, "interrupted", self.now())
            raise
        run_store.set_run_status(self.conn, run.id, "done", self.now())
        plan_store.transition(self.conn, plan.id, (PlanStatus.APPLYING,), PlanStatus.APPLIED)
        return result

    def _ensure_playlist(
        self, run: Run, rp: RunPlaylist, source: str, resuming: bool, result: ApplyResult
    ) -> str:
        marker = run.playlist_marker(rp.index)
        if resuming:
            found = self.backend.find_playlists_by_marker(marker)
            if len(found) > 1:
                raise ApplyError(f"有 {len(found)} 个歌单带有同一个标记 {marker}，请手动检查")
            if found:  # 建好了、标记也写了，只是没来得及记进数据库
                run_store.set_playlist(self.conn, run.id, rp.category, found[0].id, "created")
                return found[0].id
            adopted = self._adopt_unmarked(run, rp, result)
            if adopted:
                return adopted
        pid = self.backend.create_playlist(
            rp.name, description(source, marker), private=self.cfg.private
        )
        run_store.set_playlist(self.conn, run.id, rp.category, pid, "created")
        return pid

    def _adopt_unmarked(self, run: Run, rp: RunPlaylist, result: ApplyResult) -> str | None:
        """极端情况：歌单建好了但标记没写进简介就中断了。只考虑同名、空、无标记的歌单。"""
        candidates = []
        for info in self.backend.list_playlists():
            if not info.owned or info.name != rp.name or info.track_count != 0:
                continue
            snap = self.backend.get_playlist(info.id)
            if "tunetidy:run-" not in snap.description and not snap.track_ids:
                candidates.append(info)
        if not candidates:
            return None
        chosen = self.confirm_adopt(rp.name, candidates) if self.confirm_adopt else None
        if chosen is None:
            result.warnings.append(
                f"发现 {len(candidates)} 个同名的空歌单「{rp.name}」，可能是上次中断留下的，"
                "没有沿用，已另建新歌单；确认无用后可在 App 里删除"
            )
            return None
        run_store.set_playlist(self.conn, run.id, rp.category, chosen.id, "created")
        result.warnings.append(f"沿用了没有标记的空歌单「{rp.name}」（{chosen.id}）")
        return chosen.id

    def _fill(self, run: Run, rp: RunPlaylist, pid: str, resuming: bool) -> tuple[int, int]:
        tracks = run_store.tracks(self.conn, run.id, rp.category)
        if resuming:
            # 加歌请求成功但没来得及记录就中断：以歌单现状为准补记
            present = set(self.backend.get_playlist(pid).track_ids)
            done = [t.key for t in tracks if t.status == "pending" and t.key.id in present]
            if done:
                run_store.mark_tracks(self.conn, run.id, rp.category, done, "added", None)
                tracks = run_store.tracks(self.conn, run.id, rp.category)
        pending = [t for t in tracks if t.status == "pending"]
        # 倒序写入：新歌加在顶部且批内倒序，最终顺序与方案一致
        todo = [t.key for t in sorted(pending, key=lambda t: t.position, reverse=True)]
        total = len(tracks)
        added = sum(t.status == "added" for t in tracks)
        rejected = sum(t.status == "unaddable" for t in tracks)
        next_batch = max((t.batch or 0 for t in tracks), default=0) + 1
        for start in range(0, len(todo), self.batch_size):
            batch = todo[start : start + self.batch_size]
            self.sleep(self.rng.uniform(self.cfg.batch_pause_min, self.cfg.batch_pause_max))
            res = self.backend.add_tracks(pid, [k.id for k in batch])
            added_ids = set(res.added)
            ok = [k for k in batch if k.id in added_ids]
            bad = [k for k in batch if k.id not in added_ids]
            run_store.mark_tracks(self.conn, run.id, rp.category, ok, "added", next_batch)
            if bad:
                run_store.mark_tracks(self.conn, run.id, rp.category, bad, "unaddable", next_batch)
            added += len(ok)
            rejected += len(bad)
            next_batch += 1
            if self.on_progress:
                self.on_progress(rp.name, added + rejected, total)
        return added, rejected

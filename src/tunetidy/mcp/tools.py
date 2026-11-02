"""MCP 五个工具的逻辑（不依赖 MCP SDK，方便用假后端测试）。

- 只返回摘要，明细分页；参数里没有限速、间隔、并发之类设置，限速只读配置文件。
- 不提供确认工具：确认只能由人在终端完成。apply_plan 只接受已确认且内容未变的整理，
  而且只启动后台任务，立即返回任务 id。
- 出错时返回 {"error": "..."}，不抛出，也不带调用栈和凭据。
"""

from __future__ import annotations

import math
import sqlite3
import tomllib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from tunetidy import jobs
from tunetidy.analyze import resolve_sources, song_genres
from tunetidy.backends.base import MusicBackend, NotLoggedInError
from tunetidy.classify.split import SplitError, SplitSettings, parse_split
from tunetidy.classify.split import only as only_groups
from tunetidy.config import Config, ConfigError
from tunetidy.export import short_reason
from tunetidy.storage import jobs as job_store
from tunetidy.storage import plans as plan_store
from tunetidy.storage import repo
from tunetidy.storage.db import connect

NOT_LOGGED_IN = "还没有登录网易云，或登录已失效：请先在终端运行 tunetidy login"
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
SHOW = {"unsure": "pending", "ungrouped": "ungrouped", "unavailable": "unavailable"}
ERRORS = (
    *jobs.KNOWN_ERRORS,
    job_store.JobBusyError,
    job_store.JobNotFoundError,
    plan_store.PlanNotFoundError,
    ConfigError,
    tomllib.TOMLDecodeError,
)

Result = dict[str, Any]


def _safe(fn: Callable[[], Result]) -> Result:
    try:
        return fn()
    except NotLoggedInError:
        return {"error": NOT_LOGGED_IN}
    except ERRORS as e:
        return {"error": str(e)}


def build_split(
    level: int,
    only: list[str] | None,
    match: Literal["primary", "any"],
    top_n: int | None,
    other: bool,
    groups_toml: str | None,
) -> SplitSettings:
    """把工具参数合成拆分设置；规则同命令行 analyze 的参数。"""
    if groups_toml and only:
        raise SplitError("groups_toml 和 only 只能二选一")
    if groups_toml:
        base = parse_split(tomllib.loads(groups_toml))
    else:
        base = SplitSettings(groups=only_groups(only) if only else ())
    if level == 2 and base.groups:
        raise SplitError("level 只用于按一级或二级拆分，不能和 groups_toml、only 一起用")
    return SplitSettings(
        level=2 if level == 2 else base.level,
        groups=base.groups,
        match=match if base.groups else "primary",
        top_n=top_n if top_n is not None else base.top_n,
        other=other and base.other,
    )


class Tools:
    def __init__(
        self, db_path: Path, config: Config, backend_factory: Callable[[], MusicBackend]
    ) -> None:
        self.db_path, self.config, self.backend_factory = db_path, config, backend_factory

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def _backend(self) -> MusicBackend:
        """只有要访问网易云的工具才构造后端，并先确认已登录。"""
        backend = self.backend_factory()
        if backend.whoami() is None:
            raise NotLoggedInError(NOT_LOGGED_IN)
        return backend

    # -------------------------------------------------------------- 工具

    def list_playlists(self) -> Result:
        def run() -> Result:
            return {
                "playlists": [
                    {"id": p.id, "name": p.name, "track_count": p.track_count, "owned": p.owned}
                    for p in self._backend().list_playlists()
                ]
            }

        return _safe(run)

    def create_plan(
        self,
        playlists: list[str],
        level: int = 1,
        only: list[str] | None = None,
        match: Literal["primary", "any"] = "primary",
        top_n: int | None = None,
        other: bool = True,
        groups_toml: str | None = None,
    ) -> Result:
        def run() -> Result:
            split = build_split(level, only, match, top_n, other, groups_toml)
            backend = self._backend()
            sources = resolve_sources(backend.list_playlists(), playlists)
            job_id, _ = jobs.start_analyze(self.db_path, self.config, backend, sources, split)
            return {
                "job_id": job_id,
                "message": "已开始分析（首次分析 1000 首约 25 分钟），用 get_job_status 查看进度",
            }

        return _safe(run)

    def get_job_status(self, job_id: str) -> Result:
        def run() -> Result:
            with self._conn() as conn:
                return jobs.job_summary(conn, job_store.load_job(conn, job_id))

        return _safe(run)

    def get_plan_items(
        self,
        plan_id: str,
        category: str | None = None,
        show: Literal["unsure", "ungrouped", "unavailable"] | None = None,
        page: int = 1,
        page_size: int = PAGE_SIZE,
    ) -> Result:
        def run() -> Result:
            size = max(1, min(page_size, MAX_PAGE_SIZE))
            with self._conn() as conn:
                plan = plan_store.load_plan(conn, plan_id)
                if category is not None:
                    names = {p.category for p in plan.playlists()}
                    if category not in names:
                        raise ValueError(
                            f"这份整理里没有「{category}」歌单，现有：{'、'.join(names)}"
                        )
                    rows = [
                        a for a in plan.assignments
                        if a.status == "assigned" and a.category == category
                    ]  # fmt: skip
                elif show is not None:
                    rows = [a for a in plan.assignments if a.status == SHOW[show]]
                else:
                    rows = list(plan.assignments)
                total = len(rows)
                chunk = rows[(page - 1) * size : page * size] if page >= 1 else []
                songs = repo.get_songs(conn, [a.key for a in chunk])
                genres = song_genres(plan)
                other = str(plan.settings["other_name"])
                items = []
                for a in chunk:
                    s = songs.get(a.key)
                    items.append(
                        {
                            "id": a.key.id,
                            "name": s.name if s else "",
                            "artists": [x.name for x in s.artists if x.name] if s else [],
                            "album": (s.album_name or "") if s else "",
                            "genres": [g["name"] for g in genres.get(a.key, [])],
                            "why": short_reason(a, other),
                        }
                    )
            return {
                "plan_id": plan_id,
                "items": items,
                "page": page,
                "page_size": size,
                "pages": math.ceil(total / size),
                "total": total,
            }

        return _safe(run)

    def apply_plan(self, plan_id: str) -> Result:
        def run() -> Result:
            backend = self._backend()
            job_id, _ = jobs.start_apply(self.db_path, self.config, backend, plan_id)
            return {"job_id": job_id, "message": "已开始建歌单，用 get_job_status 查看进度"}

        return _safe(run)

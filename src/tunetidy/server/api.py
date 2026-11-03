"""网页用的接口：只读写本地数据库，不访问平台。

读接口用 GET，写接口用 POST（重新分类、移动歌曲、改歌单名等）。批准、执行、撤销不在这里：
v1.0 只能在 CLI 交互式终端里完成。
"""

from __future__ import annotations

import json
import sqlite3
import tomllib
from collections import Counter
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from tunetidy import analyze
from tunetidy.classify.pipeline import summarize
from tunetidy.classify.split import Group, SplitError, SplitSettings, parse_split
from tunetidy.config import Config
from tunetidy.export import STATUS_TEXT, UNSURE_WHY, reason_text, short_reason, tied_categories
from tunetidy.models import SongKey
from tunetidy.plan import Plan, PlanStatus, playlist_order
from tunetidy.storage import plans as plan_store
from tunetidy.storage import repo
from tunetidy.storage.db import connect

router = APIRouter(prefix="/api")

# 只有草稿和已批准的方案能在网页上修改；改已批准的方案会使批准作废
EDITABLE = (PlanStatus.DRAFT, PlanStatus.APPROVED)


def db(request: Request) -> Iterator[sqlite3.Connection]:
    """每个请求一个连接：CLI 可能同时在写同一个库，WAL 模式下读不被阻塞。

    FastAPI 会在线程池的不同线程里打开连接、执行接口和关闭连接，所以不能限定线程。
    """
    conn = connect(request.app.state.db_path, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def config(request: Request) -> Config:
    cfg: Config = request.app.state.config
    return cfg


Conn = Annotated[sqlite3.Connection, Depends(db)]
Cfg = Annotated[Config, Depends(config)]


def plan_state(plan: Plan) -> dict[str, Any]:
    """网页定时读取它判断方案有没有在 CLI 里被批准、执行或改动。"""
    return {
        "status": plan.status.value,
        "content_hash": plan.content_hash,
        "approved": plan.status == PlanStatus.APPROVED and plan.approved_hash == plan.content_hash,
        "editable": plan.status in EDITABLE,
    }


@router.get("/ping")
def ping() -> dict[str, bool]:
    """网页断线后用它探测本地服务是否恢复（同样要求口令）。"""
    return {"ok": True}


@router.get("/plans")
def list_plans(conn: Conn) -> list[dict[str, Any]]:
    """按更新时间倒序；首页按 source_ids 分组，每组显示第一份。"""
    out = []
    for r in plan_store.list_plans(conn, limit=100):
        statuses: Counter[str] = Counter()
        assigned: dict[str, int] = {}
        (manual,) = conn.execute(
            "SELECT COALESCE(SUM(manual), 0) FROM assignments WHERE plan_id = ?", (r["id"],)
        ).fetchone()
        for status, category, n in conn.execute(
            "SELECT status, category, COUNT(*) FROM assignments WHERE plan_id = ?"
            " GROUP BY status, category",
            (r["id"],),
        ):
            statuses[status] += n
            if status == "assigned" and category:
                assigned[category] = n
        sources = json.loads(r["sources_json"])["items"]
        settings = json.loads(r["settings_json"])
        out.append(
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "status": r["status"],
                "sources": [n for _, n in sources],
                "source_ids": [i for i, _ in sources],
                "split": settings.get("split", {}),
                "other_name": settings["other_name"],
                "total": sum(statuses.values()),
                "pending": statuses["pending"],
                "categories": [
                    {"name": c, "count": assigned[c]}
                    for c in playlist_order(assigned, settings["other_name"])
                ],
                "manual": manual,
            }
        )
    return out


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, conn: Conn, cfg: Cfg) -> dict[str, Any]:
    plan = plan_store.load_plan(conn, plan_id)
    return {
        "id": plan.id,
        "created_at": plan.created_at,
        "sources": [{"id": s.id, "name": s.name} for s in plan.sources],
        **plan_state(plan),
        "split": plan.settings.get("split", {}),
        "min_size": plan.settings["classify"]["min_size"],
        "other_name": plan.settings["other_name"],
        "summary": summarize(plan.assignments),
        "manual": sum(a.manual for a in plan.assignments),
        "private": cfg.apply.private,
        "playlists": [
            {
                "category": p.category,
                "name": p.name,
                "default_name": analyze.default_name(plan, p.category),
                "count": len(p.songs),
            }
            for p in plan.playlists()
        ],
    }


@router.get("/plans/{plan_id}/state")
def get_state(plan_id: str, conn: Conn) -> dict[str, Any]:
    """轻量的状态查询，供网页定时刷新。"""
    return plan_state(plan_store.load_plan(conn, plan_id))


@router.get("/plans/{plan_id}/songs")
def get_songs(plan_id: str, conn: Conn) -> dict[str, Any]:
    """整份方案的明细一次返回（上千首约几百 KB），由网页在本地筛选和虚拟滚动。"""
    plan = plan_store.load_plan(conn, plan_id)
    songs = repo.get_songs(conn, [a.key for a in plan.assignments])
    name_of = {k: p.name for p in plan.playlists() for k in p.songs}
    other_name = str(plan.settings["other_name"])
    genres = analyze.song_genres(plan)
    rows = []
    for a in plan.assignments:
        s = songs.get(a.key)
        rows.append(
            {
                "position": a.position,
                "platform": a.key.platform,
                "id": a.key.id,
                "name": s.name if s else "",
                "artists": [x.name for x in s.artists if x.name] if s else [],
                "album": (s.album_name or "") if s else "",
                "status": a.status,
                "status_text": STATUS_TEXT[a.status],
                "category": a.category,
                "suggested": a.reason.get("suggested"),
                "confidence": a.confidence,
                "reason": reason_text(a),
                "why": short_reason(a, other_name),
                "genres": genres.get(a.key, []),
                "tied": tied_categories(a),
                "unsure": a.reason.get("why") in UNSURE_WHY and a.status != "unavailable",
                "manual": a.manual,
                "playlist": name_of.get(a.key),
            }
        )
    return {**plan_state(plan), "songs": rows}


@router.get("/plans/{plan_id}/preview")
def get_preview(plan_id: str, conn: Conn, cfg: Cfg) -> dict[str, Any]:
    """写回预览（只读）。批准与执行要在终端里运行给出的命令。"""
    plan = plan_store.load_plan(conn, plan_id)
    playlists = plan.playlists()
    statuses = Counter(a.status for a in plan.assignments)
    return {
        **plan_state(plan),
        "private": cfg.apply.private,
        "playlists": [
            {"category": p.category, "name": p.name, "count": len(p.songs)} for p in playlists
        ],
        "total": sum(len(p.songs) for p in playlists),
        "skipped": {k: statuses.get(k, 0) for k in ("pending", "unavailable", "ungrouped")},
        "commands": [
            f"tunetidy apply {plan.id} --dry-run",
            f"tunetidy approve {plan.id}",
            f"tunetidy apply {plan.id}",
        ],
    }


@router.get("/plans/{plan_id}/genres")
def get_genres(plan_id: str, conn: Conn) -> dict[str, list[dict[str, Any]]]:
    """方案里出现过的曲风，供"只分出 X"挑选。"""
    return analyze.genre_counts(plan_store.load_plan(conn, plan_id))


# ------------------------------------------------------------------ 写接口（只用 POST）


class GroupIn(BaseModel):
    name: str | None = None  # None："只分出 X"，组名取曲风名
    genres: list[str]


class SplitIn(BaseModel):
    level: Literal[1, 2] = 1
    groups: list[GroupIn] = []
    match: Literal["primary", "any"] = "primary"
    top_n: int | None = None
    other: bool = True

    def settings(self) -> SplitSettings:
        return SplitSettings(
            level=self.level,
            groups=tuple(Group(g.name, tuple(g.genres)) for g in self.groups),
            match=self.match,
            top_n=self.top_n,
            other=self.other,
        )


class SimulateIn(BaseModel):
    split: SplitIn
    min_size: int | None = None


class EditIn(BaseModel):
    expected_hash: str  # 调用方看到的方案内容；不一致说明方案已在别处被改动
    confirm_reset: bool = False  # 已批准的方案：调用方已提示"会使批准作废"并得到同意


class ReclassifyIn(EditIn, SimulateIn):
    pass


class MoveIn(EditIn):
    songs: list[tuple[str, str]] = Field(min_length=1)  # (platform, id)
    category: str | None  # None：不写入（未分组）


class ResetIn(EditIn):
    songs: list[tuple[str, str]] = Field(min_length=1)  # (platform, id)


class RenameIn(EditIn):
    category: str
    name: str | None = Field(default=None, max_length=200)  # 长度规则由 analyze 判断


def _edit_result(r: analyze.ReclassifyResult) -> dict[str, Any]:
    return {
        **plan_state(r.plan),
        "changed": r.changed,
        "approval_reset": r.approval_reset,
        "manual": r.kept_manual,
        "summary": summarize(r.plan.assignments),
    }


class TomlIn(BaseModel):
    text: str = Field(max_length=100_000)


@router.post("/split/parse")
def parse_toml(body: TomlIn) -> dict[str, Any]:
    """导入自定义分组：把 TOML 文本解析成拆分设置（写法同 analyze --groups），不保存。"""
    try:
        raw = tomllib.loads(body.text)
    except tomllib.TOMLDecodeError as e:
        raise SplitError(f"不是合法的 TOML：{e}") from e
    return parse_split(raw).to_dict()


@router.post("/plans/{plan_id}/simulate")
def simulate(plan_id: str, body: SimulateIn, conn: Conn) -> dict[str, Any]:
    """按新的拆分方式预估各分类首数，不保存。"""
    plan = plan_store.load_plan(conn, plan_id)
    out = analyze.simulate(plan, body.split.settings(), min_size=body.min_size)
    return {"summary": summarize(out.assignments), "changed": out.content_hash != plan.content_hash}


@router.post("/plans/{plan_id}/reclassify")
def reclassify(plan_id: str, body: ReclassifyIn, conn: Conn) -> dict[str, Any]:
    r = analyze.reclassify(
        conn,
        plan_id,
        body.split.settings(),
        min_size=body.min_size,
        expected_hash=body.expected_hash,
        confirm_reset=body.confirm_reset,
    )
    return _edit_result(r)


@router.post("/plans/{plan_id}/reset")
def reset(plan_id: str, body: ResetIn, conn: Conn) -> dict[str, Any]:
    """撤回人工调整，按当前拆分方式重新自动分类。"""
    r = analyze.reset_songs(
        conn,
        plan_id,
        [SongKey(p, i) for p, i in body.songs],
        expected_hash=body.expected_hash,
        confirm_reset=body.confirm_reset,
    )
    return _edit_result(r)


@router.post("/plans/{plan_id}/rename")
def rename(plan_id: str, body: RenameIn, conn: Conn) -> dict[str, Any]:
    r = analyze.rename_playlist(
        conn,
        plan_id,
        body.category,
        body.name,
        expected_hash=body.expected_hash,
        confirm_reset=body.confirm_reset,
    )
    return _edit_result(r)


@router.post("/plans/{plan_id}/move")
def move(plan_id: str, body: MoveIn, conn: Conn) -> dict[str, Any]:
    keys = [SongKey(p, i) for p, i in body.songs]
    r = analyze.move_songs(
        conn,
        plan_id,
        keys,
        body.category,
        expected_hash=body.expected_hash,
        confirm_reset=body.confirm_reset,
    )
    return _edit_result(r)


@router.post("/plans/{plan_id}/cleanup")
def cleanup(plan_id: str, conn: Conn) -> dict[str, int]:
    """手动清理：删掉和这份同一来源、还没建歌单的其他整理（只删本机记录，不碰网易云）。"""
    plan = plan_store.load_plan(conn, plan_id)
    return {"deleted": plan_store.delete_drafts(conn, [s.id for s in plan.sources], keep=plan.id)}

"""runs / run_playlists / run_tracks 的读写：写回的"意图"和逐首进度。"""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from tunetidy.models import SongKey
from tunetidy.storage.db import transaction


@dataclass(frozen=True)
class RunPlaylist:
    index: int  # 从 1 开始，写进标记的 /pNNN
    category: str
    name: str
    playlist_id: str | None
    status: str  # planned / created / filled / removed
    private: bool  # 建歌单时是否私密


@dataclass(frozen=True)
class RunTrack:
    category: str
    key: SongKey
    position: int
    status: str  # pending / added / unaddable / removed
    batch: int | None


@dataclass(frozen=True)
class Run:
    id: str
    plan_id: str
    marker: str
    status: str  # running / interrupted / done / undoing / undone
    created_at: str
    playlists: tuple[RunPlaylist, ...]
    account_id: str | None = None  # 建它的账号；加这一列之前的记录为空

    def playlist_marker(self, index: int) -> str:
        return f"{self.marker}/p{index:03d}"


class RunNotFoundError(LookupError):
    pass


def new_run_id(now: datetime) -> str:
    return f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def create_run(
    conn: sqlite3.Connection,
    plan_id: str,
    run_id: str,
    created_at: str,
    playlists: Sequence[tuple[str, str, Sequence[SongKey]]],  # (分类, 歌单名, 方案顺序的歌)
    private: bool = True,
    account_id: str | None = None,
) -> Run:
    """先记意图：一次事务写入运行、每个将建的歌单、每首将写入的歌。"""
    marker = f"tunetidy:run-{run_id}"
    with transaction(conn):
        conn.execute(
            "INSERT INTO runs (id, plan_id, marker, status, created_at, updated_at, account_id)"
            " VALUES (?, ?, ?, 'running', ?, ?, ?)",
            (run_id, plan_id, marker, created_at, created_at, account_id),
        )
        # run_playlists 的插入顺序（rowid）决定歌单序号 /pNNN
        for category, name, keys in playlists:
            conn.execute(
                "INSERT INTO run_playlists (run_id, category, name, playlist_id, status, private)"
                " VALUES (?, ?, ?, NULL, 'planned', ?)",
                (run_id, category, name, int(private)),
            )
            conn.executemany(
                "INSERT INTO run_tracks (run_id, category, platform, song_id, position, status,"
                " batch) VALUES (?, ?, ?, ?, ?, 'pending', NULL)",
                [(run_id, category, k.platform, k.id, pos) for pos, k in enumerate(keys)],
            )
    return load_run(conn, run_id)


def load_run(conn: sqlite3.Connection, run_id: str) -> Run:
    r = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if r is None:
        raise RunNotFoundError(f"找不到运行记录 {run_id}")
    rows = conn.execute(
        "SELECT rowid, * FROM run_playlists WHERE run_id = ? ORDER BY rowid", (run_id,)
    ).fetchall()
    playlists = tuple(
        RunPlaylist(i, p["category"], p["name"], p["playlist_id"], p["status"], bool(p["private"]))
        for i, p in enumerate(rows, start=1)
    )
    return Run(
        r["id"], r["plan_id"], r["marker"], r["status"], r["created_at"], playlists, r["account_id"]
    )


def list_runs(conn: sqlite3.Connection, limit: int = 100) -> list[Run]:
    """所有写回记录，新的在前（网页"历史"页用）。"""
    rows = conn.execute(
        "SELECT id FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
    ).fetchall()
    return [load_run(conn, r["id"]) for r in rows]


def active_run_for_plan(conn: sqlite3.Connection, plan_id: str) -> Run | None:
    r = conn.execute(
        "SELECT id FROM runs WHERE plan_id = ? AND status IN ('running', 'interrupted')"
        " ORDER BY created_at DESC LIMIT 1",
        (plan_id,),
    ).fetchone()
    return load_run(conn, r["id"]) if r else None


def latest_run_for_plan(conn: sqlite3.Connection, plan_id: str) -> Run | None:
    r = conn.execute(
        "SELECT id FROM runs WHERE plan_id = ? ORDER BY created_at DESC LIMIT 1", (plan_id,)
    ).fetchone()
    return load_run(conn, r["id"]) if r else None


def _source_ids(sources_json: str) -> list[str]:
    return sorted(str(i) for i, _ in json.loads(sources_json).get("items", []))


def previous_batch(conn: sqlite3.Connection, plan_id: str, account_id: str) -> Run | None:
    """这份整理建好后，同一来源（来源歌单 id 集合相同，顺序无关）在它之前由同一账号建好、
    还没撤销的那批写回；没有时返回 None。这份自己还没建好时也返回 None。"""
    me = conn.execute("SELECT status, sources_json FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if me is None or me["status"] != "applied":
        return None
    own = conn.execute("SELECT MAX(rowid) FROM runs WHERE plan_id = ?", (plan_id,)).fetchone()[0]
    if own is None:
        return None
    want = _source_ids(me["sources_json"])
    # 只看这份自己那批之前建的（rowid 按建立先后递增；created_at 只精确到秒）
    rows = conn.execute(
        "SELECT runs.id, plans.sources_json FROM runs JOIN plans ON plans.id = runs.plan_id"
        " WHERE runs.status = 'done' AND runs.account_id = ? AND runs.plan_id != ?"
        " AND runs.rowid < ? ORDER BY runs.rowid DESC",
        (account_id, plan_id, own),
    ).fetchall()
    for r in rows:
        if _source_ids(r["sources_json"]) == want:
            return load_run(conn, r["id"])
    return None


def tracks(conn: sqlite3.Connection, run_id: str, category: str) -> list[RunTrack]:
    rows = conn.execute(
        "SELECT * FROM run_tracks WHERE run_id = ? AND category = ? ORDER BY position",
        (run_id, category),
    ).fetchall()
    return [
        RunTrack(t["category"], SongKey(t["platform"], t["song_id"]), t["position"], t["status"],
                 t["batch"])
        for t in rows
    ]  # fmt: skip


def set_run_status(conn: sqlite3.Connection, run_id: str, status: str, now: str) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?", (status, now, run_id)
        )


def set_playlist(
    conn: sqlite3.Connection, run_id: str, category: str, playlist_id: str | None, status: str
) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE run_playlists SET playlist_id = ?, status = ?"
            " WHERE run_id = ? AND category = ?",
            (playlist_id, status, run_id, category),
        )


def mark_tracks(
    conn: sqlite3.Connection,
    run_id: str,
    category: str,
    keys: Sequence[SongKey],
    status: str,
    batch: int | None,
) -> None:
    with transaction(conn):
        conn.executemany(
            "UPDATE run_tracks SET status = ?, batch = COALESCE(?, batch)"
            " WHERE run_id = ? AND category = ? AND platform = ? AND song_id = ?",
            [(status, batch, run_id, category, k.platform, k.id) for k in keys],
        )

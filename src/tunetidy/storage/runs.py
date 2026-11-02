"""runs / run_playlists / run_tracks 的读写：写回的"意图"和逐首进度。"""

from __future__ import annotations

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
) -> Run:
    """先记意图：一次事务写入运行、每个将建的歌单、每首将写入的歌。"""
    marker = f"tunetidy:run-{run_id}"
    with transaction(conn):
        conn.execute(
            "INSERT INTO runs (id, plan_id, marker, status, created_at, updated_at)"
            " VALUES (?, ?, ?, 'running', ?, ?)",
            (run_id, plan_id, marker, created_at, created_at),
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
    return Run(r["id"], r["plan_id"], r["marker"], r["status"], r["created_at"], playlists)


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

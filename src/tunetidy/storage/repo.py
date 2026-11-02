"""songs / song_signals / style_trees 的读写。时间一律存 UTC ISO 字符串。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from tunetidy.models import Artist, CopyrightStatus, GenreTag, Song, SongKey, SongTags, StyleNode
from tunetidy.storage.db import transaction

OFFICIAL = "official"


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ------------------------------------------------------------------ songs


def upsert_songs(conn: sqlite3.Connection, songs: Iterable[Song], now: datetime) -> None:
    rows = [
        (
            s.key.platform,
            s.key.id,
            s.name,
            json.dumps([[a.id, a.name] for a in s.artists], ensure_ascii=False),
            s.album_id,
            s.album_name,
            s.duration_ms,
            s.publish_year,
            s.copyright.value,
            iso(now),
        )
        for s in songs
    ]
    with transaction(conn):
        conn.executemany(
            """
            INSERT INTO songs (platform, id, name, artists_json, album_id, album_name,
                               duration_ms, publish_year, copyright, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (platform, id) DO UPDATE SET
                name = excluded.name, artists_json = excluded.artists_json,
                album_id = excluded.album_id, album_name = excluded.album_name,
                duration_ms = excluded.duration_ms, publish_year = excluded.publish_year,
                copyright = excluded.copyright, updated_at = excluded.updated_at
            """,
            rows,
        )


def get_songs(conn: sqlite3.Connection, keys: Sequence[SongKey]) -> dict[SongKey, Song]:
    out: dict[SongKey, Song] = {}
    for k in keys:
        r = conn.execute(
            "SELECT * FROM songs WHERE platform = ? AND id = ?", (k.platform, k.id)
        ).fetchone()
        if r is None:
            continue
        out[k] = Song(
            key=k,
            name=r["name"],
            artists=tuple(Artist(a[0], a[1]) for a in json.loads(r["artists_json"])),
            album_id=r["album_id"],
            album_name=r["album_name"],
            duration_ms=r["duration_ms"],
            publish_year=r["publish_year"],
            copyright=CopyrightStatus(r["copyright"]),
        )
    return out


# ------------------------------------------------------------------ 官方标签缓存


def tags_to_json(t: SongTags) -> str:
    return json.dumps(
        {
            "genres": [[g.tag_id, g.name] for g in t.genres],
            "biz_tags": list(t.biz_tags),
            "language": t.language,
            "bpm": t.bpm,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def tags_from_json(key: SongKey, text: str) -> SongTags:
    d = json.loads(text)
    return SongTags(
        key,
        tuple(GenreTag(g[0], g[1]) for g in d["genres"]),
        tuple(d["biz_tags"]),
        d["language"],
        d["bpm"],
    )


def save_tags(conn: sqlite3.Connection, tags: SongTags, now: datetime) -> None:
    """单首写入并立即提交：取标签中途中断，已取到的不会丢。"""
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO song_signals (platform, song_id, source, tags_json, raw_json, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (platform, song_id, source) DO UPDATE SET
                tags_json = excluded.tags_json, raw_json = excluded.raw_json,
                fetched_at = excluded.fetched_at
            """,
            (tags.key.platform, tags.key.id, OFFICIAL, tags_to_json(tags), tags.raw_json, iso(now)),
        )
        # 语种、BPM 也写进 songs 的列，方便以后筛选
        conn.execute(
            "UPDATE songs SET language = ?, bpm = ? WHERE platform = ? AND id = ?",
            (tags.language, tags.bpm, tags.key.platform, tags.key.id),
        )


def fresh_tags(
    conn: sqlite3.Connection, keys: Sequence[SongKey], now: datetime, max_age_days: int
) -> dict[SongKey, SongTags]:
    cutoff = iso(now - timedelta(days=max_age_days))
    out: dict[SongKey, SongTags] = {}
    for k in keys:
        r = conn.execute(
            "SELECT tags_json FROM song_signals"
            " WHERE platform = ? AND song_id = ? AND source = ? AND fetched_at >= ?",
            (k.platform, k.id, OFFICIAL, cutoff),
        ).fetchone()
        if r is not None:
            out[k] = tags_from_json(k, r["tags_json"])
    return out


# ------------------------------------------------------------------ 曲风树缓存


def save_style_tree(
    conn: sqlite3.Connection, platform: str, version: str, nodes: Sequence[StyleNode], now: datetime
) -> None:
    payload = json.dumps(
        [[n.tag_id, n.name, n.parent_id, n.level] for n in nodes], ensure_ascii=False
    )
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO style_trees (platform, version, nodes_json, fetched_at) VALUES (?, ?, ?, ?)
            ON CONFLICT (platform) DO UPDATE SET
                version = excluded.version, nodes_json = excluded.nodes_json,
                fetched_at = excluded.fetched_at
            """,
            (platform, version, payload, iso(now)),
        )


def fresh_style_tree(
    conn: sqlite3.Connection, platform: str, now: datetime, max_age_days: int
) -> list[StyleNode] | None:
    r = conn.execute(
        "SELECT nodes_json FROM style_trees WHERE platform = ? AND fetched_at >= ?",
        (platform, iso(now - timedelta(days=max_age_days))),
    ).fetchone()
    if r is None:
        return None
    return [StyleNode(a, b, c, d) for a, b, c, d in json.loads(r["nodes_json"])]

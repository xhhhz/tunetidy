"""把网易云响应解析成平台无关的数据类型。纯函数，用 tests/fixtures/netease/ 测试。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from tunetidy.models import (
    Artist,
    CopyrightStatus,
    GenreTag,
    PlaylistInfo,
    PlaylistSnapshot,
    Song,
    SongKey,
    SongTags,
    StyleNode,
    User,
)

PLATFORM = "netease"
PRIVATE_PRIVACY = 10
WIKI_BASIC_BLOCK = "SONG_PLAY_ABOUT_SONG_BASIC"
_TAG_ID = re.compile(r"[?&]tagId=(\d+)")


def _id(value: Any) -> str | None:
    return str(value) if value not in (None, 0, "0", "") else None


def parse_user(body: dict[str, Any]) -> User | None:
    prof = body.get("profile") or {}
    if not prof.get("userId"):
        return None
    return User(PLATFORM, str(prof["userId"]), str(prof.get("nickname") or ""))


def parse_playlists(body: dict[str, Any]) -> list[PlaylistInfo]:
    out = []
    for p in body.get("playlist") or []:
        out.append(
            PlaylistInfo(
                platform=PLATFORM,
                id=str(p["id"]),
                name=str(p.get("name") or ""),
                # trackCount 不含云盘歌曲，App 显示的数量包含（实测）
                track_count=int(p.get("trackCount") or 0) + int(p.get("cloudTrackCount") or 0),
                owned=not p.get("subscribed"),
            )
        )
    return out


def playlist_descriptions(body: dict[str, Any]) -> dict[str, str]:
    """歌单 id → 简介，用于按运行标记查找。"""
    return {str(p["id"]): str(p.get("description") or "") for p in body.get("playlist") or []}


def parse_playlist_detail(body: dict[str, Any]) -> PlaylistSnapshot:
    pl = body.get("playlist") or {}
    return PlaylistSnapshot(
        platform=PLATFORM,
        id=str(pl["id"]),
        name=str(pl.get("name") or ""),
        description=str(pl.get("description") or ""),
        private=pl.get("privacy") == PRIVATE_PRIVACY,
        subscribed_count=int(pl.get("subscribedCount") or 0),
        track_ids=tuple(str(t["id"]) for t in pl.get("trackIds") or []),
    )


def copyright_status(song: dict[str, Any], privilege: dict[str, Any] | None) -> CopyrightStatus:
    """判定顺序：云盘 → 下架或无版权 → VIP 专享 → 正常。"""
    if song.get("t") == 1 or song.get("pc"):
        return CopyrightStatus.CLOUD
    priv = privilege or {}
    st = priv.get("st", song.get("st")) or 0
    if st < 0:
        return CopyrightStatus.UNAVAILABLE
    if priv.get("fee", song.get("fee")) == 1:
        return CopyrightStatus.VIP
    return CopyrightStatus.NORMAL


def _year(ms: Any) -> int | None:
    if not ms or not isinstance(ms, int | float) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).year


def parse_song(song: dict[str, Any], privilege: dict[str, Any] | None) -> Song:
    pc = song.get("pc") or {}
    artists = tuple(
        Artist(_id(a.get("id")), str(a.get("name") or "")) for a in song.get("ar") or []
    )
    if pc and not any(a.name for a in artists) and pc.get("ar"):
        artists = (Artist(None, str(pc["ar"])),)  # 云盘歌曲的歌手信息在上传记录里
    al = song.get("al") or {}
    album_name = al.get("name") or (pc.get("alb") if pc else None)
    return Song(
        key=SongKey(PLATFORM, str(song["id"])),
        name=str(song.get("name") or pc.get("sn") or ""),
        artists=artists,
        album_id=_id(al.get("id")),
        album_name=str(album_name) if album_name else None,
        duration_ms=int(song["dt"]) if song.get("dt") else None,
        publish_year=_year(song.get("publishTime")),
        copyright=copyright_status(song, privilege),
    )


def parse_song_detail(body: dict[str, Any]) -> dict[str, Song]:
    """歌曲 id → Song。接口不保证返回顺序，调用方按歌单顺序取。"""
    privs = {str(p["id"]): p for p in body.get("privileges") or []}
    return {str(s["id"]): parse_song(s, privs.get(str(s["id"]))) for s in body.get("songs") or []}


def _resources(creative: dict[str, Any]) -> list[GenreTag]:
    out = []
    for r in creative.get("resources") or []:
        title = ((r.get("uiElement") or {}).get("mainTitle")) or {}
        url = (((title.get("action") or {}).get("clickAction")) or {}).get("targetUrl") or ""
        m = _TAG_ID.search(url)
        if title.get("title") and m:
            out.append(GenreTag(m.group(1), str(title["title"])))
    return out


def _first_text(creative: dict[str, Any]) -> str | None:
    links = (creative.get("uiElement") or {}).get("textLinks") or []
    text = links[0].get("text") if links else None
    return str(text) if text else None


def parse_song_wiki(song_id: str, body: dict[str, Any]) -> SongTags:
    blocks = (body.get("data") or {}).get("blocks") or []
    basic = next((b for b in blocks if b.get("code") == WIKI_BASIC_BLOCK), None) or {}
    genres: tuple[GenreTag, ...] = ()
    biz: tuple[str, ...] = ()
    language: str | None = None
    bpm: int | None = None
    for cr in basic.get("creatives") or []:
        kind = cr.get("creativeType")
        if kind == "songTag":
            genres = tuple(_resources(cr))
        elif kind == "songBizTag":
            biz = tuple(
                str(((r.get("uiElement") or {}).get("mainTitle") or {}).get("title"))
                for r in cr.get("resources") or []
                if ((r.get("uiElement") or {}).get("mainTitle") or {}).get("title")
            )
        elif kind == "language":
            language = _first_text(cr)
        elif kind == "bpm":
            text = _first_text(cr)
            bpm = int(text) if text and text.isdigit() else None
    return SongTags(SongKey(PLATFORM, song_id), genres, biz, language, bpm)


def wiki_basic_block(body: dict[str, Any]) -> dict[str, Any] | None:
    """只缓存基础信息板块（约 8 KB），整页约 160 KB。"""
    blocks = (body.get("data") or {}).get("blocks") or []
    block = next((b for b in blocks if b.get("code") == WIKI_BASIC_BLOCK), None)
    return block


def parse_style_tree(body: dict[str, Any]) -> list[StyleNode]:
    out: list[StyleNode] = []

    def walk(nodes: list[dict[str, Any]], parent: str | None, level: int) -> None:
        for n in nodes:
            tid = str(n["tagId"])
            out.append(StyleNode(tid, str(n.get("tagName") or ""), parent, level))
            walk(n.get("childrenTags") or [], tid, level + 1)

    walk(body.get("data") or [], None, 1)
    return out

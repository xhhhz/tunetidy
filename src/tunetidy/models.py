"""平台无关的数据类型。

歌曲一律用 (platform, id) 作主键；id 用字符串，不假设平台用整数。
这里不出现任何平台专有字段，平台差异由各后端在内部消化。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CopyrightStatus(StrEnum):
    NORMAL = "normal"
    VIP = "vip"
    UNAVAILABLE = "unavailable"  # 下架或无版权：写回前筛出，列为"无法加入"
    CLOUD = "cloud"  # 用户上传到云盘的歌：可加入歌单，没有公共曲风数据


@dataclass(frozen=True)
class SongKey:
    platform: str
    id: str


@dataclass(frozen=True)
class Artist:
    id: str | None
    name: str


@dataclass(frozen=True)
class Song:
    key: SongKey
    name: str
    artists: tuple[Artist, ...]
    album_id: str | None
    album_name: str | None
    duration_ms: int | None
    publish_year: int | None
    copyright: CopyrightStatus


@dataclass(frozen=True)
class GenreTag:
    tag_id: str
    name: str


@dataclass(frozen=True)
class SongTags:
    """单曲的官方标签。genres 为空表示平台没有给这首歌打曲风。"""

    key: SongKey
    genres: tuple[GenreTag, ...]
    biz_tags: tuple[str, ...]
    language: str | None  # 平台原值，未做归一
    bpm: int | None
    raw_json: str | None = None  # 后端原始数据，只用于缓存后重新解析，上层不读取


@dataclass(frozen=True)
class StyleNode:
    tag_id: str
    name: str
    parent_id: str | None
    level: int


@dataclass(frozen=True)
class User:
    platform: str
    id: str
    nickname: str


@dataclass(frozen=True)
class PlaylistInfo:
    platform: str
    id: str
    name: str
    track_count: int
    owned: bool  # True：用户自己创建；False：收藏的


@dataclass(frozen=True)
class PlaylistSnapshot:
    """歌单现状，用于续传和撤销前的回读对比。"""

    platform: str
    id: str
    name: str
    description: str
    private: bool
    subscribed_count: int
    track_ids: tuple[str, ...]  # 按歌单当前顺序


@dataclass(frozen=True)
class AddResult:
    """一次加歌请求的结果，以回读为准，不以返回码为准。"""

    added: tuple[str, ...]
    rejected: tuple[str, ...]
    message: str | None = None

"""整理方案：分类结果 + 将要新建的歌单 + 内容哈希。

内容哈希只覆盖"会对账号做什么"：来源、分类体系版本、每个新歌单的名称与歌曲顺序、
待确认、无法加入与未分组的歌。批准时记下哈希，执行前核对；内容一变，批准即作废。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from tunetidy.classify.pipeline import Assignment
from tunetidy.models import SongKey


class PlanStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    APPLYING = "applying"
    APPLIED = "applied"
    UNDONE = "undone"


@dataclass(frozen=True)
class Source:
    id: str
    name: str


@dataclass(frozen=True)
class PlannedPlaylist:
    category: str
    name: str
    songs: tuple[SongKey, ...]  # 方案顺序（来源歌单顺序）


@dataclass(frozen=True)
class Plan:
    id: str
    created_at: str
    status: PlanStatus
    platform: str
    sources: tuple[Source, ...]
    taxonomy_version: str
    settings: dict[str, Any]  # 生成方案时的分类与命名设置
    snapshot: dict[str, Any]  # 信号快照：同一快照必然得到同样的分类结果
    assignments: tuple[Assignment, ...]
    approved_hash: str | None = None
    approved_at: str | None = None
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "content_hash", compute_hash(self))

    def playlists(self) -> list[PlannedPlaylist]:
        return planned_playlists(
            self.assignments,
            self.sources,
            str(self.settings["name_template"]),
            str(self.settings["other_name"]),
            self.settings.get("names"),
        )

    def pending(self) -> list[Assignment]:
        return [a for a in self.assignments if a.status == "pending"]

    def unavailable(self) -> list[Assignment]:
        return [a for a in self.assignments if a.status == "unavailable"]

    def ungrouped(self) -> list[Assignment]:
        """不建"其他"歌单时，本该进"其他"的歌：不写入。"""
        return [a for a in self.assignments if a.status == "ungrouped"]


def source_label(sources: tuple[Source, ...]) -> str:
    if len(sources) == 1:
        return sources[0].name
    return f"{sources[0].name} 等 {len(sources)} 个歌单"


def playlist_order(counts: Mapping[str, int], other_name: str) -> list[str]:
    """歌多的在前，同数按名称；"其他"放最后。"""
    return sorted(counts, key=lambda c: (c == other_name, -counts[c], c))


def planned_playlists(
    assignments: tuple[Assignment, ...],
    sources: tuple[Source, ...],
    template: str,
    other_name: str,
    names: Mapping[str, str] | None = None,
) -> list[PlannedPlaylist]:
    """names：用户改过的歌单名（分类 → 名字）；分类不在时忽略。"""
    names = names or {}
    by_cat: dict[str, list[Assignment]] = {}
    for a in assignments:
        if a.status == "assigned" and a.category:
            by_cat.setdefault(a.category, []).append(a)
    order = playlist_order({c: len(v) for c, v in by_cat.items()}, other_name)
    label = source_label(sources)
    return [
        PlannedPlaylist(
            category=c,
            name=names.get(c) or template.format(category=c, source=label),
            songs=tuple(a.key for a in sorted(by_cat[c], key=lambda a: a.position)),
        )
        for c in order
    ]


def compute_hash(plan: Plan) -> str:
    def ids(items: list[Assignment]) -> list[list[str]]:
        return [[a.key.platform, a.key.id] for a in sorted(items, key=lambda a: a.position)]

    content = {
        "platform": plan.platform,
        "sources": [s.id for s in plan.sources],
        "taxonomy": plan.taxonomy_version,
        "playlists": [
            {"category": p.category, "name": p.name, "songs": [[k.platform, k.id] for k in p.songs]}
            for p in plan.playlists()
        ],
        "pending": ids(plan.pending()),
        "unavailable": ids(plan.unavailable()),
    }
    # 只在非空时加入：否则升级后所有旧方案的哈希都会变，已批准的方案会无故失效
    if plan.ungrouped():
        content["ungrouped"] = ids(plan.ungrouped())
    text = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()

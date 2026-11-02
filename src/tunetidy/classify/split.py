"""拆分方式：按一级 / 按二级 / 自定义分组，以及匹配方式、前 N 类、"其他"开关。

自定义分组用 TOML 写（标准库可读）：

    match = "primary"          # primary | any
    other = true               # "其他"是否建歌单
    top_n = 5                  # 可选：只保留可写入歌曲最多的 N 类

    [[groups]]
    name = "流行"
    genres = ["流行"]          # 一级名，或"一级-二级"全路径

这里只负责描述与校验；按设置分类在 classify/pipeline.py。
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from tunetidy.classify.taxonomy import Taxonomy, TaxonomyLookupError

Match = Literal["primary", "any"]
Level = Literal[1, 2]
MATCHES: tuple[Match, ...] = ("primary", "any")


class SplitError(ValueError):
    pass


@dataclass(frozen=True)
class Group:
    name: str | None  # None：用组里第一个曲风的名称（"只分出 X"时）
    genres: tuple[str, ...]  # 用户写的曲风名或全路径


@dataclass(frozen=True)
class SplitSettings:
    level: Level = 1
    groups: tuple[Group, ...] = ()  # 为空：按 level 自动分类；非空时 level 不起作用
    match: Match = "primary"
    top_n: int | None = None
    other: bool = True

    def __post_init__(self) -> None:
        if self.level not in (1, 2):
            raise SplitError("level 只能是 1 或 2")
        if self.match not in MATCHES:
            raise SplitError(f"match 只能是 {' 或 '.join(MATCHES)}")
        if self.top_n is not None and (isinstance(self.top_n, bool) or self.top_n < 1):
            raise SplitError("top_n 至少为 1")
        for g in self.groups:
            if not g.genres:
                raise SplitError(f"分组「{g.name or '（未命名）'}」至少要列一个曲风")

    def describe(self) -> str:
        if not self.groups:
            base = "按一级曲风" if self.level == 1 else "按二级曲风"
        elif len(self.groups) == 1 and self.groups[0].name is None:
            base = f"只分出 {'、'.join(self.groups[0].genres)}"
        else:
            base = f"自定义分组（{len(self.groups)} 组）"
        extra = []
        if self.groups:
            extra.append("纯风格" if self.match == "primary" else "含该标签")
        if self.top_n:
            extra.append(f"前 {self.top_n} 类")
        if not self.other:
            extra.append('不建"其他"')
        return base + (f"（{'，'.join(extra)}）" if extra else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "groups": [{"name": g.name, "genres": list(g.genres)} for g in self.groups],
            "match": self.match,
            "top_n": self.top_n,
            "other": self.other,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SplitSettings:
        return cls(
            level=d.get("level", 1),
            groups=tuple(Group(g["name"], tuple(g["genres"])) for g in d.get("groups", [])),
            match=d.get("match", "primary"),
            top_n=d.get("top_n"),
            other=d.get("other", True),
        )


def only(genres: Sequence[str]) -> tuple[Group, ...]:
    """ "只分出 X"：每个曲风各成一组，组名取曲风名，其余归"其他"。"""
    return tuple(Group(None, (g,)) for g in genres)


@dataclass(frozen=True)
class ResolvedGroup:
    name: str
    tag_ids: tuple[str, ...]  # 组里列出的曲风节点；歌的标签等于或在其下级即命中


def resolve_groups(
    settings: SplitSettings, taxonomy: Taxonomy, other_name: str
) -> tuple[ResolvedGroup, ...]:
    """把组里写的曲风名解析成曲风树节点，并检查重名、写错的曲风、同一曲风写进两个组。"""
    out: list[ResolvedGroup] = []
    seen_names: set[str] = set()
    owner: dict[str, str] = {}
    for g in settings.groups:
        ids = []
        for spec in g.genres:
            try:
                node = taxonomy.find(spec)
            except TaxonomyLookupError as e:
                raise SplitError(str(e)) from e
            ids.append(node.tag_id)
        name = g.name if g.name is not None else taxonomy.nodes[ids[0]].name
        if not name.strip():
            raise SplitError("分组名不能为空")
        if name == other_name:
            raise SplitError(f"分组名不能叫「{other_name}」，它留给没命中任何分组的歌")
        if name in seen_names:
            raise SplitError(f"有两个分组都叫「{name}」")
        seen_names.add(name)
        for tid in ids:
            if tid in owner:
                path = taxonomy.path(tid)
                raise SplitError(f"曲风「{path}」同时写在了「{owner[tid]}」和「{name}」两个组里")
            owner[tid] = name
        out.append(ResolvedGroup(name, tuple(ids)))
    return tuple(out)


_TOP_KEYS = {"level", "match", "top_n", "other", "groups"}
_GROUP_KEYS = {"name", "genres"}


def parse_split(raw: dict[str, Any]) -> SplitSettings:
    if "allow_duplicates" in raw:
        raise SplitError("allow_duplicates（一首歌进多个歌单）暂不支持")
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise SplitError(f"未知配置项：{sorted(unknown)}")
    groups = []
    for i, g in enumerate(raw.get("groups", []), start=1):
        if not isinstance(g, dict):
            raise SplitError(f"第 {i} 个分组格式不对，应为 [[groups]] 表")
        bad = set(g) - _GROUP_KEYS
        if bad:
            raise SplitError(f"第 {i} 个分组有未知配置项：{sorted(bad)}")
        name, genres = g.get("name"), g.get("genres")
        if not isinstance(name, str):
            raise SplitError(f"第 {i} 个分组缺少 name")
        if not isinstance(genres, list) or not all(isinstance(x, str) for x in genres):
            raise SplitError(f"分组「{name}」的 genres 应为字符串列表")
        groups.append(Group(name, tuple(genres)))
    if "groups" in raw and not groups:
        raise SplitError("groups 不能为空；不分组请删掉 groups")
    other = raw.get("other", True)
    if not isinstance(other, bool):
        raise SplitError("other 应为 true 或 false")
    return SplitSettings(
        level=raw.get("level", 1),
        groups=tuple(groups),
        match=raw.get("match", "primary"),
        top_n=raw.get("top_n"),
        other=other,
    )


def load_split(path: Path) -> SplitSettings:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SplitError(f"找不到分组文件 {path}") from e
    except tomllib.TOMLDecodeError as e:
        raise SplitError(f"{path} 不是合法的 TOML：{e}") from e
    return parse_split(raw)

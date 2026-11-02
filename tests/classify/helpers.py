"""分类测试的公共构造函数。不依赖 backends/web（它必须能被整体删除）。"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from tunetidy.classify.taxonomy import Taxonomy
from tunetidy.models import CopyrightStatus, GenreTag, Song, SongKey, SongTags, StyleNode

TREE = Path(__file__).resolve().parents[2] / "taxonomies" / "netease_style_tree.json"


@cache
def netease_taxonomy() -> Taxonomy:
    flat = json.loads(TREE.read_text(encoding="utf-8"))["flat"]
    return Taxonomy(
        StyleNode(k, v["name"], str(v["parent"]) if v["parent"] is not None else None, v["level"])
        for k, v in flat.items()
    )


@cache
def tag_id(name: str) -> str:
    """按"一级-二级-三级"全路径名找 tag id，如 tag_id("流行-华语流行")。"""
    tax = netease_taxonomy()
    parts = name.split("-")
    candidates = [n for n in tax.nodes.values() if n.name == parts[-1] and n.level == len(parts)]
    for n in candidates:
        chain = [n.name]
        cur = n
        while cur.parent_id is not None:
            cur = tax.nodes[cur.parent_id]
            chain.append(cur.name)
        if list(reversed(chain)) == parts:
            return n.tag_id
    raise KeyError(name)


def song(
    i: int, copyright: CopyrightStatus = CopyrightStatus.NORMAL, platform: str = "netease"
) -> Song:
    return Song(SongKey(platform, str(i)), f"s{i}", (), None, None, None, None, copyright)


def tags(i: int, *genres: str, platform: str = "netease") -> SongTags:
    return SongTags(
        SongKey(platform, str(i)), tuple(GenreTag(tag_id(g), g) for g in genres), (), None, None
    )

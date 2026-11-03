"""分类流程：官方标签 → 候选曲风 → 按拆分方式映射到分类 → 打分 → 合并小类、前 N 类。

拆分方式：
- 按一级 / 按二级：候选标签按"等于或下级"对应到一级或二级分类；二级粒度下只有一级标签的歌
  归入"一级 · 其他"。
- 自定义分组 primary：候选标签映射到第一个命中的组（标签等于组里的曲风或在其下级），
  没命中的映射到"其他"；所有候选落在同一组即确定，否则按平分处理。
- 自定义分组 any：不做多标签规则，按分组书写顺序取第一个被任一标签命中的组，都没命中归"其他"。

每首歌只依赖自己的信号；合并只依赖各分类的计数。所以结果与处理顺序无关。
目前只有官方标签一个信号。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from tunetidy.classify.scoring import WeightedSignal, decide
from tunetidy.classify.split import ResolvedGroup, SplitSettings, resolve_groups
from tunetidy.classify.taxonomy import Taxonomy
from tunetidy.config import ClassifyConfig
from tunetidy.models import CopyrightStatus, Song, SongKey, SongTags
from tunetidy.signals.official import SOURCE, OfficialEvidence, official_evidence

Status = Literal["assigned", "pending", "unavailable", "ungrouped"]
BUCKET = "{l1} · 其他"  # 二级粒度：只有一级标签的歌，以及被合并的小二级类


@dataclass(frozen=True)
class Assignment:
    key: SongKey
    position: int  # 在来源歌单（合并去重后）里的顺序
    status: Status
    category: str | None  # 合并之后的分类；"待确认"为 None
    confidence: float
    reason: dict[str, Any]  # 可 JSON 序列化的判定依据
    manual: bool = False  # 人工调整过（网页上的"移到"）；重新分类时保留，不被覆盖


class _Mapper:
    """把候选标签映射到分类名，并记下每个分类所属的一级曲风（二级向上合并用）。"""

    def __init__(self, tax: Taxonomy, split: SplitSettings, groups: tuple[ResolvedGroup, ...],
                 other: str) -> None:  # fmt: skip
        self.tax, self.split, self.groups, self.other = tax, split, groups, other

    def _hits(self, tag_id: str, g: ResolvedGroup) -> bool:
        return any(self.tax.is_at_or_below(tag_id, gid) for gid in g.tag_ids)

    def category(self, tag_id: str) -> tuple[str, str | None]:
        """(分类名, 所属一级曲风名)。自定义分组时一级为 None（不做向上合并）。"""
        chain = self.tax.chain(tag_id)
        if self.groups:
            for g in self.groups:
                if self._hits(tag_id, g):
                    return g.name, None
            return self.other, None
        l1 = chain[0].name
        if self.split.level == 1:
            return l1, l1
        return (chain[1].name if len(chain) >= 2 else BUCKET.format(l1=l1)), l1

    def first_group_hit(self, tag_ids: Sequence[str]) -> str:
        """any 模式：按分组书写顺序，取第一个被任一标签命中的组。"""
        for g in self.groups:
            if any(self._hits(t, g) for t in tag_ids):
                return g.name
        return self.other


def _why(song: Song, ev: OfficialEvidence, n_categories: int, confident: bool) -> str:
    if song.copyright == CopyrightStatus.UNAVAILABLE:
        return "unavailable"
    if not ev.all_tags:
        return "no_tags"
    if n_categories >= 2:
        return "multi_genre"
    return "assigned" if confident else "low_confidence"


class FirstPass:
    """第一遍：每首歌只看自己的标签，决定主风格或"待确认"。逐首调用，结果与处理顺序无关。"""

    def __init__(self, taxonomy: Taxonomy, cfg: ClassifyConfig, split: SplitSettings) -> None:
        self.taxonomy, self.cfg, self.split = taxonomy, cfg, split
        groups = resolve_groups(split, taxonomy, cfg.other_name)
        self.mapper = _Mapper(taxonomy, split, groups, cfg.other_name)
        self.use_any = bool(groups) and split.match == "any"

    def classify(self, song: Song, tags: SongTags | None, position: int) -> Assignment:
        cfg, taxonomy, mapper, use_any = self.cfg, self.taxonomy, self.mapper, self.use_any
        ev = official_evidence(tags, taxonomy, cfg)
        parent: dict[str, str | None] = {}
        if use_any:
            cats = [mapper.first_group_hit(ev.all_tags)] if ev.all_tags else []
        else:
            cats = []
            for t in ev.candidates:
                c, l1 = mapper.category(t)
                parent.setdefault(c, l1)
                cats.append(c)
        distinct = sorted(set(cats))
        dist = {c: 1 / len(distinct) for c in distinct}
        d = decide(
            [WeightedSignal(SOURCE, cfg.official_weight, dist)],
            cfg.threshold,
            cfg.tie_margin,
            cfg.evidence_w0,
        )
        status: Status
        if song.copyright == CopyrightStatus.UNAVAILABLE:
            status = "unavailable"
        else:
            status = "assigned" if d.confident else "pending"
        reason = {
            "why": _why(song, ev, len(distinct), d.confident),
            "copyright": song.copyright.value,
            "official": {
                "raw": list(ev.raw_genres),
                "level1": list(ev.level1),
                "kept": list(ev.rules.kept),
                "dropped_usage": list(ev.rules.dropped_usage),
                "dropped_generic": list(ev.rules.dropped_generic),
                "unknown_tag_ids": list(ev.unknown_tag_ids),
            },
            "candidates": [taxonomy.path(t) for t in (ev.all_tags if use_any else ev.candidates)],
            "split": self.split.describe(),
            "scores": d.scores,
            "suggested": d.category,
            "category_level1": parent.get(d.category) if d.category else None,
            "merged_from": None,
        }
        category = d.category if d.confident else None
        return Assignment(song.key, position, status, category, d.confidence, reason)


def finish_classification(
    first_pass: Sequence[Assignment], cfg: ClassifyConfig, split: SplitSettings
) -> list[Assignment]:
    """收尾：按各分类的首数合并小类、只留前 N 类、不建"其他"，再按位置排序。"""
    merged = list(first_pass)
    if not split.groups and split.level == 2:
        merged = merge_level2_upward(merged, cfg.min_size)
    merged = merge_small_categories(merged, cfg.min_size, cfg.other_name)
    if split.top_n:
        merged = keep_top_n(merged, split.top_n, cfg.other_name)
    if not split.other:
        merged = ungroup_other(merged, cfg.other_name)
    return sorted(merged, key=lambda a: a.position)


def classify_songs(
    songs: Sequence[Song],
    tags: Mapping[SongKey, SongTags],
    taxonomy: Taxonomy,
    cfg: ClassifyConfig,
    positions: Mapping[SongKey, int] | None = None,
    split: SplitSettings | None = None,
) -> list[Assignment]:
    split = split or SplitSettings()
    keys = [s.key for s in songs]
    if len(set(keys)) != len(keys):
        raise ValueError("songs 里有重复的歌曲，请先去重")
    pos = positions or {k: i for i, k in enumerate(keys)}
    fp = FirstPass(taxonomy, cfg, split)
    first = [fp.classify(song, tags.get(song.key), pos[song.key]) for song in songs]
    return finish_classification(first, cfg, split)


def _writable_counts(assignments: Sequence[Assignment]) -> Counter[str]:
    return Counter(a.category for a in assignments if a.status == "assigned" and a.category)


def _move(a: Assignment, to: str) -> Assignment:
    origin = a.reason.get("merged_from") or a.category
    return replace(a, category=to, reason={**a.reason, "merged_from": origin})


def merge_level2_upward(assignments: Sequence[Assignment], min_size: int) -> list[Assignment]:
    """二级粒度：不足 min_size 的二级类先并入"一级 · 其他"。之后由 merge_small_categories
    把仍不足的再并入"其他"。"""
    counts = _writable_counts(assignments)
    out = []
    for a in assignments:
        l1 = a.reason.get("category_level1")
        bucket = BUCKET.format(l1=l1) if l1 else None
        if a.category and bucket and a.category != bucket and counts[a.category] < min_size:
            a = _move(a, bucket)
        out.append(a)
    return out


def merge_small_categories(
    assignments: Sequence[Assignment], min_size: int, other_name: str
) -> list[Assignment]:
    """实际会写入的歌（status=assigned）不足 min_size 的分类并入"其他"。

    等于 min_size 时保留；"其他"只要非空就保留，不再继续合并。下架歌曲不计数，
    但它们的分类按同样的映射改写，导出时与可写入的歌保持一致。
    """
    counts = _writable_counts(assignments)
    small = {c for c, n in counts.items() if n < min_size and c != other_name}
    out = [_move(a, other_name) if a.category in small else a for a in assignments]
    return sorted(out, key=lambda a: a.position)


def keep_top_n(assignments: Sequence[Assignment], n: int, other_name: str) -> list[Assignment]:
    """只保留可写入歌曲最多的 n 个分类（不含"其他"），其余并入"其他"。同数按名称排序。"""
    counts = _writable_counts(assignments)
    ranked = sorted((c for c in counts if c != other_name), key=lambda c: (-counts[c], c))
    keep = set(ranked[:n])
    return [
        _move(a, other_name) if a.category and a.category != other_name and a.category not in keep
        else a
        for a in assignments
    ]  # fmt: skip


def ungroup_other(assignments: Sequence[Assignment], other_name: str) -> list[Assignment]:
    """不建"其他"歌单时，本该写进"其他"的歌改为未分组：不写入，只在导出里列出。"""
    return [
        replace(a, status="ungrouped", reason={**a.reason, "why": "ungrouped"})
        if a.status == "assigned" and a.category == other_name
        else a
        for a in assignments
    ]


def summarize(assignments: Sequence[Assignment]) -> dict[str, Any]:
    """各分类数量与"待确认"数，供 CLI 与 MCP 摘要使用。"""
    by_cat = Counter(a.category for a in assignments if a.status == "assigned")
    return {
        "total": len(assignments),
        "assigned": sum(by_cat.values()),
        "pending": sum(a.status == "pending" for a in assignments),
        "unavailable": sum(a.status == "unavailable" for a in assignments),
        "ungrouped": sum(a.status == "ungrouped" for a in assignments),
        "categories": dict(sorted(by_cat.items(), key=lambda kv: (-kv[1], kv[0]))),
    }

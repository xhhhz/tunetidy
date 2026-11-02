"""信号 1：网易云官方曲风标签 → 候选曲风（tag id）。

- 层级对齐一律按 tagId 在曲风树里找祖先；树里没有的 id 退回按名称前缀找一级曲风。
- 多标签规则在一级上做（先去用途类，再去泛化），得到 primary 用的候选；any 用全部标签。
- 同一路径上有多个标签时（如"流行"和"流行-华语流行"），只保留最具体的那个。
映射到分类（一级 / 二级 / 自定义分组）在 classify/pipeline.py。
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass

from tunetidy.classify.rules import RuleResult, apply_multilabel_rules
from tunetidy.classify.taxonomy import Taxonomy, TaxonomyLookupError
from tunetidy.config import ClassifyConfig
from tunetidy.models import SongTags

SOURCE = "official"


@dataclass(frozen=True)
class OfficialEvidence:
    raw_genres: tuple[str, ...]  # 平台原始曲风名
    level1: tuple[str, ...]  # 层级对齐、去重后的一级曲风
    rules: RuleResult
    candidates: tuple[str, ...]  # 多标签规则之后的候选 tag id（primary 与按一级/二级用）
    all_tags: tuple[str, ...]  # 不做多标签规则的全部 tag id（any 用）
    unknown_tag_ids: tuple[str, ...]  # 不在曲风树里、按名称前缀归到一级的 tag id


def most_specific(tax: Taxonomy, ids: Sequence[str]) -> tuple[str, ...]:
    """去掉被同一路径上更具体的标签覆盖的上级标签；保持原顺序、去重。"""
    uniq = list(dict.fromkeys(ids))
    return tuple(t for t in uniq if not any(o != t and tax.is_at_or_below(o, t) for o in uniq))


def official_evidence(
    tags: SongTags | None, tax: Taxonomy, cfg: ClassifyConfig
) -> OfficialEvidence:
    genres = tags.genres if tags else ()
    ids: list[str] = []
    unknown: list[str] = []
    for g in genres:
        if g.tag_id in tax.nodes:
            ids.append(g.tag_id)
            continue
        unknown.append(g.tag_id)
        # 平台新增曲风、本地树过期：按名称前缀归到一级；一级也找不到就忽略这个标签
        with contextlib.suppress(TaxonomyLookupError):
            ids.append(tax.find(g.name.split("-", 1)[0]).tag_id)
    l1_of = {t: tax.chain(t)[0].name for t in ids}
    rules = apply_multilabel_rules(set(l1_of.values()), cfg.usage_genres, cfg.generic_genres)
    kept = set(rules.kept)
    return OfficialEvidence(
        raw_genres=tuple(g.name for g in genres),
        level1=tuple(sorted(set(l1_of.values()))),
        rules=rules,
        candidates=most_specific(tax, [t for t in ids if l1_of[t] in kept]),
        all_tags=most_specific(tax, ids),
        unknown_tag_ids=tuple(sorted(unknown)),
    )

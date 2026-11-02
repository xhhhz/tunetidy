"""官方标签内部的多标签规则。

输入是一首歌官方曲风的一级祖先集合，按固定顺序做两步：
1. 去用途类：还有非用途类曲风时，去掉"原声带""二次元""儿童"等用途类；
2. 去泛化：还有"流行"以外的曲风时，去掉"流行"。
顺序不能换："原声带 + 流行"先去用途类得到"流行"，反过来会得到"原声带"。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass


@dataclass(frozen=True)
class RuleResult:
    kept: tuple[str, ...]
    dropped_usage: tuple[str, ...]
    dropped_generic: tuple[str, ...]


def apply_multilabel_rules(
    genres: Collection[str], usage: Collection[str], generic: Collection[str]
) -> RuleResult:
    current = set(genres)
    dropped_usage: set[str] = set()
    dropped_generic: set[str] = set()
    if current - set(usage):
        dropped_usage = current & set(usage)
        current -= dropped_usage
    if current - set(generic):
        dropped_generic = current & set(generic)
        current -= dropped_generic
    return RuleResult(
        kept=tuple(sorted(current)),
        dropped_usage=tuple(sorted(dropped_usage)),
        dropped_generic=tuple(sorted(dropped_generic)),
    )

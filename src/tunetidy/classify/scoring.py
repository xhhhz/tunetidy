"""加权合并与置信度。

score(c) = Σ_{s∈A} w_s · p_s(c) / Σ_{s∈A} w_s
conf     = score(c*) · min(1, Σ_{s∈A} w_s / W₀)

只有给出了分布的信号才算可用（集合 A）。置信度四舍五入到 3 位小数后与阈值比较，
大于等于阈值且第一、第二名得分差不小于 tie_margin 才确定，否则"待确认"。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

ROUND = 3


@dataclass(frozen=True)
class WeightedSignal:
    source: str
    weight: float
    distribution: dict[str, float]  # 空表示该信号对这首歌不可用


@dataclass(frozen=True)
class Decision:
    category: str | None  # 得分最高的风格；没有任何可用信号时为 None
    confident: bool
    confidence: float
    scores: dict[str, float]


def decide(
    signals: Sequence[WeightedSignal], threshold: float, tie_margin: float, w0: float
) -> Decision:
    available = [s for s in signals if s.distribution]
    total = sum(s.weight for s in available)
    if not available or total <= 0:
        return Decision(None, False, 0.0, {})
    scores: dict[str, float] = {}
    for s in available:
        for c, p in s.distribution.items():
            scores[c] = scores.get(c, 0.0) + s.weight * p
    scores = {c: round(v / total, 6) for c, v in scores.items()}
    # 同分时按名称排序，保证结果确定；同分本身会因 tie_margin 进"待确认"
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    best, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    conf = round(top * min(1.0, total / w0), ROUND)
    confident = conf >= round(threshold, ROUND) and round(top - second, ROUND) >= round(
        tie_margin, ROUND
    )
    return Decision(best, confident, conf, scores)

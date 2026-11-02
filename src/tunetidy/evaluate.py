"""评估数字（写进 README）：官方曲风覆盖率、"没分好"比例、人工改动比例，
按"有曲风标签 / 没有曲风标签"两组分别统计。只读整理内容，输出不含任何歌名。"""

from __future__ import annotations

from typing import Any

from tunetidy.classify.pipeline import Assignment
from tunetidy.plan import Plan


def _has_tags(a: Assignment) -> bool:
    return bool((a.reason.get("official") or {}).get("raw"))


def _group(items: list[Assignment]) -> dict[str, Any]:
    n = len(items)
    return {
        "songs": n,
        "pending": sum(a.status == "pending" for a in items) / n if n else 0.0,
        "manual": sum(a.manual for a in items) / n if n else 0.0,
    }


def evaluate(plan: Plan) -> dict[str, Any]:
    items = list(plan.assignments)
    tagged = [a for a in items if _has_tags(a)]
    untagged = [a for a in items if not _has_tags(a)]
    whole = _group(items)
    return {
        "plan_id": plan.id,
        "total": len(items),
        "coverage": len(tagged) / len(items) if items else 0.0,
        "pending": whole["pending"],
        "manual": whole["manual"],
        "unavailable": sum(a.status == "unavailable" for a in items),
        "tagged": _group(tagged),
        "untagged": _group(untagged),
    }


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def to_markdown(r: dict[str, Any]) -> str:
    rows = [
        ("全部", r["total"], r["pending"], r["manual"]),
        ("有曲风标签", r["tagged"]["songs"], r["tagged"]["pending"], r["tagged"]["manual"]),
        ("没有曲风标签", r["untagged"]["songs"], r["untagged"]["pending"], r["untagged"]["manual"]),
    ]
    lines = [
        f"官方曲风覆盖率：{_pct(r['coverage'])}"
        f"（{r['total']} 首，其中 {r['unavailable']} 首已下架或没版权）",
        "",
        "| 分组 | 歌曲数 | 没分好 | 人工改动 |",
        "| --- | --- | --- | --- |",
        *(f"| {name} | {n} | {_pct(p)} | {_pct(m)} |" for name, n, p, m in rows),
    ]
    return "\n".join(lines) + "\n"

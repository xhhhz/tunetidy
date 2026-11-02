"""导出整理方案：csv / json / md。"待确认"和"无法加入"的歌在导出里单独列出。"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from typing import Any, Literal

from tunetidy.classify.pipeline import Assignment
from tunetidy.models import Song, SongKey
from tunetidy.plan import Plan

Format = Literal["csv", "json", "md"]
STATUS_TEXT = {
    "assigned": "确定",
    "pending": "待确认",
    "unavailable": "无法加入",
    "ungrouped": "未分组",
}


def reason_text(a: Assignment) -> str:
    r = a.reason
    off = r.get("official") or {}
    parts = ["人工调整"] if a.manual else []
    if off.get("raw"):
        parts.append("官方曲风：" + "、".join(off["raw"]))
    if off.get("dropped_usage"):
        parts.append("去掉用途类：" + "、".join(off["dropped_usage"]))
    if off.get("dropped_generic"):
        parts.append("去掉泛化：" + "、".join(off["dropped_generic"]))
    why = r.get("why")
    if why == "no_tags":
        parts.append("没有曲风标签")
    elif why == "multi_genre":
        tied = sorted(r.get("scores") or {}) or (off.get("kept") or [])
        parts.append("多个曲风并列：" + "、".join(tied))
    elif why == "low_confidence":
        parts.append(f"置信度不足（{a.confidence}）")
    elif why == "unavailable":
        parts.append("下架或无版权，不写入")
    elif why == "ungrouped":
        parts.append('没有命中任何分组，且设置为不建"其他"歌单，不写入')
    if r.get("merged_from"):
        parts.append(f"由「{r['merged_from']}」并入（不足最小数量或不在前 N 类）")
    if off.get("unknown_tag_ids"):
        parts.append("部分曲风不在本地曲风树里，按名称归类")
    return "；".join(parts)


# 工具拿不准的几种原因：网页"没分好的歌"页列出这些歌，用户改过之后也留在列表里
UNSURE_WHY = frozenset({"no_tags", "multi_genre", "low_confidence"})


def tied_categories(a: Assignment) -> list[str]:
    """得分并列最高的分类（按名称排序）；没有得分时为空。"""
    scores: dict[str, float] = a.reason.get("scores") or {}
    if not scores:
        return []
    top = max(scores.values())
    return sorted(c for c, v in scores.items() if v == top)


def _cjk_join(left: str, word: str, right: str) -> str:
    """中英文之间留空格："R&B 和说唱"、"流行和 R&B"。"""
    pad_l = " " if left[-1:].isascii() else ""
    pad_r = " " if right[:1].isascii() else ""
    return f"{left}{pad_l}{word}{pad_r}{right}"


def short_reason(a: Assignment, other_name: str) -> str:
    """网页"为什么放这里"的一句话；完整依据见 reason_text。"""
    r = a.reason
    why = r.get("why")
    if a.status == "unavailable":
        return "已下架或没版权，加不进去"
    if a.manual:
        return "你选了不放进新歌单" if a.status == "ungrouped" else "你手动放进来的"
    if why == "ungrouped":
        return f"没有匹配的歌单，「{other_name}」不建歌单"
    if why == "no_tags":
        return "网易云没给这首歌标曲风"
    if why == "multi_genre":
        tied = tied_categories(a)
        joined = _cjk_join(tied[0], "和", tied[1]) if len(tied) == 2 else "、".join(tied)
        return f"{joined}一样多，分不出主次"
    if why == "low_confidence":
        return "曲风信息不够，分不出主次"
    if r.get("merged_from"):
        return f"「{r['merged_from']}」没单独建歌单，并进了「{other_name}」"
    kept = (r.get("official") or {}).get("kept") or []
    if len(kept) <= 1:
        return f"只有一个曲风：{kept[0] if kept else a.category}"
    # 多个曲风也能分得清：例如"带国风标签的都算"，或几个曲风都归进「其他」
    n = "两" if len(kept) == 2 else f"{len(kept)} "
    genres = f"有 {'、'.join(kept)} {n}个曲风"
    if a.category == other_name:
        return f"{genres}，都没有单独的歌单，放进「{other_name}」"
    return f"{genres}，按现在的分法放进「{a.category}」"


def _row(a: Assignment, song: Song | None, playlist: str | None) -> dict[str, Any]:
    return {
        "序号": a.position + 1,
        "平台": a.key.platform,
        "歌曲ID": a.key.id,
        "歌名": song.name if song else "",
        "歌手": " / ".join(x.name for x in song.artists if x.name) if song else "",
        "专辑": (song.album_name or "") if song else "",
        "状态": STATUS_TEXT[a.status],
        "分类": a.category or "",
        "建议分类": a.reason.get("suggested") or "",
        "新歌单": playlist or "",
        "置信度": a.confidence,
        "依据": reason_text(a),
    }


def export_plan(plan: Plan, songs: Mapping[SongKey, Song], fmt: Format) -> str:
    playlists = plan.playlists()
    name_of = {k: p.name for p in playlists for k in p.songs}
    rows = [_row(a, songs.get(a.key), name_of.get(a.key)) for a in plan.assignments]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0]) if rows else ["序号"])
        w.writeheader()
        w.writerows({k: _csv_safe(v) for k, v in r.items()} for r in rows)
        return buf.getvalue()
    if fmt == "json":
        doc = {
            "plan_id": plan.id,
            "created_at": plan.created_at,
            "status": plan.status.value,
            "content_hash": plan.content_hash,
            "sources": [{"id": s.id, "name": s.name} for s in plan.sources],
            "taxonomy_version": plan.taxonomy_version,
            "playlists": [
                {"name": p.name, "category": p.category, "count": len(p.songs)} for p in playlists
            ],
            "songs": rows,
        }
        return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    if fmt == "md":
        return _markdown(plan, rows, playlists)
    raise ValueError(f"不支持的格式：{fmt}")


def _csv_safe(v: Any) -> Any:
    """防止表格软件把 =、+、-、@ 开头的歌名当公式执行（CSV 注入）。"""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


def _md_cell(v: Any) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def _md_table(rows: list[dict[str, Any]], cols: list[str]) -> list[str]:
    out = ["| " + " | ".join(cols) + " |", "|" + " --- |" * len(cols)]
    out += ["| " + " | ".join(_md_cell(r[c]) for c in cols) + " |" for r in rows]
    return out


def _markdown(plan: Plan, rows: list[dict[str, Any]], playlists: list[Any]) -> str:
    pending = [r for r in rows if r["状态"] == "待确认"]
    unavailable = [r for r in rows if r["状态"] == "无法加入"]
    ungrouped = [r for r in rows if r["状态"] == "未分组"]
    assigned = len(rows) - len(pending) - len(unavailable) - len(ungrouped)
    lines = [
        f"# 整理方案 {plan.id}",
        "",
        f"- 来源：{'、'.join(s.name for s in plan.sources)}",
        f"- 生成时间：{plan.created_at}",
        f"- 状态：{plan.status.value}",
        f"- 共 {len(rows)} 首：确定 {assigned}，"
        f"待确认 {len(pending)}，无法加入 {len(unavailable)}"
        + (f"，未分组 {len(ungrouped)}" if ungrouped else ""),
        "",
        f"## 将新建的歌单（{len(playlists)} 个）",
        "",
        *_md_table(
            [{"歌单": p.name, "歌曲数": len(p.songs)} for p in playlists], ["歌单", "歌曲数"]
        ),
        "",
        f"## 待确认（{len(pending)} 首，不写入）",
        "",
        *_md_table(pending, ["序号", "歌名", "歌手", "建议分类", "依据"]),
        "",
        f"## 无法加入（{len(unavailable)} 首）",
        "",
        *_md_table(unavailable, ["序号", "歌名", "歌手", "分类", "依据"]),
        "",
    ]
    if ungrouped:
        lines += [
            f"## 未分组（{len(ungrouped)} 首，不写入）",
            "",
            *_md_table(ungrouped, ["序号", "歌名", "歌手", "依据"]),
            "",
        ]
    for p in playlists:
        lines += [f"## {p.name}（{len(p.songs)} 首）", ""]
        lines += _md_table(
            [r for r in rows if r["新歌单"] == p.name], ["序号", "歌名", "歌手", "依据"]
        )
        lines.append("")
    return "\n".join(lines)

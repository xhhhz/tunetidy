from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path

import pytest

from tests.test_analyze import CFG, Clock, library
from tunetidy.analyze import analyze
from tunetidy.classify.pipeline import Assignment
from tunetidy.export import UNSURE_WHY, export_plan, reason_text, short_reason, tied_categories
from tunetidy.models import Song, SongKey
from tunetidy.storage import repo
from tunetidy.storage.db import connect


def build(tmp_path: Path) -> tuple[sqlite3.Connection, object, dict[object, Song]]:
    conn = connect(tmp_path / "t.db")
    b = library()
    plan = analyze(b, conn, CFG, b.list_playlists(), now=Clock())
    songs = repo.get_songs(conn, [a.key for a in plan.assignments])
    return conn, plan, songs  # type: ignore[return-value]


def test_csv_lists_every_song(tmp_path: Path) -> None:
    _, plan, songs = build(tmp_path)
    rows = list(csv.DictReader(io.StringIO(export_plan(plan, songs, "csv"))))  # type: ignore[arg-type]
    assert len(rows) == 8
    by_id = {r["歌曲ID"]: r for r in rows}
    assert by_id["5"]["状态"] == "待确认" and by_id["5"]["新歌单"] == ""
    assert "多个曲风并列" in by_id["5"]["依据"]
    assert by_id["8"]["状态"] == "无法加入"
    assert by_id["3"]["新歌单"] == "R&B · 整理自 我喜欢的音乐"
    assert "去掉泛化：流行" in by_id["3"]["依据"]


def test_json_has_plan_meta(tmp_path: Path) -> None:
    _, plan, songs = build(tmp_path)
    doc = json.loads(export_plan(plan, songs, "json"))  # type: ignore[arg-type]
    assert doc["plan_id"] == plan.id  # type: ignore[attr-defined]
    assert [p["count"] for p in doc["playlists"]] == [2, 2]
    assert len(doc["songs"]) == 8


def test_markdown_sections(tmp_path: Path) -> None:
    _, plan, songs = build(tmp_path)
    md = export_plan(plan, songs, "md")  # type: ignore[arg-type]
    assert "## 将新建的歌单（2 个）" in md
    assert "## 待确认（3 首，不写入）" in md
    assert "## 无法加入（1 首）" in md


def test_csv_injection_guard() -> None:
    from tunetidy.export import _csv_safe

    assert _csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert _csv_safe("@x") == "'@x" and _csv_safe("晴天") == "晴天" and _csv_safe(3) == 3


def test_reason_text_for_merge() -> None:
    from tunetidy.classify.pipeline import Assignment
    from tunetidy.models import SongKey

    reason = {"why": "assigned", "official": {"raw": ["爵士"]}, "merged_from": "爵士"}
    a = Assignment(SongKey("netease", "1"), 0, "assigned", "其他", 1.0, reason)
    assert reason_text(a) == "官方曲风：爵士；由「爵士」并入（不足最小数量或不在前 N 类）"


# ---------------------------------------------------------------- 一句话原因（网页"为什么放这里"）


def _a(
    status: str = "assigned",
    category: str | None = "华语流行",
    *,
    manual: bool = False,
    **reason: object,
) -> Assignment:
    return Assignment(SongKey("netease", "1"), 0, status, category, 1.0, dict(reason), manual)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("a", "expected"),
    [
        (_a("unavailable", None, why="unavailable"), "已下架或没版权，加不进去"),
        (_a("unavailable", None, manual=True), "已下架或没版权，加不进去"),
        (_a("ungrouped", None, manual=True, why="assigned"), "你选了不放进新歌单"),
        (_a("assigned", "睡前", manual=True, why="no_tags"), "你手动放进来的"),
        (_a("ungrouped", None, why="ungrouped"), "没有匹配的歌单，「其他」不建歌单"),
        (_a("pending", None, why="no_tags"), "网易云没给这首歌标曲风"),
        (
            _a("pending", None, why="multi_genre", scores={"说唱": 0.5, "R&B": 0.5}),
            "R&B 和说唱一样多，分不出主次",
        ),
        (
            _a(
                "pending",
                None,
                why="multi_genre",
                scores={"说唱": 1 / 3, "R&B": 1 / 3, "流行": 1 / 3},
            ),
            "R&B、流行、说唱一样多，分不出主次",
        ),
        (_a("pending", None, why="low_confidence"), "曲风信息不够，分不出主次"),
        (
            _a(
                "assigned",
                "其他",
                why="assigned",
                merged_from="布鲁斯",
                official={"kept": ["布鲁斯"]},
            ),
            "「布鲁斯」没单独建歌单，并进了「其他」",
        ),
        (
            _a("assigned", "华语流行", why="assigned", official={"kept": ["华语流行"]}),
            "只有一个曲风：华语流行",
        ),
        (
            _a("assigned", "R&B-当代", why="assigned", official={"kept": []}),
            "只有一个曲风：R&B-当代",
        ),
        (
            _a("assigned", "国风", why="assigned", official={"kept": ["R&B", "国风"]}),
            "有 R&B、国风 两个曲风，按现在的分法放进「国风」",
        ),
        (
            _a("assigned", "其他", why="assigned", official={"kept": ["R&B", "嘻哈说唱", "电子"]}),
            "有 R&B、嘻哈说唱、电子 3 个曲风，都没有单独的歌单，放进「其他」",
        ),
    ],
)
def test_short_reason(a: Assignment, expected: str) -> None:
    assert short_reason(a, "其他") == expected


def test_tied_categories() -> None:
    assert tied_categories(_a(scores={"说唱": 0.5, "R&B": 0.5})) == ["R&B", "说唱"]
    assert tied_categories(_a(scores={"说唱": 0.7, "R&B": 0.3})) == ["说唱"]
    assert tied_categories(_a()) == []


def test_unsure_reasons() -> None:
    assert frozenset({"no_tags", "multi_genre", "low_confidence"}) == UNSURE_WHY

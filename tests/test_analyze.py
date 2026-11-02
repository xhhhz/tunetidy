"""analyze：缓存、续传、云盘歌曲、多来源合并、快照可复现、方案持久化。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.classify.helpers import netease_taxonomy, song, tags
from tests.fakes import FakeBackend
from tunetidy.analyze import analyze, classify_from_snapshot
from tunetidy.backends.base import BackendError
from tunetidy.config import ClassifyConfig, Config
from tunetidy.models import CopyrightStatus
from tunetidy.plan import PlanStatus
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import connect

CFG = Config(classify=ClassifyConfig(min_size=2))


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t


def library() -> FakeBackend:
    b = FakeBackend(style_tree=list(netease_taxonomy().nodes.values()))
    genres = {1: ["流行-华语流行"], 2: ["流行"], 3: ["R&B", "流行"], 4: ["R&B"],
              5: ["R&B", "嘻哈说唱"], 6: [], 8: ["摇滚"]}  # fmt: skip
    songs = [song(i) for i in range(1, 7)]
    songs.append(song(7, CopyrightStatus.CLOUD))
    songs.append(song(8, CopyrightStatus.UNAVAILABLE))
    for i, g in genres.items():
        b.tags[str(i)] = tags(i, *g)
    b.add_playlist("100", "我喜欢的音乐", songs)
    return b


def test_first_analysis_builds_plan(conn: sqlite3.Connection) -> None:
    b = library()
    src = b.list_playlists()
    plan = analyze(b, conn, CFG, src, now=Clock())
    assert plan.status == PlanStatus.DRAFT
    # 云盘歌曲不请求百科
    assert b.tag_requests == ["1", "2", "3", "4", "5", "6", "8"]
    by_id = {a.key.id: a for a in plan.assignments}
    assert by_id["1"].category == by_id["2"].category == "流行"
    assert by_id["3"].category == by_id["4"].category == "R&B"
    assert [by_id[i].status for i in ("5", "6", "7", "8")] == [
        "pending",
        "pending",
        "pending",
        "unavailable",
    ]
    names = [p.name for p in plan.playlists()]
    assert names == ["R&B · 整理自 我喜欢的音乐", "流行 · 整理自 我喜欢的音乐"]


def test_second_analysis_uses_cache(conn: sqlite3.Connection) -> None:
    b = library()
    src = b.list_playlists()
    clock = Clock()
    first = analyze(b, conn, CFG, src, now=clock)
    requests_after_first = len(b.tag_requests)
    clock.t += timedelta(minutes=5)
    second = analyze(b, conn, CFG, src, now=clock)
    assert len(b.tag_requests) == requests_after_first  # 标签全部命中缓存
    assert b.calls["get_style_tree"] == 1  # 曲风树也命中缓存
    assert second.id != first.id  # 每次 analyze 生成新方案
    assert second.content_hash == first.content_hash  # 内容相同


def test_cache_expires(conn: sqlite3.Connection) -> None:
    b = library()
    src = b.list_playlists()
    clock = Clock()
    analyze(b, conn, CFG, src, now=clock)
    clock.t += timedelta(days=31)
    analyze(b, conn, CFG, src, now=clock)
    assert len(b.tag_requests) == 14
    assert b.calls["get_style_tree"] == 2


def test_interrupted_tag_fetch_resumes(conn: sqlite3.Connection) -> None:
    b = library()
    src = b.list_playlists()
    b.fail_tags_after = 3
    with pytest.raises(BackendError):
        analyze(b, conn, CFG, src, now=Clock())
    assert plan_store.list_plans(conn) == []  # 中断时不生成方案
    b.fail_tags_after = None
    plan = analyze(b, conn, CFG, src, now=Clock())
    assert b.tag_requests == ["1", "2", "3", "4", "5", "6", "8"]  # 只补取剩下的
    assert len(plan.assignments) == 8


def test_multiple_sources_dedupe_and_label(conn: sqlite3.Connection) -> None:
    b = library()
    b.add_playlist("200", "通勤", [song(4), song(9)])
    b.tags["9"] = tags(9, "R&B")
    plan = analyze(b, conn, CFG, b.list_playlists(), now=Clock())
    ids = [a.key.id for a in plan.assignments]
    assert ids == ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
    assert plan.playlists()[0].name == "R&B · 整理自 我喜欢的音乐 等 2 个歌单"
    assert [k.id for k in plan.playlists()[0].songs] == ["3", "4", "9"]


def test_snapshot_reproduces_assignments(conn: sqlite3.Connection) -> None:
    b = library()
    plan = analyze(b, conn, CFG, b.list_playlists(), now=Clock())
    loaded = plan_store.load_plan(conn, plan.id)
    assert tuple(classify_from_snapshot(loaded)) == plan.assignments


def test_plan_roundtrip_and_hash_recomputed(conn: sqlite3.Connection) -> None:
    b = library()
    plan = analyze(b, conn, CFG, b.list_playlists(), now=Clock())
    loaded = plan_store.load_plan(conn, plan.id)
    assert loaded == plan
    # 有人直接改了库里的分类：重新加载时哈希按实际内容重算，不再等于原值
    conn.execute(
        "UPDATE assignments SET category = '摇滚' WHERE plan_id = ? AND song_id = '1'", (plan.id,)
    )
    assert plan_store.load_plan(conn, plan.id).content_hash != plan.content_hash


def test_missing_plan(conn: sqlite3.Connection) -> None:
    with pytest.raises(plan_store.PlanNotFoundError):
        plan_store.load_plan(conn, "nope")


def test_requires_source(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        analyze(library(), conn, CFG, [], now=Clock())


def test_progress_callback(conn: sqlite3.Connection) -> None:
    events: list[tuple[str, int, int]] = []
    b = library()
    analyze(b, conn, CFG, b.list_playlists(), now=Clock(), on_progress=lambda *e: events.append(e))
    assert events[0] == ("tracks", 0, 1)
    assert ("tags", 0, 7) in events and events[-1] == ("tags", 7, 7)


def test_split_settings_saved_and_reproduced(conn: sqlite3.Connection) -> None:
    from tunetidy.classify.split import SplitSettings, only

    b = library()
    split = SplitSettings(groups=only(["流行"]), match="any")
    plan = analyze(b, conn, CFG, b.list_playlists(), now=Clock(), split=split)
    loaded = plan_store.load_plan(conn, plan.id)
    assert SplitSettings.from_dict(loaded.settings["split"]) == split
    assert tuple(classify_from_snapshot(loaded)) == plan.assignments
    by_id = {a.key.id: a for a in plan.assignments}
    assert by_id["3"].category == "流行"  # R&B + 流行：any 模式归流行
    assert by_id["4"].category == "其他"


def test_bad_group_fails_before_fetching_tags(conn: sqlite3.Connection) -> None:
    from tunetidy.classify.split import SplitError, SplitSettings, only

    b = library()
    with pytest.raises(SplitError, match="曲风树里没有"):
        analyze(b, conn, CFG, b.list_playlists(), now=Clock(),
                split=SplitSettings(groups=only(["流形"])))  # fmt: skip
    assert b.tag_requests == []


def test_old_plans_without_split_reproduce_as_level1(conn: sqlite3.Connection) -> None:
    b = library()
    plan = analyze(b, conn, CFG, b.list_playlists(), now=Clock())
    settings = dict(plan.settings)
    settings.pop("split")
    conn.execute(
        "UPDATE plans SET settings_json = ? WHERE id = ?",
        (__import__("json").dumps(settings, ensure_ascii=False), plan.id),
    )
    assert tuple(classify_from_snapshot(plan_store.load_plan(conn, plan.id))) == plan.assignments

"""写回：dry-run、先记意图、标记、倒序分批、回读、续传、绝不碰原有歌单。全部用假后端。"""

from __future__ import annotations

import random
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.classify.helpers import netease_taxonomy, song, tags
from tests.fakes import FakeBackend
from tests.test_analyze import Clock
from tunetidy.analyze import analyze
from tunetidy.apply.approval import ApprovalError
from tunetidy.apply.writer import ApplyError, Writer, dry_run_lines
from tunetidy.backends.base import BackendError
from tunetidy.config import ApplyConfig, ClassifyConfig, Config
from tunetidy.models import CopyrightStatus, PlaylistInfo
from tunetidy.plan import Plan, PlanStatus
from tunetidy.storage import plans as plan_store
from tunetidy.storage import runs as run_store
from tunetidy.storage.db import connect

CFG = Config(classify=ClassifyConfig(min_size=2))
APPLY = ApplyConfig(batch_size=2, batch_pause_min=0, batch_pause_max=0)
ORIGINAL = "100"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def build_library() -> FakeBackend:
    """原歌单：流行 7 首、R&B 5 首、多曲风 2 首、下架 2 首、云盘 1 首（无标签，待确认）。"""
    b = FakeBackend(style_tree=list(netease_taxonomy().nodes.values()))
    songs = []
    for i in range(1, 8):
        songs.append(song(i))
        b.tags[str(i)] = tags(i, "流行-华语流行")
    for i in range(8, 13):
        songs.append(song(i, CopyrightStatus.VIP))  # VIP 照常写入
        b.tags[str(i)] = tags(i, "R&B", "流行")
    for i in (13, 14):
        songs.append(song(i))
        b.tags[str(i)] = tags(i, "R&B", "嘻哈说唱")
    for i in (15, 16):
        songs.append(song(i, CopyrightStatus.UNAVAILABLE))
        b.tags[str(i)] = tags(i, "流行")
        b.unavailable_ids.add(str(i))
    songs.append(song(17, CopyrightStatus.CLOUD))
    b.add_playlist(ORIGINAL, "我喜欢的音乐", songs)
    return b


def approved_plan(b: FakeBackend, conn: sqlite3.Connection) -> Plan:
    plan = analyze(b, conn, CFG, [b.list_playlists()[0]], now=Clock())
    plan_store.record_approval(conn, plan.id, plan.content_hash, "2026-10-30T12:00:00+00:00")
    return plan_store.load_plan(conn, plan.id)


def writer(b: FakeBackend, conn: sqlite3.Connection, **kw: object) -> Writer:
    return Writer(b, conn, APPLY, sleep=lambda _s: None, rng=random.Random(0), **kw)  # type: ignore[arg-type]


def new_playlists(b: FakeBackend) -> dict[str, list[str]]:
    return {p.info.name: [s.key.id for s in p.songs] for p in b.owned_by_tool()}


def original_songs(b: FakeBackend) -> list[str]:
    return [s.key.id for s in b.playlists[ORIGINAL].songs]


EXPECTED = {
    "流行 · 整理自 我喜欢的音乐": ["1", "2", "3", "4", "5", "6", "7"],
    "R&B · 整理自 我喜欢的音乐": ["8", "9", "10", "11", "12"],
}


# ------------------------------------------------------------------ dry-run


def test_dry_run_writes_nothing(conn: sqlite3.Connection) -> None:
    b = build_library()
    plan = analyze(b, conn, CFG, b.list_playlists(), now=Clock())  # 未批准也能 dry-run
    lines = dry_run_lines(plan, APPLY)
    assert any("新建私密歌单「流行 · 整理自 我喜欢的音乐」，分 4 批写入 7 首" in x for x in lines)
    assert any("待确认 3 首，无法加入 2 首" in x for x in lines)
    assert b.writes == []
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


# ------------------------------------------------------------------ 正常执行


def test_execute_creates_private_playlists_in_plan_order(conn: sqlite3.Connection) -> None:
    b = build_library()
    before = original_songs(b)
    plan = approved_plan(b, conn)
    result = writer(b, conn).execute(plan)

    assert new_playlists(b) == EXPECTED  # 最终顺序与方案一致（倒序分批写入的效果）
    assert all(p.private for p in b.owned_by_tool())
    run = run_store.load_run(conn, result.run_id)
    for rp in run.playlists:
        desc = b.playlists[rp.playlist_id or ""].description
        assert run.playlist_marker(rp.index) in desc
    assert [(n, c) for n, _, c in result.created] == [(n, len(v)) for n, v in EXPECTED.items()]
    assert result.unaddable == 0
    assert plan_store.load_plan(conn, plan.id).status == PlanStatus.APPLIED
    assert run.status == "done"
    # 原歌单原封不动；没有任何删除或移除操作
    assert original_songs(b) == before
    assert not any(w[0] in ("remove_tracks", "delete_playlist") for w in b.writes)
    assert all(w[1] != ORIGINAL for w in b.writes)


def test_batches_respect_limit_and_skip_unavailable_and_pending(conn: sqlite3.Connection) -> None:
    b = build_library()
    writer(b, conn).execute(approved_plan(b, conn))
    sent = [sid for w in b.writes if w[0] == "add_tracks" for sid in w[2]]
    assert all(len(w[2]) <= 2 for w in b.writes if w[0] == "add_tracks")
    assert not set(sent) & {"13", "14", "15", "16", "17"}  # 待确认、下架、云盘(无标签)都不写
    assert sorted(sent, key=int) == [str(i) for i in range(1, 13)]


def test_cannot_execute_twice_or_unapproved(conn: sqlite3.Connection) -> None:
    b = build_library()
    plan = approved_plan(b, conn)
    writer(b, conn).execute(plan)
    with pytest.raises(ApprovalError, match="已执行过"):
        writer(b, conn).execute(plan_store.load_plan(conn, plan.id))
    assert len(b.owned_by_tool()) == 2  # 没有第二批歌单

    b2 = build_library()
    draft = analyze(b2, conn, CFG, [b2.list_playlists()[0]], now=Clock())
    with pytest.raises(ApprovalError, match="还没有批准"):
        writer(b2, conn).execute(draft)
    assert b2.writes == []


def test_unaddable_songs_recorded_from_read_back(conn: sqlite3.Connection) -> None:
    b = build_library()
    b.reject_ids = {"3"}  # 平台返回成功但实际没加进去
    result = writer(b, conn).execute(approved_plan(b, conn))
    assert result.unaddable == 1
    assert new_playlists(b)["流行 · 整理自 我喜欢的音乐"] == ["1", "2", "4", "5", "6", "7"]
    rows = conn.execute("SELECT status FROM run_tracks WHERE song_id = '3'").fetchall()
    assert [r["status"] for r in rows] == ["unaddable"]


# ------------------------------------------------------------------ 先记意图


def test_intent_recorded_before_first_write(conn: sqlite3.Connection) -> None:
    b = build_library()
    b.fail_before["create_playlist"] = {1}
    plan = approved_plan(b, conn)
    with pytest.raises(BackendError):
        writer(b, conn).execute(plan)
    run = run_store.active_run_for_plan(conn, plan.id)
    assert run is not None and run.status == "interrupted"
    assert [p.status for p in run.playlists] == ["planned", "planned"]
    assert conn.execute("SELECT COUNT(*) FROM run_tracks").fetchone()[0] == 12
    assert plan_store.load_plan(conn, plan.id).status == PlanStatus.APPLYING
    assert b.owned_by_tool() == []


# ------------------------------------------------------------------ 续传


def resume(b: FakeBackend, conn: sqlite3.Connection, plan_id: str, **kw: object) -> object:
    return writer(b, conn, **kw).resume(plan_store.load_plan(conn, plan_id))


def test_execute_refuses_interrupted_plan(conn: sqlite3.Connection) -> None:
    b = build_library()
    b.fail_before["add_tracks"] = {1}
    plan = approved_plan(b, conn)
    with pytest.raises(BackendError):
        writer(b, conn).execute(plan)
    with pytest.raises(ApprovalError, match="续传"):
        writer(b, conn).execute(plan_store.load_plan(conn, plan.id))


@pytest.mark.parametrize(
    "fault",
    [
        ("fail_before", "create_playlist", 2),  # 第二个歌单还没建
        ("fail_after", "create_playlist", 1),  # 建好且写了标记，但没记进数据库
        ("fail_before", "add_tracks", 3),  # 某批请求没发出去
        ("fail_after", "add_tracks", 3),  # 某批已加入，但没记进数据库
        ("fail_after", "add_tracks", 4),  # 第一个歌单最后一批加入后中断
    ],
)
def test_resume_after_interruption(conn: sqlite3.Connection, fault: tuple[str, str, int]) -> None:
    kind, method, n = fault
    b = build_library()
    getattr(b, kind)[method] = {n}
    plan = approved_plan(b, conn)
    with pytest.raises(BackendError):
        writer(b, conn).execute(plan)
    getattr(b, kind)[method] = set()

    resume(b, conn, plan.id)
    assert new_playlists(b) == EXPECTED  # 不重复建歌单、不重复加歌、顺序正确
    assert len(b.owned_by_tool()) == 2
    assert plan_store.load_plan(conn, plan.id).status == PlanStatus.APPLIED
    statuses = {r[0] for r in conn.execute("SELECT status FROM run_tracks").fetchall()}
    assert statuses == {"added"}


def test_resume_twice_after_repeated_failures(conn: sqlite3.Connection) -> None:
    b = build_library()
    b.fail_after["add_tracks"] = {2}
    plan = approved_plan(b, conn)
    with pytest.raises(BackendError):
        writer(b, conn).execute(plan)
    b.fail_after["add_tracks"] = {4}
    with pytest.raises(BackendError):
        resume(b, conn, plan.id)
    b.fail_after["add_tracks"] = set()
    resume(b, conn, plan.id)
    assert new_playlists(b) == EXPECTED


def test_resume_requires_applying_and_unchanged_plan(conn: sqlite3.Connection) -> None:
    b = build_library()
    plan = approved_plan(b, conn)
    with pytest.raises(ApplyError, match="不在执行中"):
        resume(b, conn, plan.id)
    b.fail_before["add_tracks"] = {1}
    with pytest.raises(BackendError):
        writer(b, conn).execute(plan)
    conn.execute(
        "UPDATE assignments SET category = '摇滚' WHERE plan_id = ? AND song_id = '1'", (plan.id,)
    )
    with pytest.raises(ApplyError, match="发生了变化"):
        resume(b, conn, plan.id)


# ------------------------------------------------------------------ 建好歌单但标记没写进去


def crash_before_marker(conn: sqlite3.Connection) -> tuple[FakeBackend, Plan]:
    b = build_library()
    b.crash_between_create_and_desc = True
    plan = approved_plan(b, conn)
    with pytest.raises(BackendError):
        writer(b, conn).execute(plan)
    unmarked = [p for p in b.playlists.values() if p.info.id != ORIGINAL]
    assert len(unmarked) == 1 and unmarked[0].description == ""
    return b, plan


def test_unmarked_leftover_adopted_only_with_confirmation(conn: sqlite3.Connection) -> None:
    b, plan = crash_before_marker(conn)
    asked: list[list[PlaylistInfo]] = []

    def confirm(name: str, candidates: list[PlaylistInfo]) -> PlaylistInfo | None:
        asked.append(candidates)
        return candidates[0]

    result = resume(b, conn, plan.id, confirm_adopt=confirm)
    assert len(asked) == 1 and len(asked[0]) == 1
    names = [p.info.name for p in b.playlists.values() if p.info.id != ORIGINAL]
    assert sorted(names) == sorted(EXPECTED)  # 沿用了那个空歌单，没有重复建
    assert any("沿用了没有标记的空歌单" in w for w in result.warnings)  # type: ignore[attr-defined]


def test_unmarked_leftover_not_adopted_without_confirmation(conn: sqlite3.Connection) -> None:
    b, plan = crash_before_marker(conn)
    result = resume(b, conn, plan.id)  # 没有确认回调（如非交互调用）：不沿用，另建
    assert new_playlists(b) == EXPECTED
    leftovers = [p for p in b.playlists.values() if p.info.id != ORIGINAL and not p.description]
    assert len(leftovers) == 1 and leftovers[0].songs == []
    assert any("没有沿用" in w for w in result.warnings)  # type: ignore[attr-defined]


def test_user_playlist_with_same_name_is_never_adopted(conn: sqlite3.Connection) -> None:
    """同名但不是空的歌单（用户自己的）绝不会被当成候选。"""
    b, plan = crash_before_marker(conn)
    leftover = next(p for p in b.playlists.values() if p.info.id != ORIGINAL)
    leftover.songs.append(song(99))  # 用户往里加了歌：不再是"空的"
    asked: list[object] = []
    resume(b, conn, plan.id, confirm_adopt=lambda n, c: asked.append(c) or c[0])
    assert asked == []
    assert [s.key.id for s in leftover.songs] == ["99"]  # 原样保留


def test_progress_reports(conn: sqlite3.Connection) -> None:
    b = build_library()
    events: list[tuple[str, int, int]] = []
    writer(b, conn, on_progress=lambda *e: events.append(e)).execute(approved_plan(b, conn))
    assert events[-1] == ("R&B · 整理自 我喜欢的音乐", 5, 5)
    assert ("流行 · 整理自 我喜欢的音乐", 7, 7) in events

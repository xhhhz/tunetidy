"""撤销：先回读对比；一致才删歌单，否则只移除本工具加入的歌；绝不碰原有歌单。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.apply.test_approval import Terminal
from tests.apply.test_writer import (
    EXPECTED,
    ORIGINAL,
    approved_plan,
    build_library,
    original_songs,
    writer,
)
from tests.classify.helpers import song
from tests.fakes import FakeBackend, FakePlaylist
from tunetidy.apply import approval
from tunetidy.apply.approval import ApprovalError
from tunetidy.apply.undo import UndoError, execute_undo, plan_undo, undo_interactive, undo_lines
from tunetidy.backends.base import BackendError
from tunetidy.plan import PlanStatus
from tunetidy.storage import plans as plan_store
from tunetidy.storage import runs as run_store
from tunetidy.storage.db import connect

POP = "流行 · 整理自 我喜欢的音乐"
RNB = "R&B · 整理自 我喜欢的音乐"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def applied(conn: sqlite3.Connection) -> tuple[FakeBackend, str, str]:
    b = build_library()
    plan = approved_plan(b, conn)
    result = writer(b, conn).execute(plan)
    return b, plan.id, result.run_id


def tool_playlist(b: FakeBackend, name: str) -> FakePlaylist:
    return next(p for p in b.owned_by_tool() if p.info.name == name)


def by_name(b: FakeBackend) -> dict[str, list[str]]:
    return {p.info.name: [s.key.id for s in p.songs] for p in b.playlists.values()}


def items(conn: sqlite3.Connection, b: FakeBackend, run_id: str) -> dict[str, tuple[str, ...]]:
    up = plan_undo(b, conn, run_id)
    return {it.playlist.name: (it.action, *it.differences) for it in up.items}


# ------------------------------------------------------------------ 对比


def test_untouched_playlists_are_deleted(conn: sqlite3.Connection) -> None:
    b, plan_id, run_id = applied(conn)
    before = original_songs(b)
    assert items(conn, b, run_id) == {POP: ("delete",), RNB: ("delete",)}
    result = execute_undo(b, conn, plan_undo(b, conn, run_id))
    assert sorted(result.deleted) == sorted(EXPECTED)
    assert b.owned_by_tool() == []
    assert original_songs(b) == before  # 原歌单原封不动
    assert all(w[1] != ORIGINAL for w in b.writes)
    assert run_store.load_run(conn, run_id).status == "undone"
    assert plan_store.load_plan(conn, plan_id).status == PlanStatus.UNDONE


@pytest.mark.parametrize(
    ("change", "expected_diff"),
    [
        (lambda p: p.songs.insert(0, song(99)), "你另外加入了 1 首歌（会保留）"),
        (lambda p: setattr(p, "info", p.info.__class__(p.info.platform, p.info.id, "改名了",
                                                        0, True)), "歌单名被改为「改名了」"),
        (lambda p: setattr(p, "private", False), "歌单被设为公开"),
        (lambda p: setattr(p, "subscribed_count", 3), "已有 3 人收藏"),
        (lambda p: setattr(p, "description", "用户重写了简介"), "简介里找不到运行标记"),
        (lambda p: p.songs.pop(), "本工具加入的歌里有 1 首已被移除"),
    ],
)  # fmt: skip
def test_modified_playlist_keeps_playlist_and_removes_only_ours(
    conn: sqlite3.Connection, change: Callable[[FakePlaylist], object], expected_diff: str
) -> None:
    b, _, run_id = applied(conn)
    target = tool_playlist(b, POP)
    pid = target.info.id
    change(target)
    up = plan_undo(b, conn, run_id)
    pop_item = next(it for it in up.items if it.playlist.name == POP)
    assert pop_item.action == "remove_ours"
    assert any(expected_diff in d for d in pop_item.differences), pop_item.differences
    execute_undo(b, conn, up)
    assert pid in b.playlists  # 歌单保留
    left = [s.key.id for s in b.playlists[pid].songs]
    assert left == (["99"] if expected_diff.startswith("你另外加入") else [])
    assert RNB not in by_name(b)  # 另一个没被改的照常删除
    assert not any(w == ("delete_playlist", pid, ()) for w in b.writes)


def test_playlist_deleted_by_user_is_skipped(conn: sqlite3.Connection) -> None:
    b, _, run_id = applied(conn)
    del b.playlists[tool_playlist(b, POP).info.id]
    real_get = b.get_playlist

    def get(pid: str):  # type: ignore[no-untyped-def]
        if pid not in b.playlists:
            raise BackendError("歌单不存在")
        return real_get(pid)

    b.get_playlist = get  # type: ignore[method-assign]
    up = plan_undo(b, conn, run_id)
    assert {it.playlist.name: it.action for it in up.items} == {POP: "skip", RNB: "delete"}
    result = execute_undo(b, conn, up)
    assert result.skipped == [POP] and result.deleted == [RNB]


def test_change_between_preview_and_execution(conn: sqlite3.Connection) -> None:
    b, _, run_id = applied(conn)
    up = plan_undo(b, conn, run_id)  # 预览：两个都"删除"
    tool_playlist(b, POP).songs.insert(0, song(99))  # 用户在确认期间加了歌
    result = execute_undo(b, conn, up)
    assert POP in by_name(b) and by_name(b)[POP] == ["99"]
    assert any("在你确认期间被修改" in w for w in result.warnings)


def test_undo_twice_and_running(conn: sqlite3.Connection) -> None:
    b, _, run_id = applied(conn)
    execute_undo(b, conn, plan_undo(b, conn, run_id))
    with pytest.raises(UndoError, match="已经撤销过"):
        plan_undo(b, conn, run_id)
    conn.execute("UPDATE runs SET status = 'running' WHERE id = ?", (run_id,))
    with pytest.raises(UndoError, match="正在执行"):
        plan_undo(b, conn, run_id)


def test_undone_plan_cannot_be_applied_again(conn: sqlite3.Connection) -> None:
    b, plan_id, run_id = applied(conn)
    execute_undo(b, conn, plan_undo(b, conn, run_id))
    with pytest.raises(ApprovalError, match="已撤销"):
        writer(b, conn).execute(plan_store.load_plan(conn, plan_id))


def test_undo_interrupted_apply(conn: sqlite3.Connection) -> None:
    """写回中途中断后，也可以直接撤销已经写入的部分。"""
    b = build_library()
    b.fail_before["create_playlist"] = {2}
    plan = approved_plan(b, conn)
    with pytest.raises(BackendError):
        writer(b, conn).execute(plan)
    run = run_store.active_run_for_plan(conn, plan.id)
    assert run is not None
    up = plan_undo(b, conn, run.id)
    assert {it.playlist.name: it.action for it in up.items} == {POP: "delete", RNB: "skip"}
    execute_undo(b, conn, up)
    assert b.owned_by_tool() == []
    assert plan_store.load_plan(conn, plan.id).status == PlanStatus.UNDONE


def test_interrupted_undo_can_continue(conn: sqlite3.Connection) -> None:
    b, _, run_id = applied(conn)
    tool_playlist(b, RNB).private = False  # R&B 走"只移除"
    b.fail_before["remove_tracks"] = {1}
    with pytest.raises(BackendError):
        execute_undo(b, conn, plan_undo(b, conn, run_id))
    assert run_store.load_run(conn, run_id).status == "undoing"
    b.fail_before["remove_tracks"] = set()
    up = plan_undo(b, conn, run_id)
    assert {it.playlist.name: it.action for it in up.items} == {POP: "skip", RNB: "remove_ours"}
    execute_undo(b, conn, up)
    assert by_name(b)[RNB] == [] and POP not in by_name(b)


def test_lines_describe_actions(conn: sqlite3.Connection) -> None:
    b, _, run_id = applied(conn)
    tool_playlist(b, POP).songs.insert(0, song(99))
    text = "\n".join(undo_lines(plan_undo(b, conn, run_id)))
    assert f"删除歌单「{RNB}」（5 首，与写入记录完全一致）" in text
    assert f"保留歌单「{POP}」，只移除本工具加入的 7 首" in text
    assert "你另外加入了 1 首歌（会保留）" in text
    assert "不会碰你原有的任何歌单" in text


# ------------------------------------------------------------------ 交互式


def test_undo_requires_terminal(conn: sqlite3.Connection) -> None:
    b, _, run_id = applied(conn)
    with pytest.raises(ApprovalError, match="交互式终端"):
        undo_interactive(b, conn, run_id, Terminal().ask, Terminal().say)
    assert len(b.owned_by_tool()) == 2


def test_undo_wrong_code(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approval, "is_interactive", lambda: True)
    b, _, run_id = applied(conn)
    with pytest.raises(UndoError, match="确认码不符"):
        undo_interactive(b, conn, run_id, Terminal(correct=False).ask, Terminal().say)
    assert len(b.owned_by_tool()) == 2
    assert run_store.load_run(conn, run_id).status == "done"


def test_undo_with_code(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approval, "is_interactive", lambda: True)
    b, _, run_id = applied(conn)
    t = Terminal()
    result = undo_interactive(b, conn, run_id, t.ask, t.say)
    assert result is not None and len(result.deleted) == 2
    assert any("确认撤销请输入" in x for x in t.out)


def test_nothing_to_undo(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approval, "is_interactive", lambda: True)
    b = build_library()
    b.fail_before["create_playlist"] = {1}
    plan = approved_plan(b, conn)
    with pytest.raises(BackendError):
        writer(b, conn).execute(plan)
    run = run_store.active_run_for_plan(conn, plan.id)
    assert run is not None
    t = Terminal()
    assert undo_interactive(b, conn, run.id, t.ask, t.say) is None
    assert any("没有需要撤销的内容" in x for x in t.out)

"""人工"移动到"、预估与曲风统计：只改本地方案，批准随内容变化作废。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.apply.test_writer import build_library
from tests.test_analyze import CFG, Clock
from tunetidy.analyze import (
    ConfirmResetError,
    MoveError,
    ReclassifyError,
    analyze,
    genre_counts,
    move_songs,
    reclassify,
    reset_songs,
    simulate,
)
from tunetidy.classify.pipeline import summarize
from tunetidy.classify.split import SplitSettings, only
from tunetidy.export import reason_text
from tunetidy.models import SongKey
from tunetidy.plan import Plan, PlanStatus
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import connect


def k(i: int) -> SongKey:
    return SongKey("netease", str(i))


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def plan(conn: sqlite3.Connection) -> Plan:
    b = build_library()
    return analyze(b, conn, CFG, [b.list_playlists()[0]], now=Clock())


def by_id(p: Plan) -> dict[str, object]:
    return {a.key.id: a for a in p.assignments}


def counts(p: Plan) -> dict[str, int]:
    return {pl.category: len(pl.songs) for pl in p.playlists()}


def test_move_to_existing_category(conn: sqlite3.Connection, plan: Plan) -> None:
    r = move_songs(conn, plan.id, [k(1), k(2)], "R&B")
    assert r.changed and not r.approval_reset and r.kept_manual == 2
    assert counts(r.plan) == {"流行": 5, "R&B": 7}
    a = r.plan.assignments[0]
    assert a.category == "R&B" and a.manual and a.status == "assigned"
    assert reason_text(a).startswith("人工调整")
    assert r.plan == plan_store.load_plan(conn, plan.id)


def test_move_to_new_category(conn: sqlite3.Connection, plan: Plan) -> None:
    r = move_songs(conn, plan.id, [k(1)], "  睡前  ")
    assert counts(r.plan) == {"流行": 6, "R&B": 5, "睡前": 1}


def test_move_pending_song_into_category(conn: sqlite3.Connection, plan: Plan) -> None:
    assert len(plan.pending()) == 3
    r = move_songs(conn, plan.id, [k(13)], "R&B")
    assert len(r.plan.pending()) == 2 and counts(r.plan)["R&B"] == 6


def test_move_to_not_written(conn: sqlite3.Connection, plan: Plan) -> None:
    r = move_songs(conn, plan.id, [k(1)], None)
    a = r.plan.assignments[0]
    assert a.status == "ungrouped" and a.category is None and a.manual
    assert k(1) not in {s for p in r.plan.playlists() for s in p.songs}
    assert [x.key for x in r.plan.ungrouped()] == [k(1)]


@pytest.mark.parametrize(
    ("keys", "category", "match"),
    [
        ([k(15)], "流行", "下架或无版权"),
        ([k(99)], "流行", "不在这份方案里"),
        ([k(1)], "  ", "分类名不能为空"),
        ([], "流行", "没有选中"),
    ],
)
def test_bad_moves(
    conn: sqlite3.Connection, plan: Plan, keys: list[SongKey], category: str, match: str
) -> None:
    with pytest.raises(MoveError, match=match):
        move_songs(conn, plan.id, keys, category)
    assert plan_store.load_plan(conn, plan.id) == plan


def test_approved_plan_needs_confirmation(conn: sqlite3.Connection, plan: Plan) -> None:
    plan_store.record_approval(conn, plan.id, plan.content_hash, "t")
    approved = plan_store.load_plan(conn, plan.id)
    with pytest.raises(ConfirmResetError, match="批准作废"):
        move_songs(conn, plan.id, [k(1)], "R&B", confirm_reset=False)
    with pytest.raises(ConfirmResetError):
        reclassify(conn, plan.id, SplitSettings(level=2), confirm_reset=False)
    assert plan_store.load_plan(conn, plan.id) == approved
    r = move_songs(conn, plan.id, [k(1)], "R&B", confirm_reset=True)
    assert r.approval_reset and r.plan.status == PlanStatus.DRAFT and r.plan.approved_hash is None


def test_move_to_same_place_keeps_approval(conn: sqlite3.Connection, plan: Plan) -> None:
    plan_store.record_approval(conn, plan.id, plan.content_hash, "t")
    r = move_songs(conn, plan.id, [k(1)], "流行")
    assert not r.changed and not r.approval_reset and r.plan.status == PlanStatus.APPROVED


@pytest.mark.parametrize("status", ["applying", "applied", "undone"])
def test_executed_plans_are_read_only(conn: sqlite3.Connection, plan: Plan, status: str) -> None:
    conn.execute("UPDATE plans SET status = ? WHERE id = ?", (status, plan.id))
    with pytest.raises(ReclassifyError, match="不能再修改"):
        move_songs(conn, plan.id, [k(1)], "R&B")


def test_stale_hash_is_rejected(conn: sqlite3.Connection, plan: Plan) -> None:
    move_songs(conn, plan.id, [k(1)], "R&B")  # 别处先改了一次
    with pytest.raises(plan_store.PlanStateError, match="已在别处被修改"):
        move_songs(conn, plan.id, [k(2)], "R&B", expected_hash=plan.content_hash)
    with pytest.raises(plan_store.PlanStateError):
        reclassify(conn, plan.id, SplitSettings(level=2), expected_hash=plan.content_hash)


def test_moves_survive_reclassify(conn: sqlite3.Connection, plan: Plan) -> None:
    move_songs(conn, plan.id, [k(1)], "睡前")
    move_songs(conn, plan.id, [k(2)], None)
    r = reclassify(conn, plan.id, SplitSettings(groups=only(["流行"])))
    a = by_id(r.plan)
    assert a["1"].category == "睡前" and a["2"].status == "ungrouped"  # type: ignore[attr-defined]
    assert r.kept_manual == 2


def test_simulate_does_not_save(conn: sqlite3.Connection, plan: Plan) -> None:
    pop = SplitSettings(groups=only(["流行"]))
    pop_any = SplitSettings(groups=pop.groups, match="any")
    primary = summarize(simulate(plan, pop).assignments)["categories"]
    any_ = summarize(simulate(plan, pop_any).assignments)["categories"]
    assert primary == {"流行": 7, "其他": 7}
    assert any_ == {"流行": 12, "其他": 2}  # R&B 那 5 首也带流行标签
    assert plan_store.load_plan(conn, plan.id) == plan


def test_genre_counts(plan: Plan) -> None:
    g = genre_counts(plan)
    assert g["level1"] == [
        {"name": "流行", "label": "流行", "count": 14},
        {"name": "R&B", "label": "R&B", "count": 7},
        {"name": "嘻哈说唱", "label": "嘻哈说唱", "count": 2},
    ]
    assert g["level2"] == [{"name": "流行-华语流行", "label": "华语流行", "count": 7}]


# ---------------------------------------------------------------- 恢复自动分类


def test_reset_restores_auto_assignment(conn: sqlite3.Connection, plan: Plan) -> None:
    move_songs(conn, plan.id, [k(13)], "流行")
    r = reset_songs(conn, plan.id, [k(13)])
    a = by_id(r.plan)["13"]
    assert a.status == "pending" and not a.manual  # type: ignore[attr-defined]
    assert r.plan.content_hash == plan.content_hash


def test_reset_keeps_other_manual_songs(conn: sqlite3.Connection, plan: Plan) -> None:
    move_songs(conn, plan.id, [k(1), k(2)], "R&B")
    r = reset_songs(conn, plan.id, [k(1)])
    assert not by_id(r.plan)["1"].manual  # type: ignore[attr-defined]
    assert by_id(r.plan)["2"].manual and by_id(r.plan)["2"].category == "R&B"  # type: ignore[attr-defined]


def test_reset_bad_input(conn: sqlite3.Connection, plan: Plan) -> None:
    with pytest.raises(MoveError, match="不在这份方案里"):
        reset_songs(conn, plan.id, [k(999)])
    with pytest.raises(MoveError, match="没有选中"):
        reset_songs(conn, plan.id, [])


def test_reset_rejected_when_applied(conn: sqlite3.Connection, plan: Plan) -> None:
    plan_store.transition(conn, plan.id, (PlanStatus.DRAFT,), PlanStatus.APPLIED)
    with pytest.raises(ReclassifyError):
        reset_songs(conn, plan.id, [k(1)])

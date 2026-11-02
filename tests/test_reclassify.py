"""按快照重新分类：不访问平台、保留人工调整、内容变化时批准作废、已执行不能改。"""

from __future__ import annotations

import inspect
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.apply.test_writer import build_library, writer
from tests.test_analyze import CFG, Clock
from tunetidy.analyze import ReclassifyError, analyze, classify_from_snapshot, reclassify
from tunetidy.classify.split import SplitSettings, only
from tunetidy.config import ConfigError
from tunetidy.plan import Plan, PlanStatus
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import connect

POP_ONLY = SplitSettings(groups=only(["流行"]))


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def plan(conn: sqlite3.Connection) -> Plan:
    b = build_library()
    return analyze(b, conn, CFG, [b.list_playlists()[0]], now=Clock())


def categories(p: Plan) -> dict[str, str | None]:
    return {a.key.id: a.category for a in p.assignments if a.status == "assigned"}


def test_takes_no_backend() -> None:
    """接口里根本没有后端参数：重新分类不可能访问平台。"""
    params = inspect.signature(reclassify).parameters
    assert list(params) == [
        "conn",
        "plan_id",
        "split",
        "min_size",
        "expected_hash",
        "confirm_reset",
    ]


def test_reclassify_draft(conn: sqlite3.Connection, plan: Plan) -> None:
    assert set(categories(plan).values()) == {"流行", "R&B"}
    r = reclassify(conn, plan.id, POP_ONLY)
    assert r.changed and not r.approval_reset
    assert set(categories(r.plan).values()) == {"流行", "其他"}
    assert r.plan.status == PlanStatus.DRAFT
    assert SplitSettings.from_dict(r.plan.settings["split"]) == POP_ONLY
    # 存进库里的结果就是按新设置从快照算出来的
    assert tuple(classify_from_snapshot(r.plan)) == r.plan.assignments
    assert r.plan.snapshot == plan.snapshot  # 快照不变


def test_back_to_level1_restores_original(conn: sqlite3.Connection, plan: Plan) -> None:
    reclassify(conn, plan.id, POP_ONLY)
    r = reclassify(conn, plan.id, SplitSettings())
    assert r.plan.content_hash == plan.content_hash
    assert categories(r.plan) == categories(plan)


def test_approval_reset_when_content_changes(conn: sqlite3.Connection, plan: Plan) -> None:
    plan_store.record_approval(conn, plan.id, plan.content_hash, "t")
    r = reclassify(conn, plan.id, POP_ONLY)
    assert r.approval_reset
    assert r.plan.status == PlanStatus.DRAFT
    assert r.plan.approved_hash is None and r.plan.approved_at is None


def test_approval_kept_when_content_unchanged(conn: sqlite3.Connection, plan: Plan) -> None:
    plan_store.record_approval(conn, plan.id, plan.content_hash, "t")
    r = reclassify(conn, plan.id, SplitSettings(top_n=5))  # 只有 2 类，前 5 类不改变内容
    assert not r.changed and not r.approval_reset
    assert r.plan.status == PlanStatus.APPROVED
    assert r.plan.approved_hash == r.plan.content_hash
    assert r.plan.settings["split"]["top_n"] == 5


def test_manual_adjustments_are_kept(conn: sqlite3.Connection, plan: Plan) -> None:
    # 模拟网页上的"移到"：把 1 号歌人工改到 R&B
    conn.execute(
        "UPDATE assignments SET category = 'R&B', manual = 1 WHERE plan_id = ? AND song_id = '1'",
        (plan.id,),
    )
    r = reclassify(conn, plan.id, POP_ONLY)
    assert r.kept_manual == 1
    by_id = {a.key.id: a for a in r.plan.assignments}
    assert by_id["1"].category == "R&B" and by_id["1"].manual
    assert by_id["2"].category == "流行" and not by_id["2"].manual


def test_min_size_override(conn: sqlite3.Connection, plan: Plan) -> None:
    r = reclassify(conn, plan.id, SplitSettings(), min_size=6)  # R&B 只有 5 首
    assert set(categories(r.plan).values()) == {"流行", "其他"}
    assert r.plan.settings["classify"]["min_size"] == 6
    with pytest.raises(ConfigError, match="min_size"):
        reclassify(conn, plan.id, SplitSettings(), min_size=0)


@pytest.mark.parametrize("status", ["applying", "applied", "undone"])
def test_executed_plans_cannot_be_reclassified(
    conn: sqlite3.Connection, plan: Plan, status: str
) -> None:
    conn.execute("UPDATE plans SET status = ? WHERE id = ?", (status, plan.id))
    with pytest.raises(ReclassifyError, match="不能再修改"):
        reclassify(conn, plan.id, POP_ONLY)


def test_really_applied_plan_is_refused(conn: sqlite3.Connection) -> None:
    b = build_library()
    plan = analyze(b, conn, CFG, [b.list_playlists()[0]], now=Clock())
    plan_store.record_approval(conn, plan.id, plan.content_hash, "t")
    writer(b, conn).execute(plan_store.load_plan(conn, plan.id))
    writes = len(b.writes)
    with pytest.raises(ReclassifyError):
        reclassify(conn, plan.id, POP_ONLY)
    assert len(b.writes) == writes


def test_concurrent_change_is_rejected(
    conn: sqlite3.Connection, plan: Plan, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重新分类算到一半，方案被别的进程批准或改动：放弃写入。"""
    import tunetidy.analyze as analyze_mod

    real = analyze_mod.classify_songs

    def classify_and_tamper(*a: object, **k: object) -> object:
        out = real(*a, **k)  # type: ignore[arg-type]
        conn.execute("UPDATE plans SET status = 'approved' WHERE id = ?", (plan.id,))
        return out

    monkeypatch.setattr(analyze_mod, "classify_songs", classify_and_tamper)
    with pytest.raises(plan_store.PlanStateError, match="重新分类期间"):
        reclassify(conn, plan.id, POP_ONLY)
    assert plan_store.load_plan(conn, plan.id).assignments == plan.assignments

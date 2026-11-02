"""建歌单前改歌单名：名字计入内容哈希，改名会使批准作废。"""

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
    default_name,
    reclassify,
    rename_playlist,
)
from tunetidy.apply.writer import dry_run_lines
from tunetidy.classify.split import SplitSettings, only
from tunetidy.plan import Plan, PlanStatus
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import connect


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def plan(conn: sqlite3.Connection) -> Plan:
    """流行 7、R&B 5。"""
    b = build_library()
    return analyze(b, conn, CFG, [b.list_playlists()[0]], now=Clock())


def names(p: Plan) -> dict[str, str]:
    return {pl.category: pl.name for pl in p.playlists()}


def test_rename_changes_playlist_name_and_hash(conn: sqlite3.Connection, plan: Plan) -> None:
    r = rename_playlist(conn, plan.id, "R&B", "深夜 R&B")
    assert names(r.plan)["R&B"] == "深夜 R&B"
    assert names(r.plan)["流行"] == names(plan)["流行"]
    assert r.changed and r.plan.content_hash != plan.content_hash
    assert plan_store.load_plan(conn, plan.id) == r.plan


def test_rename_blank_restores_default(conn: sqlite3.Connection, plan: Plan) -> None:
    rename_playlist(conn, plan.id, "R&B", "深夜 R&B")
    r = rename_playlist(conn, plan.id, "R&B", "  ")
    assert names(r.plan)["R&B"] == default_name(r.plan, "R&B")
    assert "names" not in r.plan.settings
    assert r.plan.content_hash == plan.content_hash


def test_rename_none_restores_default(conn: sqlite3.Connection, plan: Plan) -> None:
    rename_playlist(conn, plan.id, "R&B", "深夜 R&B")
    r = rename_playlist(conn, plan.id, "R&B", None)
    assert names(r.plan) == names(plan)


def test_rename_to_default_removes_override(conn: sqlite3.Connection, plan: Plan) -> None:
    r = rename_playlist(conn, plan.id, "R&B", default_name(plan, "R&B"))
    assert "names" not in r.plan.settings and not r.changed


def test_rename_trims_whitespace(conn: sqlite3.Connection, plan: Plan) -> None:
    r = rename_playlist(conn, plan.id, "R&B", "  深夜  ")
    assert names(r.plan)["R&B"] == "深夜"


def test_rename_length_limit(conn: sqlite3.Connection, plan: Plan) -> None:
    with pytest.raises(MoveError, match="歌单名最多 40 个字"):
        rename_playlist(conn, plan.id, "R&B", "长" * 41)
    r = rename_playlist(conn, plan.id, "R&B", "长" * 40)
    assert names(r.plan)["R&B"] == "长" * 40


def test_rename_rejects_duplicate(conn: sqlite3.Connection, plan: Plan) -> None:
    with pytest.raises(MoveError, match="已经有一个同名歌单了"):
        rename_playlist(conn, plan.id, "R&B", names(plan)["流行"])
    rename_playlist(conn, plan.id, "流行", "白天")
    with pytest.raises(MoveError, match="已经有一个同名歌单了"):
        rename_playlist(conn, plan.id, "R&B", "白天")


def test_rename_unknown_category(conn: sqlite3.Connection, plan: Plan) -> None:
    with pytest.raises(MoveError, match="没有这个歌单"):
        rename_playlist(conn, plan.id, "摇滚", "x")


def test_rename_survives_reclassify(conn: sqlite3.Connection, plan: Plan) -> None:
    rename_playlist(conn, plan.id, "R&B", "深夜 R&B")
    r = reclassify(conn, plan.id, SplitSettings(groups=only(["流行"])))
    assert "R&B" not in names(r.plan)  # 分类不在时，旧名字不生效也不报错
    r = reclassify(conn, plan.id, SplitSettings())
    assert names(r.plan)["R&B"] == "深夜 R&B"


def test_rename_resets_approval(conn: sqlite3.Connection, plan: Plan) -> None:
    plan_store.record_approval(conn, plan.id, plan.content_hash, "t")
    with pytest.raises(ConfirmResetError):
        rename_playlist(conn, plan.id, "R&B", "深夜", confirm_reset=False)
    r = rename_playlist(conn, plan.id, "R&B", "深夜", confirm_reset=True)
    assert r.approval_reset and r.plan.status == PlanStatus.DRAFT


def test_rename_rejected_when_applied(conn: sqlite3.Connection, plan: Plan) -> None:
    plan_store.transition(conn, plan.id, (PlanStatus.DRAFT,), PlanStatus.APPLIED)
    with pytest.raises(ReclassifyError):
        rename_playlist(conn, plan.id, "R&B", "深夜")


def test_apply_dry_run_uses_custom_name(conn: sqlite3.Connection, plan: Plan) -> None:
    r = rename_playlist(conn, plan.id, "R&B", "深夜 R&B")
    assert any("深夜 R&B" in line for line in dry_run_lines(r.plan, CFG.apply))

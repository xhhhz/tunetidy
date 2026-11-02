"""CLI：各命令可用；非交互终端下 approve / apply（含 --yes）/ undo 一律拒绝。全部用假后端。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.apply.test_writer import EXPECTED
from tests.cli.conftest import invoke, runner
from tests.fakes import FakeBackend
from tunetidy import cli
from tunetidy.apply import approval
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import connect


def make_plan(backend: FakeBackend) -> str:
    code, out = invoke("analyze", "我喜欢的音乐")
    assert code == 0, out
    m = re.search(r"已生成方案 (\d{8}-[0-9a-f]{4})", out)
    assert m
    return m.group(1)


def plan_status(db: Path, plan_id: str) -> str:
    conn = connect(db)
    try:
        return plan_store.load_plan(conn, plan_id).status.value
    finally:
        conn.close()


# ------------------------------------------------------------------ 只读命令


def test_playlists(backend: FakeBackend) -> None:
    code, out = invoke("playlists")
    assert code == 0 and "我喜欢的音乐" in out and "共 1 个" in out


def test_analyze_by_name_and_id(backend: FakeBackend) -> None:
    code, out = invoke("analyze", "我喜欢的音乐")
    assert code == 0
    assert "流行 · 整理自 我喜欢的音乐" in out and "待确认 3 首、无法加入 2 首" in out
    code, out = invoke("analyze", "100")
    assert code == 0 and "已生成方案" in out


def test_analyze_unknown_playlist(backend: FakeBackend) -> None:
    code, out = invoke("analyze", "不存在的歌单")
    assert code == 1 and "找不到歌单" in out and "Traceback" not in out


def test_export_stdout_and_file(backend: FakeBackend, tmp_path: Path) -> None:
    pid = make_plan(backend)
    code, out = invoke("export", pid, "--format", "md")
    assert code == 0 and "## 待确认（3 首，不写入）" in out
    target = tmp_path / "plan.csv"
    code, out = invoke("export", pid, "-f", "csv", "-o", str(target))
    assert code == 0 and target.read_text(encoding="utf-8").startswith("序号,")


def test_missing_plan_is_clean_error(backend: FakeBackend) -> None:
    code, out = invoke("export", "nope")
    assert code == 1 and "找不到方案" in out and "Traceback" not in out


def test_version() -> None:
    code, out = invoke("version")
    assert code == 0 and out.startswith("tunetidy ")


# ------------------------------------------------------------------ 非交互终端：一律拒绝


def test_dry_run_works_without_terminal(backend: FakeBackend) -> None:
    pid = make_plan(backend)
    code, out = invoke("apply", pid, "--dry-run")
    assert code == 0 and "[dry-run]" in out and "不会写入任何内容" in out
    assert backend.writes == []


def test_approve_refused_without_terminal(backend: FakeBackend, db: Path) -> None:
    pid = make_plan(backend)
    code, out = invoke("approve", pid, input="1234\n")
    assert code == 1 and "交互式终端" in out
    assert plan_status(db, pid) == "draft"


@pytest.mark.parametrize("extra", [["--yes"], []])
def test_apply_refused_without_terminal(backend: FakeBackend, db: Path, extra: list[str]) -> None:
    """非交互终端下 apply --yes 必须被拒绝；不带 --yes 也一样。"""
    pid = make_plan(backend)
    conn = connect(db)
    plan = plan_store.load_plan(conn, pid)
    plan_store.record_approval(conn, pid, plan.content_hash, "2026-10-30T12:00:00+00:00")
    conn.close()
    code, out = invoke("apply", pid, *extra)
    assert code == 1 and "交互式终端" in out
    assert backend.writes == []
    assert plan_status(db, pid) == "approved"


def test_undo_refused_without_terminal(
    backend: FakeBackend, tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = make_plan(backend)
    assert invoke("approve", pid, input="1234\n")[0] == 0
    assert invoke("apply", pid, "--yes")[0] == 0
    monkeypatch.setattr(approval, "_interactive", lambda: False)
    code, out = invoke("undo", pid, input="1234\n")
    assert code == 1 and "交互式终端" in out
    assert len(backend.owned_by_tool()) == 2


# ------------------------------------------------------------------ 交互式完整流程


@pytest.mark.usefixtures("tty")
def test_full_flow_approve_apply_undo(backend: FakeBackend, db: Path) -> None:
    pid = make_plan(backend)
    code, out = invoke("approve", pid, input="1234\n")
    assert code == 0 and "已批准方案" in out
    code, out = invoke("apply", pid, input="1234\n")
    assert code == 0, out
    assert "写入 7 首" in out and "写入 5 首" in out
    assert {p.info.name: [s.key.id for s in p.songs] for p in backend.owned_by_tool()} == EXPECTED
    assert plan_status(db, pid) == "applied"
    # 再执行一次：拒绝，不会生成第二批歌单
    code, out = invoke("apply", pid, "--yes")
    assert code == 1 and "已执行过" in out
    assert len(backend.owned_by_tool()) == 2
    # 用方案 id 撤销最近一次写回
    code, out = invoke("undo", pid, input="1234\n")
    assert code == 0 and out.count("已删除") == 2
    assert backend.owned_by_tool() == []
    assert plan_status(db, pid) == "undone"


@pytest.mark.usefixtures("tty")
def test_apply_on_draft_approves_first_with_single_code(backend: FakeBackend) -> None:
    pid = make_plan(backend)
    code, out = invoke("apply", pid, input="1234\n")  # 只输一次确认码
    assert code == 0, out
    assert "已批准方案" in out and len(backend.owned_by_tool()) == 2


@pytest.mark.usefixtures("tty")
def test_wrong_code_writes_nothing(backend: FakeBackend, db: Path) -> None:
    pid = make_plan(backend)
    assert invoke("approve", pid, input="1234\n")[0] == 0
    code, out = invoke("apply", pid, input="9999\n")
    assert code == 1 and "确认码不符" in out
    assert backend.writes == [] and plan_status(db, pid) == "approved"


@pytest.mark.usefixtures("tty")
def test_interrupted_apply_resumes(backend: FakeBackend, db: Path) -> None:
    pid = make_plan(backend)
    assert invoke("approve", pid, input="1234\n")[0] == 0
    backend.fail_after["add_tracks"] = {3}
    code, out = invoke("apply", pid, "--yes")
    assert code == 1 and "续传" in out and "Traceback" not in out
    assert plan_status(db, pid) == "applying"
    backend.fail_after["add_tracks"] = set()
    code, out = invoke("apply", pid, input="1234\n")
    assert code == 0 and "从中断处续传" in out, out
    assert {p.info.name: [s.key.id for s in p.songs] for p in backend.owned_by_tool()} == EXPECTED


@pytest.mark.usefixtures("tty")
def test_undo_unknown_and_never_applied(backend: FakeBackend) -> None:
    code, out = invoke("undo", "nope", input="1234\n")
    assert code == 1 and "找不到方案" in out
    pid = make_plan(backend)
    code, out = invoke("undo", pid, input="1234\n")
    assert code == 1 and "还没有写回过" in out


def test_resolve_sources_ambiguous_name() -> None:
    from tunetidy.apply.writer import ApplyError
    from tunetidy.models import PlaylistInfo

    pls = [
        PlaylistInfo("netease", "1", "同名", 1, True),
        PlaylistInfo("netease", "2", "同名", 1, True),
    ]
    with pytest.raises(ApplyError, match="请改用歌单 id"):
        cli.resolve_sources(pls, ["同名"])
    assert [p.id for p in cli.resolve_sources(pls, ["1", "1", "2"])] == ["1", "2"]


@pytest.mark.parametrize("width", [80, 48, 40])
def test_playlist_ids_never_truncated_in_narrow_terminal(width: int) -> None:
    from rich.console import Console

    from tunetidy.models import PlaylistInfo

    rows = [PlaylistInfo("netease", "14082862645", "爬坡-Kpop超强踩点健身BGM" * 2, 107, False)]
    table = cli.playlists_table(rows)
    table.add_row("1", "收藏", "107", rows[0].id, rows[0].name)
    console = Console(width=width, record=True, highlight=False)
    console.print(table)
    assert "14082862645" in console.export_text()


def _dead_job(db: Path) -> str:
    """一个进程早已不在的"进行中"任务（pid 取一个不存在的大数）。"""
    from datetime import UTC, datetime

    from tunetidy.storage import jobs

    conn = connect(db)
    try:
        return jobs.create_job(
            conn, "analyze", pid=2**22 + 7, now=datetime.now(UTC), alive=lambda _p: True
        ).id
    finally:
        conn.close()


def test_any_command_marks_orphan_jobs(backend: FakeBackend, db: Path) -> None:
    from tunetidy.storage import jobs

    job_id = _dead_job(db)
    assert runner.invoke(cli.app, ["playlists"]).exit_code == 0
    conn = connect(db)
    try:
        assert jobs.load_job(conn, job_id).status == "interrupted"
    finally:
        conn.close()

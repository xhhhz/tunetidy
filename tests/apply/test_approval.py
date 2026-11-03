"""批准：只能交互式 + 确认码；内容变了批准作废；已执行不能再执行；apply --yes 也要交互式。"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.test_analyze import CFG, Clock, library
from tunetidy.analyze import analyze
from tunetidy.apply import approval
from tunetidy.apply.approval import (
    ApprovalError,
    approval_is_valid,
    approve_interactive,
    check_executable,
    confirm_execution,
)
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
    b = library()
    return analyze(b, conn, CFG, b.list_playlists(), now=Clock())


@pytest.fixture
def tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approval, "is_interactive", lambda: True)


class Terminal:
    """记录输出；回答时从提示里读出确认码（或故意答错）。"""

    def __init__(self, correct: bool = True) -> None:
        self.out: list[str] = []
        self.correct = correct

    def say(self, line: str) -> None:
        self.out.append(line)

    def ask(self, prompt: str) -> str:
        self.out.append(prompt)
        code = re.search(r"输入 (\d{4})", prompt)
        assert code, prompt
        return code.group(1) if self.correct else "0000"


def stored(conn: sqlite3.Connection, plan_id: str) -> sqlite3.Row:
    row: sqlite3.Row = conn.execute(
        "SELECT status, approved_hash FROM plans WHERE id = ?", (plan_id,)
    ).fetchone()
    return row


# ------------------------------------------------------------------ 交互式要求


def test_pytest_streams_are_not_interactive() -> None:
    """真实判断：pytest 捕获输出时 stdin/stdout 不是终端。"""
    assert approval.is_interactive() is False


def test_approve_refused_without_terminal(conn: sqlite3.Connection, plan: Plan) -> None:
    t = Terminal()
    with pytest.raises(ApprovalError, match="交互式终端"):
        approve_interactive(conn, plan.id, t.ask, t.say)
    assert t.out == []  # 连预览都不显示
    assert stored(conn, plan.id)["status"] == "draft"


def test_apply_yes_refused_without_terminal(conn: sqlite3.Connection, plan: Plan) -> None:
    """--yes 不能绕过交互式终端的要求。"""
    with pytest.raises(ApprovalError, match="交互式终端"):
        confirm_execution(plan, yes=True)
    with pytest.raises(ApprovalError, match="交互式终端"):
        confirm_execution(plan, yes=False)


def test_no_bypass_via_environment(
    conn: sqlite3.Connection, plan: Plan, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("TUNETIDY_YES", "TUNETIDY_APPROVE", "CI", "TUNETIDY_NONINTERACTIVE"):
        monkeypatch.setenv(var, "1")
    with pytest.raises(ApprovalError):
        approve_interactive(conn, plan.id, Terminal().ask, Terminal().say)


# ------------------------------------------------------------------ 批准流程


@pytest.mark.usefixtures("tty")
def test_approve_with_correct_code(conn: sqlite3.Connection, plan: Plan) -> None:
    t = Terminal()
    approved = approve_interactive(conn, plan.id, t.ask, t.say)
    assert approved.status == PlanStatus.APPROVED
    assert approved.approved_hash == plan.content_hash
    assert approval_is_valid(approved)
    assert any("将新建 2 个私密歌单" in line for line in t.out)
    assert any("不会修改或删除你原有的任何歌单" in line for line in t.out)


@pytest.mark.usefixtures("tty")
def test_wrong_code_does_not_approve(conn: sqlite3.Connection, plan: Plan) -> None:
    with pytest.raises(ApprovalError, match="确认码不符"):
        approve_interactive(conn, plan.id, Terminal(correct=False).ask, Terminal().say)
    assert tuple(stored(conn, plan.id)) == ("draft", None)


@pytest.mark.usefixtures("tty")
def test_code_is_random(conn: sqlite3.Connection, plan: Plan) -> None:
    codes = {approval.new_code() for _ in range(50)}
    assert len(codes) > 10 and all(len(c) == 4 for c in codes)


@pytest.mark.usefixtures("tty")
def test_change_during_confirmation_is_rejected(conn: sqlite3.Connection, plan: Plan) -> None:
    """用户看预览时，方案被别的进程改了：批准必须针对看过的那份内容。"""
    t = Terminal()

    def ask_and_tamper(prompt: str) -> str:
        conn.execute(
            "UPDATE assignments SET category = '摇滚' WHERE plan_id = ? AND song_id = '1'",
            (plan.id,),
        )
        return t.ask(prompt)

    with pytest.raises(ApprovalError, match="发生了变化"):
        approve_interactive(conn, plan.id, ask_and_tamper, t.say)
    assert stored(conn, plan.id)["status"] == "draft"


@pytest.mark.usefixtures("tty")
def test_content_change_invalidates_approval(conn: sqlite3.Connection, plan: Plan) -> None:
    approve_interactive(conn, plan.id, Terminal().ask, Terminal().say)
    conn.execute(
        "UPDATE assignments SET category = '摇滚' WHERE plan_id = ? AND song_id = '1'", (plan.id,)
    )
    changed = plan_store.load_plan(conn, plan.id)
    assert changed.status == PlanStatus.APPROVED  # 状态字段没变……
    assert not approval_is_valid(changed)  # ……但批准已失效
    with pytest.raises(ApprovalError, match="批准已失效"):
        check_executable(changed)
    with pytest.raises(ApprovalError, match="批准已失效"):
        confirm_execution(changed, yes=True)
    # 重新批准后恢复有效
    again = approve_interactive(conn, plan.id, Terminal().ask, Terminal().say)
    assert approval_is_valid(again)
    assert again.approved_hash == changed.content_hash


@pytest.mark.usefixtures("tty")
def test_already_valid_approval_is_noop(conn: sqlite3.Connection, plan: Plan) -> None:
    approve_interactive(conn, plan.id, Terminal().ask, Terminal().say)
    t = Terminal()
    approve_interactive(conn, plan.id, t.ask, t.say)
    assert any("无需再次批准" in line for line in t.out)
    assert not any("输入" in line for line in t.out)


@pytest.mark.usefixtures("tty")
@pytest.mark.parametrize("status", ["applying", "applied", "undone"])
def test_cannot_approve_after_execution(conn: sqlite3.Connection, plan: Plan, status: str) -> None:
    conn.execute("UPDATE plans SET status = ? WHERE id = ?", (status, plan.id))
    with pytest.raises(ApprovalError, match="不能批准"):
        approve_interactive(conn, plan.id, Terminal().ask, Terminal().say)


# ------------------------------------------------------------------ 执行前检查


@pytest.mark.parametrize(
    ("status", "msg"),
    [("draft", "还没有批准"), ("applied", "已执行过"), ("undone", "已撤销"), ("applying", "续传")],
)
def test_check_executable_by_status(
    conn: sqlite3.Connection, plan: Plan, status: str, msg: str
) -> None:
    conn.execute(
        "UPDATE plans SET status = ?, approved_hash = ? WHERE id = ?",
        (status, plan.content_hash, plan.id),
    )
    with pytest.raises(ApprovalError, match=msg):
        check_executable(plan_store.load_plan(conn, plan.id))


@pytest.mark.usefixtures("tty")
def test_confirm_execution(conn: sqlite3.Connection, plan: Plan) -> None:
    approved = approve_interactive(conn, plan.id, Terminal().ask, Terminal().say)
    confirm_execution(approved, yes=True)  # --yes：跳过确认码
    t = Terminal()
    confirm_execution(approved, yes=False, ask=t.ask, say=t.say)
    assert any("开始写回请输入" in line for line in t.out)
    with pytest.raises(ApprovalError, match="确认码不符"):
        confirm_execution(approved, yes=False, ask=Terminal(correct=False).ask, say=t.say)


def test_transition_guards_concurrent_execution(conn: sqlite3.Connection, plan: Plan) -> None:
    conn.execute("UPDATE plans SET status = 'approved' WHERE id = ?", (plan.id,))
    plan_store.transition(conn, plan.id, (PlanStatus.APPROVED,), PlanStatus.APPLYING)
    with pytest.raises(plan_store.PlanStateError, match="applying"):
        plan_store.transition(conn, plan.id, (PlanStatus.APPROVED,), PlanStatus.APPLYING)
    with pytest.raises(plan_store.PlanNotFoundError):
        plan_store.transition(conn, "nope", (PlanStatus.APPROVED,), PlanStatus.APPLYING)

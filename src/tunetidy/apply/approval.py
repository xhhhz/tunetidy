"""批准与执行前检查。

- 批准只能由人在交互式终端完成：stdin 和 stdout 都必须是终端，并输入预览里显示的确认码。
- CLI 的 apply 无论是否带 --yes，都只能在交互式终端下执行；--yes 只跳过执行确认，不能代替批准。
- 非交互地执行已批准方案只走 MCP 的 apply_plan，不在这里。

是否交互式由本模块自己读取 sys.stdin / sys.stdout 判断，不接受调用方传入；
不提供任何绕过这一检查的参数、环境变量或代码路径。
"""

from __future__ import annotations

import secrets
import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, datetime

from tunetidy.plan import Plan, PlanStatus
from tunetidy.storage import plans as plan_store

Ask = Callable[[str], str]
Say = Callable[[str], None]


class ApprovalError(RuntimeError):
    pass


def _interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):  # 流被替换或已关闭
        return False


def require_interactive(action: str) -> None:
    if not _interactive():
        raise ApprovalError(
            f"{action}只能在交互式终端里由你本人完成。"
            "如果是 AI 助手在运行这个命令，请自己打开终端执行。"
        )


def approval_is_valid(plan: Plan) -> bool:
    """批准有效：状态为已批准，且批准时的哈希等于按当前内容重算的哈希。"""
    return plan.status == PlanStatus.APPROVED and plan.approved_hash == plan.content_hash


def check_executable(plan: Plan) -> None:
    """执行前检查。已执行、已撤销、执行中的方案不能再执行；未批准或批准失效也不行。"""
    if plan.status == PlanStatus.APPLIED:
        raise ApprovalError(f"方案 {plan.id} 已执行过，不能再次执行（避免生成第二批歌单）")
    if plan.status == PlanStatus.UNDONE:
        raise ApprovalError(f"方案 {plan.id} 已撤销，不能再次执行；请重新 analyze")
    if plan.status == PlanStatus.APPLYING:
        raise ApprovalError(f"方案 {plan.id} 正在执行或上次中断，请用续传，不要重新执行")
    if plan.status != PlanStatus.APPROVED:
        raise ApprovalError(f"方案 {plan.id} 还没有批准，请先运行 tunetidy approve {plan.id}")
    if not approval_is_valid(plan):
        raise ApprovalError(f"方案 {plan.id} 的内容在批准后发生了变化，批准已失效，请重新批准")


def preview_lines(plan: Plan) -> list[str]:
    playlists = plan.playlists()
    total = sum(len(p.songs) for p in playlists)
    lines = [
        f"方案 {plan.id}（来源：{'、'.join(s.name for s in plan.sources)}）",
        f"将新建 {len(playlists)} 个私密歌单，共写入 {total} 首：",
    ]
    lines += [f"  {p.name}  {len(p.songs)} 首" for p in playlists]
    pending, unavailable = len(plan.pending()), len(plan.unavailable())
    ungrouped = len(plan.ungrouped())
    extra = f"，未分组 {ungrouped} 首" if ungrouped else ""
    lines.append(f"不写入：待确认 {pending} 首，无法加入（下架或无版权）{unavailable} 首{extra}")
    lines.append("只会新建歌单，不会修改或删除你原有的任何歌单。")
    return lines


def new_code() -> str:
    return f"{secrets.randbelow(9000) + 1000}"


def approve_interactive(
    conn: sqlite3.Connection,
    plan_id: str,
    ask: Ask = input,
    say: Say = print,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Plan:
    require_interactive("批准")
    plan = plan_store.load_plan(conn, plan_id)
    if plan.status not in (PlanStatus.DRAFT, PlanStatus.APPROVED):
        raise ApprovalError(f"方案状态为 {plan.status.value}，不能批准")
    if approval_is_valid(plan):
        say(f"方案 {plan.id} 已经批准过，内容没有变化，无需再次批准。")
        return plan
    if not plan.playlists():
        raise ApprovalError("这个方案没有要新建的歌单，无需批准")
    for line in preview_lines(plan):
        say(line)
    code = new_code()
    answer = ask(f"确认批准请输入 {code}（其他输入取消）：").strip()
    if answer != code:
        raise ApprovalError("确认码不符，已取消，没有批准")
    try:
        plan_store.record_approval(
            conn, plan.id, plan.content_hash, now().isoformat(timespec="seconds")
        )
    except plan_store.PlanStateError as e:
        raise ApprovalError(str(e)) from e
    say(f"已批准方案 {plan.id}。执行：tunetidy apply {plan.id}")
    return plan_store.load_plan(conn, plan.id)


def confirm_execution(plan: Plan, yes: bool, ask: Ask = input, say: Say = print) -> None:
    """apply 执行前确认。无论 yes 与否都要求交互式终端；yes 只跳过确认码这一步。"""
    require_interactive("执行写回")
    check_executable(plan)
    if yes:
        return
    for line in preview_lines(plan):
        say(line)
    code = new_code()
    if ask(f"开始写回请输入 {code}（其他输入取消）：").strip() != code:
        raise ApprovalError("确认码不符，已取消，没有写入任何内容")

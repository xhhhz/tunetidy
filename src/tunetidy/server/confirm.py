"""网页确认码。

网页上的"确认并开始建""撤销"本质是本机接口，电脑上的 AI 助手可能拿到网址里的口令直接调用。
所以确认码走旁路：只在本进程内存里，只通过显示通道（启动理音的终端；安装包里是控制窗口）给人看，
不经过网页、接口、日志或数据库。网页只能申请码、提交码。

规则：6 位数字，2 分钟有效，最多输错 3 次；同一时间只有一个有效的码；一个码只对应
"哪个对象 + 什么动作 + 当时的内容指纹"，任何一项不符即作废；每 10 分钟最多申请 5 次；
连续作废 3 个"输错过"的码后锁定 10 分钟（重启理音也会解除）。
"""

from __future__ import annotations

import hmac
import secrets
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, TextIO

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from tunetidy.apply.undo import UndoPlan
from tunetidy.plan import Plan, source_label

Action = Literal["apply", "resume", "undo"]
ErrorKind = Literal["unavailable", "locked", "rate_limited", "expired", "wrong", "mismatch", "none"]

DIGITS = 6
TTL_SECONDS = 120
MAX_ATTEMPTS = 3
REQUEST_WINDOW = 600.0
MAX_REQUESTS = 5
LOCK_AFTER = 3
LOCK_SECONDS = 600.0
WARNING = "只在理音网页里输入，不要告诉任何人或 AI 助手"
IGNORE = "不是你点的？忽略即可"


class Channel(Protocol):
    def show(self, lines: list[str]) -> None: ...


class TerminalChannel:
    """把确认码打印在启动 tunetidy ui 的终端里。"""

    def __init__(self, file: TextIO | None = None) -> None:
        self.console = Console(file=file or sys.stdout, highlight=False, soft_wrap=True)

    def show(self, lines: list[str]) -> None:
        body = "\n".join(escape(line) for line in lines)
        self.console.print(Panel(body, title="理音 · 网页确认", border_style="yellow"))


class ConfirmError(Exception):
    """说明文字里从不包含确认码。"""

    def __init__(self, kind: ErrorKind, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


@dataclass(frozen=True)
class Ticket:
    """申请成功的回执（给网页）：不含码。"""

    expires_in: int
    attempts_left: int


@dataclass
class _Pending:
    target: str
    action: Action
    fingerprint: str
    code: str
    expires_at: float
    attempts_left: int = MAX_ATTEMPTS

    @property
    def guessed_wrong(self) -> bool:
        return self.attempts_left < MAX_ATTEMPTS

    def __repr__(self) -> str:  # 防止码出现在调试输出和异常回溯里
        return f"_Pending(target={self.target!r}, action={self.action!r})"


class ConfirmGate:
    def __init__(self, channel: Channel | None, now: Callable[[], float] = time.monotonic) -> None:
        self._channel = channel
        self._now = now
        self._lock = threading.Lock()
        self._pending: _Pending | None = None
        self._requests: list[float] = []
        self._burned = 0  # 连续作废的"输错过"的码
        self._locked_until: float | None = None

    @property
    def available(self) -> bool:
        return self._channel is not None

    def locked_until(self) -> float | None:
        with self._lock:
            return self._check_lock()

    def _check_lock(self) -> float | None:
        if self._locked_until is not None and self._now() >= self._locked_until:
            self._locked_until = None
        return self._locked_until

    def _raise_if_locked(self) -> None:
        until = self._check_lock()
        if until is not None:
            left = max(1, int(until - self._now() + 0.999))
            raise ConfirmError(
                "locked",
                f"试错太多次，网页确认已暂停，{(left + 59) // 60} 分钟后恢复；也可以在终端里确认",
                retry_after=left,
            )

    def _void(self) -> None:
        """当前码作废；输错过的码计入"连续作废"，到 3 个就锁定。"""
        pending, self._pending = self._pending, None
        if pending is not None and pending.guessed_wrong:
            self._burned += 1
            if self._burned >= LOCK_AFTER:
                self._burned = 0
                self._locked_until = self._now() + LOCK_SECONDS

    def request(self, target: str, action: Action, fingerprint: str, summary: list[str]) -> Ticket:
        if self._channel is None:
            raise ConfirmError(
                "unavailable", "网页确认不可用：理音不是在交互式终端里启动的，请在终端里确认"
            )
        with self._lock:
            self._raise_if_locked()
            now = self._now()
            self._requests = [t for t in self._requests if now - t < REQUEST_WINDOW]
            if len(self._requests) >= MAX_REQUESTS:
                retry = int(REQUEST_WINDOW - (now - self._requests[0])) + 1
                raise ConfirmError("rate_limited", "申请太频繁，请稍后再试", retry_after=retry)
            self._requests.append(now)
            self._void()  # 同一时间只有一个有效的码
            code = f"{secrets.randbelow(10**DIGITS):0{DIGITS}d}"
            self._pending = _Pending(target, action, fingerprint, code, now + TTL_SECONDS)
            lines = [
                *summary,
                "",
                f"确认码：{code}",
                f"{TTL_SECONDS // 60} 分钟内有效。{WARNING}。",
                IGNORE,
            ]
        self._channel.show(lines)
        return Ticket(expires_in=TTL_SECONDS, attempts_left=MAX_ATTEMPTS)

    def verify(self, target: str, action: Action, fingerprint: str, code: str) -> None:
        with self._lock:
            self._raise_if_locked()
            p = self._pending
            if p is None or p.target != target:
                raise ConfirmError("none", "没有待确认的请求，请重新点一次")
            if (p.target, p.action, p.fingerprint) != (target, action, fingerprint):
                self._void()
                raise ConfirmError(
                    "mismatch", "内容和申请确认码时不一样了，这个码已作废，请重新点一次"
                )
            if self._now() > p.expires_at:
                self._void()
                raise ConfirmError("expired", "确认码已过期，请重新点一次")
            if hmac.compare_digest(code.strip().encode(), p.code.encode()):
                self._pending = None
                self._burned = 0
                return
            p.attempts_left -= 1
            if p.attempts_left > 0:
                raise ConfirmError("wrong", f"不对，还能再试 {p.attempts_left} 次")
            self._void()
            self._raise_if_locked()
            raise ConfirmError("wrong", "试了 3 次都不对，这个码已作废，关掉弹窗重新点一次")


# ------------------------------------------------------------------ "要做什么"的说明

LIST_MAX = 8  # 最多逐个列出几个歌单


def _kind(private: bool) -> str:
    return "私密" if private else "公开"


def apply_summary(plan: Plan, private: bool) -> list[str]:
    playlists = plan.playlists()
    head = f"将新建 {len(playlists)} 个{_kind(private)}歌单，来源：{source_label(plan.sources)}"
    lines = [head, *(f"· {p.name}（{len(p.songs)} 首）" for p in playlists[:LIST_MAX])]
    if len(playlists) > LIST_MAX:
        lines.append(f"· 还有 {len(playlists) - LIST_MAX} 个")
    return lines


def resume_summary(plan: Plan, private: bool) -> list[str]:
    n = len(plan.playlists())
    return [f"继续建上次没建完的 {n} 个{_kind(private)}歌单，来源：{source_label(plan.sources)}"]


def _names(items: list[str]) -> str:
    shown = "、".join(items[:LIST_MAX])
    return shown + (f" 等 {len(items)} 个" if len(items) > LIST_MAX else "")


def undo_summary(up: UndoPlan, source: str) -> list[str]:
    run = up.run
    day = run.created_at[:10]
    try:
        d = datetime.fromisoformat(run.created_at)
        day = f"{d.month}月{d.day}日"
    except ValueError:
        pass
    deletes = [it.playlist.name for it in up.items if it.action == "delete"]
    trims = [it.playlist.name for it in up.items if it.action == "remove_ours"]
    skips = [it.playlist.name for it in up.items if it.action == "skip"]
    lines = [f"将撤销 {day} 从「{source}」建的歌单："]
    if deletes:
        lines.append(f"· 整个删除 {len(deletes)} 个歌单：{_names(deletes)}")
    if trims:
        lines.append(f"· 只移除理音加入的歌（歌单被你改过，保留）：{_names(trims)}")
    if skips:
        lines.append(f"· 跳过 {len(skips)} 个：{_names(skips)}")
    lines.append("你原来的歌单不会被碰")
    return lines

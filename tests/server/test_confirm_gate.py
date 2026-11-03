"""网页确认码：只在内存里、只走显示通道；作废、频率限制、锁定与自动解除。"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pytest

from tests.apply.test_undo import POP, RNB, applied, tool_playlist
from tests.apply.test_writer import build_library
from tests.classify.helpers import song
from tests.test_analyze import CFG
from tests.test_analyze import Clock as AnalyzeClock
from tunetidy.analyze import analyze
from tunetidy.apply import approval
from tunetidy.apply.undo import plan_undo
from tunetidy.server.confirm import (
    LOCK_SECONDS,
    TTL_SECONDS,
    ConfirmError,
    ConfirmGate,
    TerminalChannel,
    apply_summary,
    resume_summary,
    undo_summary,
)
from tunetidy.storage.db import connect

WARNING = "只在理音网页里输入，不要告诉任何人或 AI 助手"


class FakeChannel:
    def __init__(self) -> None:
        self.shown: list[list[str]] = []

    def show(self, lines: list[str]) -> None:
        self.shown.append(list(lines))

    @property
    def last_code(self) -> str:
        text = "\n".join(self.shown[-1])
        m = re.search(r"确认码[:：]\s*(\d{6})\b", text)
        assert m, text
        return m.group(1)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def channel() -> FakeChannel:
    return FakeChannel()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def gate(channel: FakeChannel, clock: Clock) -> ConfirmGate:
    return ConfirmGate(channel, now=clock)


SUMMARY = ["将新建 2 个私密歌单，来源：我喜欢的音乐"]


def ask(gate: ConfirmGate, target: str = "p1", fp: str = "h1") -> None:
    gate.request(target, "apply", fp, SUMMARY)


def kind_of(fn: object) -> str:
    try:
        fn()  # type: ignore[operator]
    except ConfirmError as e:
        return e.kind
    return "ok"


def test_code_is_six_digits_and_only_in_channel(gate: ConfirmGate, channel: FakeChannel) -> None:
    ticket = gate.request("p1", "apply", "h1", SUMMARY)
    code = channel.last_code
    assert re.fullmatch(r"\d{6}", code)
    assert code not in repr(ticket) and code not in str(ticket.__dict__)
    assert (ticket.expires_in, ticket.attempts_left) == (TTL_SECONDS, 3)
    text = "\n".join(channel.shown[-1])
    assert SUMMARY[0] in text and WARNING in text and "不是你点的？忽略即可" in text


def test_right_code_passes_once(gate: ConfirmGate, channel: FakeChannel) -> None:
    ask(gate)
    code = channel.last_code
    gate.verify("p1", "apply", "h1", code)
    assert kind_of(lambda: gate.verify("p1", "apply", "h1", code)) == "none"


def test_expires_after_two_minutes(gate: ConfirmGate, channel: FakeChannel, clock: Clock) -> None:
    ask(gate)
    clock.t += TTL_SECONDS + 1
    assert kind_of(lambda: gate.verify("p1", "apply", "h1", channel.last_code)) == "expired"


def test_three_wrong_attempts_burn_the_code(gate: ConfirmGate, channel: FakeChannel) -> None:
    ask(gate)
    code = channel.last_code
    wrong = "000000" if code != "000000" else "111111"
    for left in (2, 1):
        with pytest.raises(ConfirmError) as e:
            gate.verify("p1", "apply", "h1", wrong)
        assert e.value.kind == "wrong" and f"还能再试 {left} 次" in str(e.value)
        assert code not in str(e.value)
    with pytest.raises(ConfirmError) as e:
        gate.verify("p1", "apply", "h1", wrong)
    assert "已作废" in str(e.value)
    assert kind_of(lambda: gate.verify("p1", "apply", "h1", code)) == "none"


@pytest.mark.parametrize(("target", "action", "fp"), [("p1", "undo", "h1"), ("p1", "apply", "h2")])
def test_binding_mismatch_burns(
    gate: ConfirmGate, channel: FakeChannel, target: str, action: str, fp: str
) -> None:
    ask(gate)
    code = channel.last_code
    assert kind_of(lambda: gate.verify(target, action, fp, code)) == "mismatch"  # type: ignore[arg-type]
    assert kind_of(lambda: gate.verify("p1", "apply", "h1", code)) == "none"


def test_other_target_does_not_void_pending_code(gate: ConfirmGate, channel: FakeChannel) -> None:
    """拿码去确认别的整理：说没有待确认的请求，但不能让这次有效的码作废。"""
    ask(gate)
    code = channel.last_code
    assert kind_of(lambda: gate.verify("p2", "apply", "h1", code)) == "none"
    gate.verify("p1", "apply", "h1", code)


def test_new_request_voids_previous_code(gate: ConfirmGate, channel: FakeChannel) -> None:
    """同一时间只有一个有效的码：重新申请（哪怕是别的整理）会让旧码失效。"""
    ask(gate)
    old = channel.last_code
    ask(gate, target="p2")
    assert kind_of(lambda: gate.verify("p1", "apply", "h1", old)) == "none"
    gate.verify("p2", "apply", "h1", channel.last_code)


def test_rate_limit(gate: ConfirmGate, clock: Clock) -> None:
    for _ in range(5):
        ask(gate)
        clock.t += 10
    assert kind_of(lambda: ask(gate)) == "rate_limited"
    clock.t += 600
    ask(gate)


def burn_with_wrong_guesses(gate: ConfirmGate, channel: FakeChannel) -> None:
    ask(gate)
    wrong = "000000" if channel.last_code != "000000" else "111111"
    for _ in range(3):
        kind_of(lambda: gate.verify("p1", "apply", "h1", wrong))


def test_lock_after_three_burned_codes_and_auto_unlock(
    gate: ConfirmGate, channel: FakeChannel, clock: Clock
) -> None:
    for _ in range(3):
        burn_with_wrong_guesses(gate, channel)
    locked = gate.locked_until()
    assert locked is not None and locked == pytest.approx(clock.t + LOCK_SECONDS)
    assert kind_of(lambda: ask(gate)) == "locked"
    clock.t += LOCK_SECONDS - 1
    assert kind_of(lambda: ask(gate)) == "locked"
    clock.t += 2
    assert gate.locked_until() is None
    clock.t += 600  # 避开频率限制
    ask(gate)


def test_restart_clears_lock(channel: FakeChannel, clock: Clock) -> None:
    gate = ConfirmGate(channel, now=clock)
    for _ in range(3):
        burn_with_wrong_guesses(gate, channel)
    assert gate.locked_until() is not None
    fresh = ConfirmGate(channel, now=clock)  # 重启理音 = 新进程、新的 gate
    assert fresh.locked_until() is None
    ask(fresh)


def test_success_resets_burn_count(gate: ConfirmGate, channel: FakeChannel, clock: Clock) -> None:
    for _ in range(2):
        burn_with_wrong_guesses(gate, channel)
    ask(gate)
    gate.verify("p1", "apply", "h1", channel.last_code)
    clock.t += 600
    burn_with_wrong_guesses(gate, channel)
    assert gate.locked_until() is None


def test_expired_unused_code_does_not_count_towards_lock(
    gate: ConfirmGate, channel: FakeChannel, clock: Clock
) -> None:
    for _ in range(3):
        ask(gate)
        clock.t += TTL_SECONDS + 1
        kind_of(lambda: gate.verify("p1", "apply", "h1", channel.last_code))
    assert gate.locked_until() is None


def test_unavailable_without_channel(clock: Clock) -> None:
    gate = ConfirmGate(None, now=clock)
    assert not gate.available
    assert kind_of(lambda: ask(gate)) == "unavailable"


def test_terminal_channel_prints_warning_and_code() -> None:
    out = io.StringIO()
    TerminalChannel(file=out).show(["将新建 2 个私密歌单", "确认码：123456", WARNING])
    text = out.getvalue()
    assert "123456" in text and WARNING in text


@pytest.mark.parametrize(
    ("stdin", "stdout", "expected"),
    [(True, True, True), (True, False, False), (False, True, False)],
)
def test_is_interactive_needs_both_streams(
    monkeypatch: pytest.MonkeyPatch, stdin: bool, stdout: bool, expected: bool
) -> None:
    """网页确认是否可用，与 CLI approve 用同一个判断：标准输入和标准输出都是终端。"""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: stdout, raising=False)
    assert approval.is_interactive() is expected


# ---------------------------------------------------------------- 确认码旁边写明"要做什么"


def test_apply_and_resume_summary(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    b = build_library()
    plan = analyze(b, conn, CFG, [b.list_playlists()[0]], now=AnalyzeClock())
    lines = apply_summary(plan, private=True)
    assert lines[0] == "将新建 2 个私密歌单，来源：我喜欢的音乐"
    assert "· 流行 · 整理自 我喜欢的音乐（7 首）" in lines
    assert resume_summary(plan, private=True)[0] == (
        "继续建上次没建完的 2 个私密歌单，来源：我喜欢的音乐"
    )
    conn.close()


def test_undo_summary_names_deletes_and_trims(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    b, _plan_id, run_id = applied(conn)
    tool_playlist(b, RNB).songs.append(song(99))  # 用户往 R&B 歌单里加了一首
    lines = undo_summary(plan_undo(b, conn, run_id), "我喜欢的音乐")
    text = "\n".join(lines)
    assert "从「我喜欢的音乐」建的" in lines[0]
    assert f"整个删除 1 个歌单：{POP}" in text
    assert f"只移除理音加入的歌（歌单被你改过，保留）：{RNB}" in text
    assert "你原来的歌单不会被碰" in text
    conn.close()

"""网页扫码登录的内存状态：同一时间只有一个登录线程，状态随扫码推进。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest

from tunetidy.backends.base import BackendError, LoginExpiredError
from tunetidy.models import User
from tunetidy.server.login import LoginFlow, LoginState


class ScriptedLogin:
    """假后端的 login：先给二维码，再按测试的指令推进到已扫码、成功或出错。"""

    def __init__(self) -> None:
        self.calls = 0
        self.scan = threading.Event()
        self.finish = threading.Event()
        self.error: BaseException | None = None

    def login(
        self, show_qr: Callable[[str], None], on_scanned: Callable[[], None] | None = None
    ) -> User:
        self.calls += 1
        show_qr(f"https://example.invalid/qr?n={self.calls}")
        if self.scan.wait(5) and on_scanned is not None:
            on_scanned()
        self.finish.wait(5)
        if self.error is not None:
            raise self.error
        return User("netease", "1", "小明")


def wait_for(flow: LoginFlow, status: str) -> LoginState:
    for _ in range(500):
        s = flow.state()
        if s.status == status:
            return s
        time.sleep(0.01)
    raise AssertionError(f"状态一直没有变成 {status}，现在是 {flow.state().status}")


@pytest.fixture
def script() -> ScriptedLogin:
    return ScriptedLogin()


@pytest.fixture
def flow(script: ScriptedLogin) -> LoginFlow:
    return LoginFlow(lambda: script)  # type: ignore[arg-type, return-value]


def test_idle_before_start(flow: LoginFlow) -> None:
    assert flow.state().status == "idle"
    assert flow.state().qr is None


def test_start_returns_qr_matrix(flow: LoginFlow) -> None:
    s = flow.start()
    assert s.status == "waiting"
    assert s.qr and len(s.qr) == len(s.qr[0])
    assert set("".join(s.qr)) <= {"0", "1"}


def test_second_start_reuses_the_same_login(flow: LoginFlow, script: ScriptedLogin) -> None:
    first = flow.start()
    second = flow.start()
    assert second.qr == first.qr
    assert script.calls == 1


def test_scanned_then_done(flow: LoginFlow, script: ScriptedLogin) -> None:
    flow.start()
    script.scan.set()
    wait_for(flow, "scanned")
    assert flow.start().status == "scanned"  # 已扫码时再点开始，仍是同一次登录
    script.finish.set()
    s = wait_for(flow, "done")
    assert s.nickname == "小明"
    assert script.calls == 1


def test_expired_then_restart_gives_new_qr(flow: LoginFlow, script: ScriptedLogin) -> None:
    first = flow.start()
    script.error = LoginExpiredError("二维码已过期，请重新登录")
    script.scan.set()
    script.finish.set()
    wait_for(flow, "expired")
    script.error = None
    script.scan.clear()
    script.finish.clear()
    second = flow.start()
    assert script.calls == 2
    assert second.status == "waiting"
    assert second.qr != first.qr
    script.scan.set()
    script.finish.set()
    wait_for(flow, "done")


def test_backend_error_is_shown(flow: LoginFlow, script: ScriptedLogin) -> None:
    flow.start()
    script.error = BackendError("登录失败：code=8821 需要行为验证码")
    script.scan.set()
    script.finish.set()
    s = wait_for(flow, "failed")
    assert s.error == "登录失败：code=8821 需要行为验证码"


def test_unexpected_error_hides_details(flow: LoginFlow, script: ScriptedLogin) -> None:
    flow.start()
    script.error = RuntimeError("cookie=secret")
    script.scan.set()
    script.finish.set()
    s = wait_for(flow, "failed")
    assert s.error and "secret" not in s.error
    assert "RuntimeError" in s.error


def test_error_before_qr_does_not_hang(script: ScriptedLogin) -> None:
    class NoQr:
        def login(self, show_qr: Callable[[str], None], on_scanned: object = None) -> User:
            raise BackendError("获取登录二维码失败：code=-460")

    flow = LoginFlow(lambda: NoQr())  # type: ignore[arg-type, return-value]
    t0 = time.monotonic()
    s = flow.start()
    assert time.monotonic() - t0 < 2
    assert s.status == "failed"
    assert s.error == "获取登录二维码失败：code=-460"

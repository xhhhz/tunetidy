"""网页扫码登录：在 tunetidy ui 进程里开一个线程跑 backend.login()，状态只放在内存里。

网页每秒读一次状态。同一时间只有一个登录线程：正在等扫码时再点"开始"，拿到的是同一个
二维码，避免两个线程抢着写登录态。登录态照旧由后端存进钥匙串，这里只记昵称，不碰 Cookie。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

import qrcode

from tunetidy.backends.base import BackendError, LoginExpiredError, MusicBackend

LoginStatus = Literal["idle", "waiting", "scanned", "done", "expired", "failed"]
QR_WAIT = 10.0  # 秒；开始登录后最多等这么久拿到二维码


@dataclass(frozen=True)
class LoginState:
    status: LoginStatus
    qr: tuple[str, ...] | None = None  # 二维码点阵，每行由 0 和 1 组成，1 是黑格
    nickname: str | None = None
    error: str | None = None


def qr_matrix(content: str) -> tuple[str, ...]:
    q = qrcode.QRCode(border=0)
    q.add_data(content)
    return tuple("".join("1" if cell else "0" for cell in row) for row in q.get_matrix())


class LoginFlow:
    def __init__(self, backend_factory: Callable[[], MusicBackend]) -> None:
        self._factory = backend_factory
        self._lock = threading.Lock()
        self._state = LoginState("idle")
        self._thread: threading.Thread | None = None

    def state(self) -> LoginState:
        with self._lock:
            return self._state

    def _set(self, state: LoginState) -> None:
        with self._lock:
            self._state = state

    def start(self) -> LoginState:
        """开始一次扫码登录，等拿到二维码（或出错）后返回；已在等扫码时返回当前这次。"""
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            if running and self._state.status in ("waiting", "scanned"):
                return self._state
            self._state = LoginState("idle")
            ready = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
            self._thread.start()
        ready.wait(QR_WAIT)
        return self.state()

    def _run(self, ready: threading.Event) -> None:
        def show_qr(url: str) -> None:
            self._set(LoginState("waiting", qr=qr_matrix(url)))
            ready.set()

        def on_scanned() -> None:
            with self._lock:
                self._state = replace(self._state, status="scanned")

        try:
            user = self._factory().login(show_qr, on_scanned)
            self._set(LoginState("done", nickname=user.nickname))
        except LoginExpiredError as e:
            self._set(LoginState("expired", error=str(e)))
        except BackendError as e:  # 后端的说明文字已脱敏
            self._set(LoginState("failed", error=str(e)))
        except Exception as e:  # noqa: BLE001 — 其他错误只报类型，细节可能含凭据
            self._set(LoginState("failed", error=f"登录时出现意外错误（{type(e).__name__}）"))
        finally:
            ready.set()

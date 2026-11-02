"""网易云 HTTP 客户端：限速、直连、Cookie 处理、网络错误重试。

- 默认不继承系统代理（trust_env=False）；需要代理时由配置显式指定。
- 每个请求前等待 [min_interval, max_interval] 的随机间隔；不要为了测试快而去掉，
  测试里注入假的 sleep。
- 登录成功时同名 Cookie 会按不同 Path 下发多份，直接遍历 cookie jar，优先取 Path=/。
- 错误信息不包含 Cookie 或请求体。
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from tunetidy.backends.base import BackendError
from tunetidy.backends.web import crypto
from tunetidy.backends.web.session import Session
from tunetidy.config import NetworkConfig

WEB_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
PC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Safari/537.36 Chrome/91.0.4472.164 NeteaseMusicDesktop/3.1.29.205117"
)
PC_HEADER = {
    "os": "pc",
    "appver": "3.1.29.205117",
    "osver": "Microsoft-Windows-10-Professional-build-19045-64bit",
    "channel": "netease",
}
WEB_BASE = "https://music.163.com"
EAPI_BASE = "https://interfacepc.music.163.com"
API_BASE = "https://interface.music.163.com"
RETRY_BACKOFF = (2.0, 5.0)  # 网络错误重试前的等待（秒）


class Client:
    def __init__(
        self,
        session: Session,
        network: NetworkConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.session = session
        self.network = network
        self._sleep, self._clock = sleep, clock
        self._rng = rng or random.Random()
        self._last: float | None = None
        self.http = httpx.Client(
            timeout=network.timeout,
            follow_redirects=False,
            trust_env=False,
            proxy=network.proxy,
            transport=transport,
        )

    def close(self) -> None:
        self.http.close()

    # ------------------------------------------------------------ 内部

    def _wait(self) -> None:
        gap = self._rng.uniform(self.network.min_interval, self.network.max_interval)
        if self._last is not None:
            delay = self._last + gap - self._clock()
            if delay > 0:
                self._sleep(delay)
        self._last = self._clock()

    def _absorb(self, resp: httpx.Response) -> None:
        seen_root: set[str] = set()
        for ck in resp.cookies.jar:
            if ck.value is None or ck.name in seen_root:
                continue
            self.session.cookies[ck.name] = ck.value
            if ck.path == "/":
                seen_root.add(ck.name)
        # 登录态只放在 Session 里、由各请求显式带上；清空 httpx 自带的 jar，
        # 否则不带 Cookie 头的匿名请求会被自动附上 MUSIC_U
        self.http.cookies.clear()

    def _send(self, build: Callable[[], httpx.Request]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(len(RETRY_BACKOFF) + 1):
            if attempt:
                self._sleep(RETRY_BACKOFF[attempt - 1])
            self._wait()
            try:
                resp = self.http.send(build())
            except httpx.HTTPError as e:
                last_error = e
                continue
            self._absorb(resp)
            try:
                body = resp.json()
            except ValueError as e:
                raise BackendError(f"网易云返回了无法解析的内容（HTTP {resp.status_code}）") from e
            if not isinstance(body, dict):
                raise BackendError(f"网易云返回了意外的结构（HTTP {resp.status_code}）")
            return body
        raise BackendError(f"网络错误：{type(last_error).__name__}，已重试 {len(RETRY_BACKOFF)} 次")

    def _cookie_header(self, values: dict[str, str]) -> str:
        return "; ".join(f"{k}={v}" for k, v in values.items())

    # ------------------------------------------------------------ 三种调用方式；path 写成 /api/...

    def weapi(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {**(data or {}), "csrf_token": self.session.cookies.get("__csrf", "")}
        cookies = {
            **self.session.cookies,
            "os": "pc",
            "appver": PC_HEADER["appver"],
            "deviceId": self.session.device_id,
        }

        def build() -> httpx.Request:
            return self.http.build_request(
                "POST",
                f"{WEB_BASE}/weapi/{path.removeprefix('/api/')}",
                data=crypto.weapi(payload),
                headers={
                    "User-Agent": WEB_UA,
                    "Referer": WEB_BASE,
                    "Cookie": self._cookie_header(cookies),
                },
            )

        return self._send(build)

    def eapi(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        def build() -> httpx.Request:
            header = {
                **PC_HEADER,
                "deviceId": self.session.device_id,
                "versioncode": "140",
                "buildver": str(int(time.time())),
                "resolution": "1920x1080",
                "__csrf": self.session.cookies.get("__csrf", ""),
                "requestId": f"{int(time.time() * 1000)}_{self._rng.randint(0, 999):04d}",
            }
            for k in ("MUSIC_U", "MUSIC_A", "NMTID"):
                if k in self.session.cookies:
                    header[k] = self.session.cookies[k]
            payload = {**(data or {}), "header": header, "e_r": False}
            return self.http.build_request(
                "POST",
                f"{EAPI_BASE}/eapi/{path.removeprefix('/api/')}",
                data=crypto.eapi(path, payload),
                headers={"User-Agent": PC_UA, "Cookie": self._cookie_header(header)},
            )

        return self._send(build)

    def anonymous_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """明文匿名 GET（歌曲百科）。不带登录态，避免把账号暴露给高频接口。"""

        def build() -> httpx.Request:
            return self.http.build_request(
                "GET", f"{API_BASE}{path}", params=params, headers={"User-Agent": PC_UA}
            )

        return self._send(build)

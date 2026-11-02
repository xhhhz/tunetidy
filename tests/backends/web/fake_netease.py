"""离线的假网易云服务器，挂在 httpx.MockTransport 上。

eapi 请求用真实算法解密；weapi 用随机密钥 + RSA，无法解密，测试里把 crypto.weapi
替换成明文编码（见 plain_weapi）。
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from tunetidy.backends.web import crypto

Handler = Callable[[dict[str, Any]], dict[str, Any] | httpx.Response]


def plain_weapi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        crypto,
        "weapi",
        lambda data, secret_key=None: {"plain": json.dumps(data, ensure_ascii=False)},
    )


@dataclass
class Call:
    kind: str
    path: str
    data: dict[str, Any]
    cookie: str | None


@dataclass
class FakeNetease:
    routes: dict[str, Handler] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    set_cookies: dict[str, list[str]] = field(default_factory=dict)  # 路径 → Set-Cookie 头

    def route(self, path: str, handler: Handler | dict[str, Any]) -> None:
        self.routes[path] = handler if callable(handler) else (lambda _d, h=handler: h)

    def _decode(self, req: httpx.Request) -> tuple[str, str, dict[str, Any]]:
        if req.method == "GET":
            return "get", req.url.path, dict(req.url.params)
        form = urllib.parse.parse_qs(req.content.decode())
        if "/eapi/" in req.url.path:
            path, data = crypto.eapi_decrypt_request(form["params"][0])
            return "eapi", path, data
        return "weapi", "/api/" + req.url.path.split("/weapi/", 1)[1], json.loads(form["plain"][0])

    def handler(self, req: httpx.Request) -> httpx.Response:
        kind, path, data = self._decode(req)
        self.calls.append(Call(kind, path, data, req.headers.get("cookie")))
        if path not in self.routes:
            return httpx.Response(200, json={"code": 404, "message": f"no route {path}"})
        out = self.routes[path](data)
        if isinstance(out, httpx.Response):
            return out
        headers = [("Set-Cookie", c) for c in self.set_cookies.pop(path, [])]
        return httpx.Response(200, json=out, headers=headers)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def paths(self) -> list[str]:
        return [c.path for c in self.calls]


class FakeClock:
    """sleep 推进时间；每个请求本身不耗时。"""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now

"""本地服务安全：token、Host、跨站请求、只接受 POST 的写接口。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tunetidy.server.app import TOKEN_HEADER, create_app, new_token

PORT = 8765
TOKEN = "t" * 43
BASE = f"http://127.0.0.1:{PORT}"
OK = {TOKEN_HEADER: TOKEN}


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    a = create_app(tmp_path / "t.db", TOKEN, PORT)
    a.add_api_route("/api/probe", lambda: {"ok": True}, methods=["GET"])
    a.add_api_route("/api/probe-write", lambda: {"ok": True}, methods=["POST"])
    a.add_api_route("/page", lambda: {"page": True}, methods=["GET"])
    return a


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url=BASE)


def test_valid_token(client: TestClient) -> None:
    assert client.get("/api/probe", headers=OK).json() == {"ok": True}
    assert client.post("/api/probe-write", headers=OK).status_code == 200


@pytest.mark.parametrize("headers", [{}, {TOKEN_HEADER: ""}, {TOKEN_HEADER: TOKEN + "x"}])
def test_missing_or_wrong_token(client: TestClient, headers: dict[str, str]) -> None:
    for r in (client.get("/api/probe", headers=headers), client.post("/api/probe-write")):
        assert r.status_code == 401
        assert "口令" in r.json()["detail"]


def test_token_only_accepted_in_header(client: TestClient) -> None:
    """放在网址参数或 Cookie 里都不算：跨站页面能带上它们，却加不了自定义头。"""
    assert client.get(f"/api/probe?token={TOKEN}").status_code == 401
    client.cookies.set(TOKEN_HEADER, TOKEN)
    assert client.get("/api/probe").status_code == 401


@pytest.mark.parametrize(
    "host", ["evil.example", f"evil.example:{PORT}", "127.0.0.1:9999", "127.0.0.1", "0.0.0.0:8765"]
)
def test_foreign_host_rejected(client: TestClient, host: str) -> None:
    """DNS 重绑定：恶意域名解析到 127.0.0.1 后，请求里的 Host 仍是恶意域名。"""
    for path in ("/api/probe", "/page"):
        r = client.get(path, headers={**OK, "Host": host})
        assert r.status_code == 403


def test_localhost_accepted(client: TestClient) -> None:
    r = client.get("/api/probe", headers={**OK, "Host": f"localhost:{PORT}"})
    assert r.status_code == 200


@pytest.mark.parametrize(
    "origin",
    ["http://evil.example", f"http://127.0.0.1:{PORT + 1}", "null", f"https://127.0.0.1:{PORT}"],
)
def test_cross_origin_rejected(client: TestClient, origin: str) -> None:
    for r in (
        client.get("/api/probe", headers={**OK, "Origin": origin}),
        client.post("/api/probe-write", headers={**OK, "Origin": origin}),
        client.options(
            "/api/probe-write",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": TOKEN_HEADER,
            },
        ),
    ):
        assert r.status_code == 403
        assert "access-control-allow-origin" not in r.headers


def test_same_origin_accepted(client: TestClient) -> None:
    for origin in (BASE, f"http://localhost:{PORT}"):
        r = client.post("/api/probe-write", headers={**OK, "Origin": origin})
        assert r.status_code == 200
        assert "access-control-allow-origin" not in r.headers  # 同源不需要 CORS 头


def test_write_endpoint_rejects_get(client: TestClient) -> None:
    assert client.get("/api/probe-write", headers=OK).status_code == 405


def test_no_unauthenticated_docs(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_api_routes_are_get_or_post(app: FastAPI) -> None:
    """接口只有读（GET）和写（POST）两种，没有 PUT/DELETE 等绕过"写只走 POST"的口子。"""
    for route in app.routes:
        if isinstance(route, APIRoute):
            assert route.methods <= {"GET", "POST"}, route.path
            assert len(route.methods) == 1, route.path


def test_tokens_are_random() -> None:
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 40

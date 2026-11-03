"""网页登录与账号状态的接口。"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from tests.fakes import FakeBackend
from tests.server.conftest import BASE, PORT, TOKEN
from tests.test_jobs import CONFIG
from tunetidy.models import User
from tunetidy.server.app import TOKEN_HEADER, create_app
from tunetidy.storage import jobs as job_store
from tunetidy.storage.db import connect

SECRET = "MUSIC_U=secret-cookie-value"


class CookieBackend(FakeBackend):
    """登录后"存下"一个 Cookie，用来检查它不会出现在任何接口响应里。"""

    cookie: str | None = None

    def login(
        self, show_qr: Callable[[str], None], on_scanned: Callable[[], None] | None = None
    ) -> User:
        user = super().login(show_qr, on_scanned)
        self.cookie = SECRET
        return user


def make_client(db_path: Path, backend: FakeBackend) -> TestClient:
    app = create_app(db_path, TOKEN, PORT, CONFIG, backend_factory=lambda: backend)
    return TestClient(app, base_url=BASE, headers={TOKEN_HEADER: TOKEN})


def test_me_logged_in(db_path: Path, backend: FakeBackend) -> None:
    r = make_client(db_path, backend).get("/api/me")
    assert r.json() == {"logged_in": True, "nickname": "测试用户"}


def test_me_logged_out(db_path: Path, backend: FakeBackend) -> None:
    backend.user = None
    r = make_client(db_path, backend).get("/api/me")
    assert r.json() == {"logged_in": False, "nickname": None}


def test_login_flow_over_api_never_leaks_cookie(db_path: Path) -> None:
    backend = CookieBackend(style_tree=[])
    client = make_client(db_path, backend)
    texts = []
    r = client.post("/api/login/start", json={})
    assert r.status_code == 200
    texts.append(r.text)
    assert r.json()["status"] in ("waiting", "done")
    for _ in range(200):
        r = client.get("/api/login")
        texts.append(r.text)
        if r.json()["status"] == "done":
            break
        time.sleep(0.01)
    assert r.json()["nickname"] == "测试用户"
    texts.append(client.get("/api/me").text)
    assert backend.cookie == SECRET
    assert all("secret-cookie" not in t for t in texts)


def test_login_start_requires_token_and_post(db_path: Path, backend: FakeBackend) -> None:
    client = make_client(db_path, backend)
    assert client.get("/api/login/start").status_code == 405
    no_token = TestClient(client.app, base_url=BASE)
    assert no_token.post("/api/login/start", json={}).status_code == 401
    assert no_token.get("/api/login").status_code == 401
    assert no_token.get("/api/me").status_code == 401


def test_idle_login_state(db_path: Path, backend: FakeBackend) -> None:
    r = make_client(db_path, backend).get("/api/login")
    assert r.json() == {"status": "idle", "qr": None, "nickname": None, "error": None}


def test_not_logged_in_points_to_web_login(db_path: Path, backend: FakeBackend) -> None:
    backend.user = None
    r = make_client(db_path, backend).get("/api/runs/x/undo")
    assert r.status_code == 409
    assert r.json()["code"] == "not_logged_in"
    assert "终端" not in r.json()["detail"]


def test_logout_clears_login(db_path: Path, backend: FakeBackend) -> None:
    client = make_client(db_path, backend)
    r = client.post("/api/logout", json={})
    assert r.status_code == 200
    assert client.get("/api/me").json() == {"logged_in": False, "nickname": None}


def test_logout_refused_while_a_job_runs(db_path: Path, backend: FakeBackend) -> None:
    """后台任务手里还拿着登录态，这时退出会被它写回钥匙串，所以等任务结束再退出。"""
    client = make_client(db_path, backend)
    conn = connect(db_path)
    job_store.create_job(conn, "apply", plan_id="x", pid=os.getpid(), now=datetime.now(UTC))
    conn.close()
    r = client.post("/api/logout", json={})
    assert r.status_code == 409
    assert r.json()["code"] == "busy"
    assert client.get("/api/me").json()["logged_in"] is True


def test_logout_requires_token_and_post(db_path: Path, backend: FakeBackend) -> None:
    client = make_client(db_path, backend)
    assert client.get("/api/logout").status_code == 405
    assert client.post("/api/logout", json={}, headers={TOKEN_HEADER: "wrong"}).status_code == 401
    assert client.get("/api/me").json()["logged_in"] is True

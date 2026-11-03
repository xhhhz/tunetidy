"""服务端测试共用：用假后端生成一份方案，再用 TestClient 访问本地服务。"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.apply.test_writer import build_library
from tests.fakes import FakeBackend
from tests.server.test_confirm_gate import FakeChannel
from tests.test_analyze import CFG, Clock
from tests.test_jobs import CONFIG
from tunetidy.analyze import analyze
from tunetidy.plan import Plan
from tunetidy.server.app import TOKEN_HEADER, create_app
from tunetidy.storage.db import connect

PORT = 8765
TOKEN = "t" * 43
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(db_path)
    yield c
    c.close()


@pytest.fixture
def backend() -> FakeBackend:
    return build_library()


@pytest.fixture
def plan(conn: sqlite3.Connection, backend: FakeBackend) -> Plan:
    """流行 7、R&B 5、待确认 3（多曲风 2 + 云盘 1）、无法加入 2。"""
    return analyze(backend, conn, CFG, [backend.list_playlists()[0]], now=Clock())


@pytest.fixture
def client(db_path: Path) -> TestClient:
    app = create_app(db_path, TOKEN, PORT, CFG)
    return TestClient(app, base_url=BASE, headers={TOKEN_HEADER: TOKEN})


class Env:
    """一套测试环境：假后端、假显示通道、本地服务客户端，以及记下的所有接口响应。"""

    def __init__(self, db_path: Path, backend: FakeBackend, channel: FakeChannel | None) -> None:
        self.db_path, self.backend, self.channel = db_path, backend, channel
        app = create_app(
            db_path, TOKEN, PORT, CONFIG, backend_factory=lambda: backend, channel=channel
        )
        self.client = TestClient(app, base_url=BASE, headers={TOKEN_HEADER: TOKEN})
        self.responses: list[str] = []

    def call(self, method: str, path: str, **kw: Any) -> Any:
        r = self.client.request(method, path, **kw)
        self.responses.append(r.text + "\n" + "\n".join(f"{k}: {v}" for k, v in r.headers.items()))
        return r

    def wait(self, job_id: str) -> dict[str, Any]:
        for _ in range(500):
            s = self.call("GET", f"/api/jobs/{job_id}").json()
            if s["status"] != "running":
                return s
            time.sleep(0.01)
        raise AssertionError("任务没有结束")

    @property
    def code(self) -> str:
        assert self.channel is not None
        return self.channel.last_code


@pytest.fixture
def env(db_path: Path, backend: FakeBackend) -> Env:
    """确认与写回测试的环境：假后端、假显示通道、记下所有接口响应。"""
    return Env(db_path, backend, FakeChannel())

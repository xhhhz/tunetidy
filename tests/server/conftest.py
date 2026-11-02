"""服务端测试共用：用假后端生成一份方案，再用 TestClient 访问本地服务。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.apply.test_writer import build_library
from tests.test_analyze import CFG, Clock
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
def plan(conn: sqlite3.Connection) -> Plan:
    """流行 7、R&B 5、待确认 3（多曲风 2 + 云盘 1）、无法加入 2。"""
    b = build_library()
    return analyze(b, conn, CFG, [b.list_playlists()[0]], now=Clock())


@pytest.fixture
def client(db_path: Path) -> TestClient:
    app = create_app(db_path, TOKEN, PORT, CFG)
    return TestClient(app, base_url=BASE, headers={TOKEN_HEADER: TOKEN})

"""前端静态文件与 tunetidy ui：没有构建产物时报错并给出构建命令。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.cli.conftest import invoke
from tests.server.conftest import BASE, PORT, TOKEN
from tests.test_analyze import CFG
from tunetidy.server import app as app_mod
from tunetidy.server.app import TOKEN_HEADER, MissingBuildError, create_app, find_static


@pytest.fixture
def static(tmp_path: Path) -> Path:
    d = tmp_path / "static"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>曲整</title>", encoding="utf-8")
    (d / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return d


def test_missing_build_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(MissingBuildError, match="npm run build"):
        find_static(tmp_path / "nothing")
    with pytest.raises(MissingBuildError):
        create_app(tmp_path / "t.db", TOKEN, PORT, static_dir=tmp_path / "nothing")


def test_serves_frontend(tmp_path: Path, static: Path) -> None:
    c = TestClient(create_app(tmp_path / "t.db", TOKEN, PORT, static_dir=static), base_url=BASE)
    for path in ("/", "/plans/20261030-abcd", "/plans/x/preview"):
        r = c.get(path)
        assert r.status_code == 200 and "曲整" in r.text
        assert r.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert c.get("/assets/app.js").text == "console.log(1)"
    # 页面不需要口令，接口仍然需要；不存在的接口不会被当成页面返回
    assert c.get("/api/plans").status_code == 401
    r = c.get("/api/nope", headers={TOKEN_HEADER: TOKEN})
    assert r.status_code == 404 and r.json()["detail"] == "没有这个接口"
    assert c.get("/", headers={"Host": "evil.example"}).status_code == 403


def test_ui_without_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_mod, "STATIC_DIR", tmp_path / "missing")
    code, out = invoke("ui", "--no-browser")
    assert code == 1
    assert "找不到前端构建产物" in out and "npm run build" in out


def test_ui_starts_local_server(
    tmp_path: Path, static: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    started: dict[str, Any] = {}
    monkeypatch.setattr(app_mod, "STATIC_DIR", static)
    monkeypatch.setattr("tunetidy.cli.default_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(app=app, **kw))
    code, out = invoke("ui", "--no-browser", "--port", "8799")
    assert code == 0, out
    assert started["host"] == "127.0.0.1" and started["port"] == 8799
    token = out.split("#token=")[1].split()[0]
    c = TestClient(started["app"], base_url="http://127.0.0.1:8799")
    assert c.get("/api/plans", headers={TOKEN_HEADER: token}).json() == []
    assert c.get("/api/plans", headers={TOKEN_HEADER: TOKEN}).status_code == 401


def test_ui_startup_marks_orphan_jobs(db_path: Path) -> None:
    """tunetidy ui 启动（create_app）时把进程已不在的任务标为中断。"""
    from datetime import UTC, datetime

    from tunetidy.storage import jobs
    from tunetidy.storage.db import connect

    conn = connect(db_path)
    job_id = jobs.create_job(
        conn, "analyze", pid=2**22 + 7, now=datetime.now(UTC), alive=lambda _p: True
    ).id
    create_app(db_path, TOKEN, PORT, CFG)
    assert jobs.load_job(conn, job_id).status == "interrupted"
    conn.close()

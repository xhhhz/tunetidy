"""网页确认与建歌单的接口：确认码只走显示通道，提交正确后登记确认并启动后台任务。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.fakes import FakeBackend
from tests.server.conftest import Env
from tunetidy.analyze import move_songs
from tunetidy.apply import approval
from tunetidy.models import SongKey
from tunetidy.plan import Plan, PlanStatus
from tunetidy.storage import plans as plan_store


def confirm(env: Env, plan_id: str, h: str) -> Any:
    return env.call("POST", f"/api/plans/{plan_id}/confirm", json={"expected_hash": h})


def submit(env: Env, plan_id: str, code: str) -> Any:
    return env.call("POST", f"/api/plans/{plan_id}/confirm/submit", json={"code": code})


def wrong(code: str) -> str:
    return "000000" if code != "000000" else "111111"


def test_full_flow_creates_playlists(env: Env, plan: Plan, conn: sqlite3.Connection) -> None:
    assert env.call("GET", "/api/confirm").json()["available"] is True
    r = confirm(env, plan.id, plan.content_hash)
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "apply" and body["expires_in"] == 120 and body["attempts_left"] == 3
    assert body["summary"][0] == "将新建 2 个私密歌单，来源：我喜欢的音乐"
    r = submit(env, plan.id, env.code)
    assert r.status_code == 200, r.text
    done = env.wait(r.json()["job_id"])
    assert done["status"] == "done" and len(env.backend.owned_by_tool()) == 2
    assert plan_store.load_plan(conn, plan.id).status == PlanStatus.APPLIED


def test_unavailable_without_terminal(db_path: Path, backend: FakeBackend, plan: Plan) -> None:
    env = Env(db_path, backend, None)
    assert env.call("GET", "/api/confirm").json()["available"] is False
    r = confirm(env, plan.id, plan.content_hash)
    assert r.status_code == 403 and r.json()["code"] == "unavailable"


def test_ui_without_interactive_terminal_has_no_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ui 命令用和 CLI approve 同一个判断决定网页确认是否可用。"""
    import uvicorn

    from tests.cli.conftest import invoke
    from tunetidy.server import app as app_mod

    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html></html>")
    monkeypatch.setattr(app_mod, "STATIC_DIR", static)
    monkeypatch.setattr("tunetidy.cli.default_db_path", lambda: tmp_path / "t.db")
    started: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(app=app))

    for interactive, available in ((False, False), (True, True)):
        monkeypatch.setattr(approval, "is_interactive", lambda v=interactive: v)
        code, out = invoke("ui", "--no-browser")
        assert code == 0, out
        assert started["app"].state.gate.available is available
        assert ("网页确认：可用" in out) is available


def test_content_changed_after_request_voids_code(
    env: Env, plan: Plan, conn: sqlite3.Connection
) -> None:
    confirm(env, plan.id, plan.content_hash)
    code = env.code
    move_songs(conn, plan.id, [SongKey("netease", "1")], "R&B")
    r = submit(env, plan.id, code)
    assert r.status_code == 409 and r.json()["code"] == "mismatch"
    assert plan_store.load_plan(conn, plan.id).status == PlanStatus.DRAFT
    assert env.backend.owned_by_tool() == []


def test_stale_page_hash_is_rejected(env: Env, plan: Plan) -> None:
    r = confirm(env, plan.id, "not-the-hash")
    assert r.status_code == 409 and r.json()["code"] == "stale"


def test_terminal_approval_in_between_still_works(
    env: Env, plan: Plan, conn: sqlite3.Connection
) -> None:
    confirm(env, plan.id, plan.content_hash)
    plan_store.record_approval(conn, plan.id, plan.content_hash, "t")  # 终端里确认了同一内容
    r = submit(env, plan.id, env.code)
    assert env.wait(r.json()["job_id"])["status"] == "done"


def test_wrong_codes_and_lock(env: Env, plan: Plan) -> None:
    for _ in range(3):
        confirm(env, plan.id, plan.content_hash)
        bad = wrong(env.code)
        for _ in range(3):
            r = submit(env, plan.id, bad)
        assert r.status_code in (409, 423)
    r = confirm(env, plan.id, plan.content_hash)
    assert r.status_code == 423 and r.json()["code"] == "locked" and r.json()["retry_after"] > 0
    assert env.call("GET", "/api/confirm").json()["locked_seconds"] > 0


def test_backend_failure_reported(env: Env, plan: Plan) -> None:
    env.backend.fail_before["add_tracks"] = {1}
    confirm(env, plan.id, plan.content_hash)
    done = env.wait(submit(env, plan.id, env.code).json()["job_id"])
    assert done["status"] == "failed" and done["error"]


def test_resume_after_interruption(env: Env, plan: Plan, conn: sqlite3.Connection) -> None:
    env.backend.fail_before["add_tracks"] = {3}
    confirm(env, plan.id, plan.content_hash)
    assert env.wait(submit(env, plan.id, env.code).json()["job_id"])["status"] == "failed"
    env.backend.fail_before["add_tracks"] = set()
    current = plan_store.load_plan(conn, plan.id)
    assert current.status == PlanStatus.APPLYING
    r = confirm(env, plan.id, current.content_hash)
    assert r.json()["action"] == "resume" and "继续建" in r.json()["summary"][0]
    done = env.wait(submit(env, plan.id, env.code).json()["job_id"])
    assert done["status"] == "done" and len(env.backend.owned_by_tool()) == 2


def test_applied_plan_cannot_be_confirmed(env: Env, plan: Plan, conn: sqlite3.Connection) -> None:
    confirm(env, plan.id, plan.content_hash)
    env.wait(submit(env, plan.id, env.code).json()["job_id"])
    r = confirm(env, plan.id, plan.content_hash)
    assert r.status_code == 409


def test_not_logged_in(env: Env, plan: Plan) -> None:
    env.backend.user = None
    confirm(env, plan.id, plan.content_hash)
    r = submit(env, plan.id, env.code)
    assert r.status_code == 409 and r.json()["code"] == "not_logged_in"


def test_unknown_job(env: Env) -> None:
    assert env.call("GET", "/api/jobs/nope").status_code == 404

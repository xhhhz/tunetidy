"""网页上开始一次整理：列歌单、选一个或几个歌单开始读取。"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from tests.classify.helpers import song, tags
from tests.fakes import FakeBackend
from tests.server.conftest import Env
from tunetidy.storage import jobs as job_store
from tunetidy.storage import plans as plan_store


def add_drive(backend: FakeBackend) -> None:
    """第二个歌单"开车听"：和"我喜欢的音乐"重复 1、2 两首，另有 3 首摇滚。"""
    songs = [song(1), song(2)]
    for i in (30, 31, 32):
        songs.append(song(i))
        backend.tags[str(i)] = tags(i, "摇滚")
    backend.add_playlist("200", "开车听", songs)
    backend.add_playlist("300", "收藏的", [song(3)], owned=False)


def test_list_playlists(env: Env) -> None:
    add_drive(env.backend)
    r = env.call("GET", "/api/playlists")
    assert r.status_code == 200
    rows = r.json()["playlists"]
    assert [(p["id"], p["name"], p["owned"]) for p in rows] == [
        ("100", "我喜欢的音乐", True),
        ("200", "开车听", True),
        ("300", "收藏的", False),
    ]
    assert rows[0]["track_count"] == 17


def test_list_playlists_needs_login(env: Env) -> None:
    env.backend.user = None
    r = env.call("GET", "/api/playlists")
    assert r.status_code == 409
    assert r.json()["code"] == "not_logged_in"


def test_analyze_merges_in_picked_order(env: Env, conn: sqlite3.Connection) -> None:
    add_drive(env.backend)
    r = env.call("POST", "/api/analyze", json={"playlists": ["200", "100"]})
    assert r.status_code == 200
    job = env.wait(r.json()["job_id"])
    assert job["status"] == "done"
    plan = plan_store.load_plan(conn, job["plan_id"])
    assert [s.id for s in plan.sources] == ["200", "100"]
    ids = [a.key.id for a in sorted(plan.assignments, key=lambda a: a.position)]
    # 先"开车听"的 5 首（含重复的 1、2），再"我喜欢的音乐"里剩下的 15 首
    assert ids[:5] == ["1", "2", "30", "31", "32"]
    assert len(ids) == len(set(ids)) == 20


def test_analyze_busy(env: Env, conn: sqlite3.Connection) -> None:
    job_store.create_job(conn, "apply", plan_id="x", pid=os.getpid(), now=datetime.now(UTC))
    r = env.call("POST", "/api/analyze", json={"playlists": ["100"]})
    assert r.status_code == 409
    assert r.json()["code"] == "busy"
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_analyze_rejects_bad_requests(env: Env) -> None:
    assert env.call("POST", "/api/analyze", json={"playlists": []}).status_code == 422
    too_many = [str(i) for i in range(21)]
    assert env.call("POST", "/api/analyze", json={"playlists": too_many}).status_code == 422
    r = env.call("POST", "/api/analyze", json={"playlists": ["999"]})
    assert r.status_code == 400
    assert "找不到歌单" in r.json()["detail"]
    assert env.call("GET", "/api/analyze").status_code == 405


def test_analyze_needs_login(env: Env) -> None:
    env.backend.user = None
    r = env.call("POST", "/api/analyze", json={"playlists": ["100"]})
    assert r.status_code == 409
    assert r.json()["code"] == "not_logged_in"


def test_current_job_none(env: Env) -> None:
    r = env.call("GET", "/api/jobs/current")
    assert r.status_code == 200
    assert r.json() is None


def test_current_job_and_progress_fields(env: Env) -> None:
    job_id = env.call("POST", "/api/analyze", json={"playlists": ["100"]}).json()["job_id"]
    cur = env.call("GET", "/api/jobs/current").json()
    if cur is not None:  # 假后端很快，可能已经读完
        assert cur["job_id"] == job_id
        assert cur["sources"] == ["我喜欢的音乐"]
    done = env.wait(job_id)
    assert done["sources"] == ["我喜欢的音乐"]
    assert {c["name"] for c in done["categories"]} == {"流行", "R&B"}
    assert env.call("GET", "/api/jobs/current").json() is None


def test_orphaned_job_is_not_current(
    db_path: Path, backend: FakeBackend, conn: sqlite3.Connection
) -> None:
    """理音重启后，上次没读完的任务是"已中断"，不再是当前任务。"""
    job_store.create_job(conn, "analyze", pid=2**22 + 12345, now=datetime.now(UTC))
    env = Env(db_path, backend, None)
    assert env.call("GET", "/api/jobs/current").json() is None


def test_cancel_endpoint(env: Env, conn: sqlite3.Connection) -> None:
    r = env.call("POST", "/api/jobs/nope/cancel", json={})
    assert r.status_code == 409
    assert "只能取消" in r.json()["detail"]
    assert env.call("GET", "/api/jobs/nope/cancel").status_code == 405

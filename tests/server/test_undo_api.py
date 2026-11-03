"""历史与撤销的接口：先回读预览，确认码绑定预览结果，执行前在后台再核对一次。"""

from __future__ import annotations

import sqlite3
from typing import Any

from tests.classify.helpers import song
from tests.server.conftest import Env
from tests.server.test_confirm_api import confirm, submit
from tunetidy.plan import Plan, PlanStatus
from tunetidy.storage import plans as plan_store

POP = "流行 · 整理自 我喜欢的音乐"
RNB = "R&B · 整理自 我喜欢的音乐"


def applied(env: Env, plan: Plan) -> str:
    confirm(env, plan.id, plan.content_hash)
    done = env.wait(submit(env, plan.id, env.code).json()["job_id"])
    assert done["status"] == "done"
    return str(done["run_id"])


def preview(env: Env, run_id: str) -> Any:
    r = env.call("GET", f"/api/runs/{run_id}/undo")
    assert r.status_code == 200, r.text
    return r.json()


def undo(env: Env, run_id: str, fp: str) -> Any:
    r = env.call("POST", f"/api/runs/{run_id}/undo/confirm", json={"fingerprint": fp})
    assert r.status_code == 200, r.text
    r = env.call(
        "POST", f"/api/runs/{run_id}/undo/submit", json={"code": env.code, "fingerprint": fp}
    )
    assert r.status_code == 200, r.text
    return env.wait(r.json()["job_id"])


def test_runs_list(env: Env, plan: Plan) -> None:
    assert env.call("GET", "/api/runs").json() == []
    run_id = applied(env, plan)
    rows = env.call("GET", "/api/runs").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == run_id and row["plan_id"] == plan.id
    assert row["sources"] == ["我喜欢的音乐"] and row["status"] == "done"
    assert [(p["name"], p["added"]) for p in row["playlists"]] == [(POP, 7), (RNB, 5)]


def test_full_undo(env: Env, plan: Plan, conn: sqlite3.Connection) -> None:
    run_id = applied(env, plan)
    p = preview(env, run_id)
    assert [(i["name"], i["action"]) for i in p["items"]] == [(POP, "delete"), (RNB, "delete")]
    assert p["summary"][0].startswith("将撤销") and "整个删除 2 个歌单" in p["summary"][1]
    done = undo(env, run_id, p["fingerprint"])
    assert done["status"] == "done" and sorted(done["deleted"]) == sorted([POP, RNB])
    assert env.backend.owned_by_tool() == []
    assert plan_store.load_plan(conn, plan.id).status == PlanStatus.UNDONE
    assert env.call("GET", "/api/runs").json()[0]["status"] == "undone"


def test_modified_playlist_only_removes_ours(env: Env, plan: Plan) -> None:
    run_id = applied(env, plan)
    rnb = next(x for x in env.backend.owned_by_tool() if x.info.name == RNB)
    rnb.songs.append(song(99))  # 用户往 R&B 歌单里加了一首
    p = preview(env, run_id)
    actions = {i["name"]: i for i in p["items"]}
    assert actions[RNB]["action"] == "remove_ours" and actions[RNB]["differences"]
    done = undo(env, run_id, p["fingerprint"])
    assert done["deleted"] == [POP] and done["trimmed"][0]["name"] == RNB
    assert [x.info.name for x in env.backend.owned_by_tool()] == [RNB]


def test_change_after_preview_stops_and_asks_to_preview_again(env: Env, plan: Plan) -> None:
    """确认的是"整删 2 个"，之后歌单变了：不按旧预览执行，任务失败并提示重新预览，什么都不删。"""
    run_id = applied(env, plan)
    p = preview(env, run_id)
    env.call("POST", f"/api/runs/{run_id}/undo/confirm", json={"fingerprint": p["fingerprint"]})
    env.backend.owned_by_tool()[0].songs.append(song(99))
    r = env.call(
        "POST",
        f"/api/runs/{run_id}/undo/submit",
        json={"code": env.code, "fingerprint": p["fingerprint"]},
    )
    done = env.wait(r.json()["job_id"])
    assert done["status"] == "failed" and "重新预览" in done["error"]
    assert len(env.backend.owned_by_tool()) == 2


def test_stale_fingerprint_on_request(env: Env, plan: Plan) -> None:
    run_id = applied(env, plan)
    p = preview(env, run_id)
    env.backend.owned_by_tool()[0].songs.append(song(99))
    r = env.call("POST", f"/api/runs/{run_id}/undo/confirm", json={"fingerprint": p["fingerprint"]})
    assert r.status_code == 409 and r.json()["code"] == "stale"


def test_undone_run_is_refused(env: Env, plan: Plan) -> None:
    run_id = applied(env, plan)
    undo(env, run_id, preview(env, run_id)["fingerprint"])
    r = env.call("GET", f"/api/runs/{run_id}/undo")
    assert r.status_code == 409


def test_code_is_bound_to_the_undo_fingerprint(env: Env, plan: Plan) -> None:
    run_id = applied(env, plan)
    p = preview(env, run_id)
    env.call("POST", f"/api/runs/{run_id}/undo/confirm", json={"fingerprint": p["fingerprint"]})
    r = env.call(
        "POST", f"/api/runs/{run_id}/undo/submit", json={"code": env.code, "fingerprint": "other"}
    )
    assert r.status_code == 409 and r.json()["code"] == "mismatch"
    assert len(env.backend.owned_by_tool()) == 2


def test_unknown_run(env: Env) -> None:
    assert env.call("GET", "/api/runs/nope/undo").status_code == 404

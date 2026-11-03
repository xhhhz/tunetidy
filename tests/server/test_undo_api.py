"""历史与撤销的接口：先回读预览，确认码绑定预览结果，执行前在后台再核对一次。"""

from __future__ import annotations

import sqlite3
from typing import Any

from tests.classify.helpers import song
from tests.server.conftest import Env
from tests.server.test_confirm_api import confirm, submit
from tunetidy.plan import Plan, PlanStatus
from tunetidy.storage import plans as plan_store
from tunetidy.storage import runs as run_store

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


# ------------------------------------------------------------------ 建完后撤销上次那批


def test_previous_batch_then_undo_it(env: Env, plan: Plan, conn: sqlite3.Connection) -> None:
    from tests.test_analyze import CFG, Clock
    from tunetidy.analyze import analyze

    run_a = applied(env, plan)
    plan_b = analyze(env.backend, conn, CFG, [env.backend.list_playlists()[0]], now=Clock())
    assert env.call("GET", f"/api/plans/{plan_b.id}/previous").json() is None  # B 还没建好
    applied(env, plan_b)

    before = dict(env.backend.calls)
    prev = env.call("GET", f"/api/plans/{plan_b.id}/previous").json()
    used = {k: v - before.get(k, 0) for k, v in env.backend.calls.items() if v != before.get(k, 0)}
    assert used == {"whoami": 1}  # 只确认当前账号，其余只读本地数据库
    assert prev["run_id"] == run_a
    assert prev["sources"] == ["我喜欢的音乐"]
    assert [(p["name"], p["added"]) for p in prev["playlists"]] == [(POP, 7), (RNB, 5)]

    a_ids = {rp.playlist_id for rp in run_store.load_run(conn, run_a).playlists}
    b_ids = {p.info.id for p in env.backend.owned_by_tool()} - a_ids
    done = undo(env, run_a, preview(env, run_a)["fingerprint"])
    assert done["status"] == "done"
    assert {p.info.id for p in env.backend.owned_by_tool()} == b_ids  # 只剩 B 建的
    assert env.call("GET", f"/api/plans/{plan_b.id}/previous").json() is None


def test_previous_batch_logged_out(env: Env, plan: Plan) -> None:
    applied(env, plan)
    env.backend.user = None
    assert env.call("GET", f"/api/plans/{plan.id}/previous").json() is None

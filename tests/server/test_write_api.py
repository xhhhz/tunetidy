"""写接口：预估、重新分类、移动到；已批准要确认、已执行只读、内容过期被拒。"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tunetidy.plan import Plan
from tunetidy.storage import plans as plan_store

POP = {"groups": [{"genres": ["流行"]}]}


def move(client: TestClient, plan_id: str, **body: Any) -> Any:
    return client.post(f"/api/plans/{plan_id}/move", json=body)


def test_simulate_both_match_modes(
    client: TestClient, plan: Plan, conn: sqlite3.Connection
) -> None:
    """ "只分出 流行"里并列显示两种匹配方式的首数；预估不改方案。"""
    url = f"/api/plans/{plan.id}/simulate"
    primary = client.post(url, json={"split": POP}).json()
    any_ = client.post(url, json={"split": {**POP, "match": "any"}}).json()
    assert primary["summary"]["categories"]["流行"] == 7 and primary["changed"]
    assert any_["summary"]["categories"]["流行"] == 12
    assert plan_store.load_plan(conn, plan.id) == plan


def test_genres(client: TestClient, plan: Plan) -> None:
    g = client.get(f"/api/plans/{plan.id}/genres").json()
    assert [x["name"] for x in g["level1"]] == ["流行", "R&B", "嘻哈说唱"]


def test_reclassify(client: TestClient, plan: Plan) -> None:
    r = client.post(
        f"/api/plans/{plan.id}/reclassify",
        json={"split": POP, "expected_hash": plan.content_hash},
    ).json()
    assert r["changed"] and not r["approval_reset"] and r["status"] == "draft"
    assert r["summary"]["categories"] == {"流行": 7, "其他": 7}
    assert r["content_hash"] != plan.content_hash


@pytest.mark.parametrize(
    ("split", "min_size"),
    [({"groups": [{"genres": ["不存在的曲风"]}]}, None), ({}, 0), ({"top_n": 0}, None)],
)
def test_bad_settings_are_400(
    client: TestClient, plan: Plan, split: dict[str, Any], min_size: int | None
) -> None:
    for path in ("simulate", "reclassify"):
        r = client.post(
            f"/api/plans/{plan.id}/{path}",
            json={"split": split, "min_size": min_size, "expected_hash": plan.content_hash},
        )
        assert r.status_code == 400 and r.json()["code"] == "invalid"


def test_move(client: TestClient, plan: Plan, conn: sqlite3.Connection) -> None:
    r = move(
        client, plan.id, songs=[["netease", "1"]], category="R&B", expected_hash=plan.content_hash
    )
    d = r.json()
    assert r.status_code == 200 and d["changed"] and d["manual"] == 1
    assert d["summary"]["categories"] == {"流行": 6, "R&B": 6}
    songs = client.get(f"/api/plans/{plan.id}/songs").json()["songs"]
    assert songs[0]["manual"] and songs[0]["reason"].startswith("人工调整")


def test_move_to_not_written(client: TestClient, plan: Plan) -> None:
    d = move(
        client, plan.id, songs=[["netease", "1"]], category=None, expected_hash=plan.content_hash
    ).json()
    assert d["summary"]["ungrouped"] == 1


def test_bad_move_is_400(client: TestClient, plan: Plan) -> None:
    r = move(
        client, plan.id, songs=[["netease", "15"]], category="流行", expected_hash=plan.content_hash
    )
    assert r.status_code == 400 and "下架" in r.json()["detail"]
    assert move(client, plan.id, songs=[], category="流行", expected_hash="x").status_code == 422


def test_approved_plan_asks_first(client: TestClient, plan: Plan, conn: sqlite3.Connection) -> None:
    """已批准的方案：不带确认先返回 confirm_reset，网页提示"会使批准作废"后再带确认重试。"""
    plan_store.record_approval(conn, plan.id, plan.content_hash, "t")
    body = {"songs": [["netease", "1"]], "category": "R&B", "expected_hash": plan.content_hash}
    r = move(client, plan.id, **body)
    assert r.status_code == 409 and r.json()["code"] == "confirm_reset"
    r2 = client.post(
        f"/api/plans/{plan.id}/reclassify", json={"split": POP, "expected_hash": plan.content_hash}
    )
    assert r2.status_code == 409 and r2.json()["code"] == "confirm_reset"
    assert plan_store.load_plan(conn, plan.id).status == "approved"

    d = move(client, plan.id, **body, confirm_reset=True).json()
    assert d["approval_reset"] and d["status"] == "draft" and not d["approved"]


@pytest.mark.parametrize("status", ["applying", "applied", "undone"])
def test_executed_plan_is_read_only(
    client: TestClient, plan: Plan, conn: sqlite3.Connection, status: str
) -> None:
    conn.execute("UPDATE plans SET status = ? WHERE id = ?", (status, plan.id))
    edits = [
        ("move", {"songs": [["netease", "1"]], "category": "R&B"}),
        ("reclassify", {"split": POP}),
    ]
    for path, body in edits:
        r = client.post(
            f"/api/plans/{plan.id}/{path}",
            json={**body, "expected_hash": plan.content_hash, "confirm_reset": True},
        )
        assert r.status_code == 409 and r.json()["code"] == "not_editable"
    assert client.get(f"/api/plans/{plan.id}/state").json()["editable"] is False
    assert plan_store.load_plan(conn, plan.id).assignments == plan.assignments


def test_stale_page_is_rejected(client: TestClient, plan: Plan, conn: sqlite3.Connection) -> None:
    """网页还停在旧内容上，方案已被别处改过：拒绝，让网页刷新。"""
    body = {"songs": [["netease", "1"]], "category": "R&B", "expected_hash": plan.content_hash}
    assert move(client, plan.id, **body).status_code == 200
    r = move(client, plan.id, **{**body, "songs": [["netease", "2"]]})
    assert r.status_code == 409 and r.json()["code"] == "stale"


def test_writes_require_post(client: TestClient, plan: Plan) -> None:
    for path in ("move", "reclassify", "simulate"):
        assert client.get(f"/api/plans/{plan.id}/{path}").status_code == 405


def test_writes_require_token(client: TestClient, plan: Plan) -> None:
    body = {"songs": [["netease", "1"]], "category": "R&B", "expected_hash": plan.content_hash}
    r = client.post(f"/api/plans/{plan.id}/move", json=body, headers={"X-TuneTidy-Token": ""})
    assert r.status_code == 401


def test_rename(client: TestClient, plan: Plan) -> None:
    r = client.post(
        f"/api/plans/{plan.id}/rename",
        json={"category": "R&B", "name": "深夜 R&B", "expected_hash": plan.content_hash},
    )
    assert r.status_code == 200 and r.json()["changed"]
    assert r.json()["content_hash"] != plan.content_hash
    d = client.get(f"/api/plans/{plan.id}").json()
    assert d["playlists"][1]["name"] == "深夜 R&B"


def test_bad_rename_is_400(client: TestClient, plan: Plan) -> None:
    r = client.post(
        f"/api/plans/{plan.id}/rename",
        json={"category": "R&B", "name": "长" * 41, "expected_hash": plan.content_hash},
    )
    assert r.status_code == 400 and r.json()["code"] == "invalid"
    assert "40" in r.json()["detail"]


def test_reset(client: TestClient, plan: Plan) -> None:
    moved = move(
        client, plan.id, songs=[["netease", "13"]], category="流行", expected_hash=plan.content_hash
    )
    r = client.post(
        f"/api/plans/{plan.id}/reset",
        json={"songs": [["netease", "13"]], "expected_hash": moved.json()["content_hash"]},
    )
    assert r.status_code == 200 and r.json()["content_hash"] == plan.content_hash


def test_parse_toml(client: TestClient) -> None:
    text = 'match = "any"\nother = false\n\n[[groups]]\nname = "流行"\ngenres = ["流行"]\n'
    d = client.post("/api/split/parse", json={"text": text}).json()
    assert d == {
        "level": 1,
        "groups": [{"name": "流行", "genres": ["流行"]}],
        "match": "any",
        "top_n": None,
        "other": False,
    }
    for bad in ("match = ", 'match = "x"', "[[groups]]\nname = 1"):
        r = client.post("/api/split/parse", json={"text": bad})
        assert r.status_code == 400 and r.json()["code"] == "invalid"

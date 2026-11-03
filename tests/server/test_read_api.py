"""只读接口：方案列表、摘要、状态、明细、写回预览。"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from tunetidy.plan import Plan
from tunetidy.server.app import TOKEN_HEADER
from tunetidy.storage import plans as plan_store


def test_list_plans(client: TestClient, plan: Plan) -> None:
    rows = client.get("/api/plans").json()
    assert rows == [
        {
            "id": plan.id,
            "created_at": plan.created_at,
            "status": "draft",
            "sources": ["我喜欢的音乐"],
            "total": 17,
            "pending": 3,
            "source_ids": [plan.sources[0].id],
            "updated_at": plan.created_at,
            "split": plan.settings["split"],
            "other_name": "其他",
            "categories": [{"name": "流行", "count": 7}, {"name": "R&B", "count": 5}],
            "manual": 0,
        }
    ]


def test_list_plans_counts_manual_moves(client: TestClient, plan: Plan) -> None:
    songs = [["netease", "1"], ["netease", "2"]]
    r = client.post(
        f"/api/plans/{plan.id}/move",
        json={"expected_hash": plan.content_hash, "songs": songs, "category": "R&B"},
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/plans").json()[0]["manual"] == 2


def test_ping_requires_token(client: TestClient) -> None:
    assert client.get("/api/ping").json() == {"ok": True}
    r = client.get("/api/ping", headers={TOKEN_HEADER: "wrong"})
    assert r.status_code == 401


def test_list_plans_empty(client: TestClient) -> None:
    assert client.get("/api/plans").json() == []


def test_plan_summary(client: TestClient, plan: Plan) -> None:
    d = client.get(f"/api/plans/{plan.id}").json()
    assert d["status"] == "draft" and d["editable"] and not d["approved"]
    assert d["content_hash"] == plan.content_hash
    assert d["summary"]["categories"] == {"流行": 7, "R&B": 5}
    assert d["summary"]["pending"] == 3 and d["summary"]["unavailable"] == 2
    assert d["min_size"] == 2 and d["other_name"] == "其他" and d["manual"] == 0
    assert d["private"] is True
    assert d["playlists"] == [
        {
            "category": "流行",
            "name": "流行 · 整理自 我喜欢的音乐",
            "default_name": "流行 · 整理自 我喜欢的音乐",
            "count": 7,
        },
        {
            "category": "R&B",
            "name": "R&B · 整理自 我喜欢的音乐",
            "default_name": "R&B · 整理自 我喜欢的音乐",
            "count": 5,
        },
    ]


def test_unknown_plan_is_404(client: TestClient) -> None:
    for path in ("", "/state", "/songs", "/preview"):
        r = client.get(f"/api/plans/nope{path}")
        assert r.status_code == 404
        assert "找不到方案" in r.json()["detail"]


def test_songs(client: TestClient, plan: Plan) -> None:
    d = client.get(f"/api/plans/{plan.id}/songs").json()
    rows = d["songs"]
    assert len(rows) == 17 and d["content_hash"] == plan.content_hash
    assert [r["position"] for r in rows] == list(range(17))
    first = rows[0]
    assert first["id"] == "1" and first["name"] == "s1" and first["artists"] == []
    assert first["category"] == "流行" and first["status_text"] == "确定"
    assert first["playlist"] and "流行" in first["playlist"]
    assert "官方曲风" in first["reason"]
    tied = next(r for r in rows if r["id"] == "13")
    assert tied["status"] == "pending" and tied["playlist"] is None
    assert "多个曲风并列" in tied["reason"]
    assert tied["unsure"] and tied["tied"] and "分不出主次" in tied["why"]
    assert not first["unsure"] and first["tied"] == ["流行"]
    assert first["why"] == "只有一个曲风：流行"
    assert sum(r["unsure"] for r in rows) == 3


def test_songs_have_genre_tags(client: TestClient, plan: Plan) -> None:
    """表格"曲风标签"列：网易云给这首歌打的曲风，按原顺序，没有标签时为空。"""
    rows = {r["id"]: r for r in client.get(f"/api/plans/{plan.id}/songs").json()["songs"]}
    assert rows["1"]["genres"] == [{"name": "华语流行", "used": True}]  # 只有流行时流行不去掉
    # 看主要曲风时，有别的曲风就不看"流行"：标成没用上，网页显示成浅色
    assert rows["8"]["genres"] == [{"name": "R&B", "used": True}, {"name": "流行", "used": False}]
    assert rows["17"]["genres"] == []


def test_any_match_uses_every_genre_tag(client: TestClient, plan: Plan) -> None:
    """ "带标签就算"时每个标签都参与匹配，没有浅色标签。"""
    r = client.post(
        f"/api/plans/{plan.id}/reclassify",
        json={
            "split": {"groups": [{"genres": ["流行"]}], "match": "any"},
            "expected_hash": plan.content_hash,
        },
    )
    assert r.status_code == 200
    rows = {r["id"]: r for r in client.get(f"/api/plans/{plan.id}/songs").json()["songs"]}
    assert rows["8"]["genres"] == [{"name": "R&B", "used": True}, {"name": "流行", "used": True}]


def test_state_follows_cli_changes(
    client: TestClient, plan: Plan, conn: sqlite3.Connection
) -> None:
    """网页打开期间 CLI 批准或执行了方案，状态接口要立刻反映出来。"""
    s = client.get(f"/api/plans/{plan.id}/state").json()
    assert s == {
        "status": "draft",
        "content_hash": plan.content_hash,
        "approved": False,
        "editable": True,
    }
    plan_store.record_approval(conn, plan.id, plan.content_hash, "t")
    s = client.get(f"/api/plans/{plan.id}/state").json()
    assert s["status"] == "approved" and s["approved"] and s["editable"]
    conn.execute("UPDATE plans SET status = 'applied' WHERE id = ?", (plan.id,))
    s = client.get(f"/api/plans/{plan.id}/state").json()
    assert s["status"] == "applied" and not s["editable"]


def test_preview(client: TestClient, plan: Plan) -> None:
    d = client.get(f"/api/plans/{plan.id}/preview").json()
    assert d["private"] is True
    assert [(p["category"], p["count"]) for p in d["playlists"]] == [("流行", 7), ("R&B", 5)]
    assert d["total"] == 12
    assert d["skipped"] == {"pending": 3, "unavailable": 2, "ungrouped": 0}
    assert d["commands"] == [
        f"tunetidy apply {plan.id} --dry-run",
        f"tunetidy approve {plan.id}",
        f"tunetidy apply {plan.id}",
    ]


def test_read_api_does_not_write(client: TestClient, plan: Plan, conn: sqlite3.Connection) -> None:
    before = plan_store.load_plan(conn, plan.id)
    for path in ("", "/state", "/songs", "/preview"):
        client.get(f"/api/plans/{plan.id}{path}")
    client.get("/api/plans")
    assert plan_store.load_plan(conn, plan.id) == before


def test_request_connection_works_across_threads(db_path: Path) -> None:
    """FastAPI 在一个线程里打开依赖、在另一个线程里执行接口（真实服务里会出现）。"""
    from types import SimpleNamespace

    from tunetidy.server.api import db

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_path=db_path)))
    gen = db(request)  # type: ignore[arg-type]
    conn = next(gen)
    errors: list[BaseException] = []

    def use() -> None:
        try:
            conn.execute("SELECT COUNT(*) FROM plans").fetchone()
            gen.close()  # 关闭也可能在别的线程
        except BaseException as e:
            errors.append(e)

    t = threading.Thread(target=use)
    t.start()
    t.join()
    assert errors == []

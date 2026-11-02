"""MCP 工具逻辑：五个工具都用假后端测（包括会访问账号的三个），钥匙串是假的。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests.apply.test_writer import APPLY, build_library
from tests.fakes import FakeBackend
from tunetidy.config import ClassifyConfig, Config
from tunetidy.mcp.tools import NOT_LOGGED_IN, Tools
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import connect

CONFIG = Config(classify=ClassifyConfig(min_size=2), apply=APPLY)


@pytest.fixture
def backend() -> FakeBackend:
    return build_library()


@pytest.fixture
def tools(tmp_path: Path, backend: FakeBackend) -> Tools:
    return Tools(tmp_path / "t.db", CONFIG, lambda: backend)


def wait(tools: Tools, job_id: str) -> dict[str, Any]:
    for _ in range(300):
        s = tools.get_job_status(job_id)
        if s["status"] != "running":
            return s
        time.sleep(0.02)
    raise AssertionError("任务没有结束")


def make_plan(tools: Tools) -> str:
    started = tools.create_plan(["我喜欢的音乐"])
    status = wait(tools, started["job_id"])
    assert status["status"] == "done", status
    return str(status["plan"]["id"])


def test_list_playlists(tools: Tools) -> None:
    out = tools.list_playlists()
    assert out["playlists"] == [
        {"id": "100", "name": "我喜欢的音乐", "track_count": 17, "owned": True}
    ]


def test_create_plan_by_name_or_id(tools: Tools) -> None:
    by_name = wait(tools, tools.create_plan(["我喜欢的音乐"])["job_id"])
    by_id = wait(tools, tools.create_plan(["100"])["job_id"])
    assert by_name["status"] == by_id["status"] == "done"
    plan = by_name["plan"]
    assert [p["count"] for p in plan["playlists"]] == [7, 5]
    assert all(len(p["samples"]) == 3 for p in plan["playlists"])
    assert plan["pending"] == 3
    assert f"tunetidy approve {plan['id']}" in by_name["message"]


def test_create_plan_unknown_playlist_starts_nothing(tools: Tools, tmp_path: Path) -> None:
    out = tools.create_plan(["不存在的歌单"])
    assert "不存在的歌单" in out["error"]
    conn = connect(tmp_path / "t.db")
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    conn.close()


def test_create_plan_split_options(tools: Tools) -> None:
    only = wait(tools, tools.create_plan(["100"], only=["流行"], match="any")["job_id"])
    assert {p["category"] for p in only["plan"]["playlists"]} == {"流行", "其他"}
    bad = tools.create_plan(["100"], groups_toml="[[groups]]\nname = 1")
    assert "error" in bad
    both = tools.create_plan(
        ["100"], only=["流行"], groups_toml='[[groups]]\nname="a"\ngenres=["流行"]'
    )
    assert "只能二选一" in both["error"]


def test_get_plan_items_paging(tools: Tools) -> None:
    plan_id = make_plan(tools)
    page = tools.get_plan_items(plan_id, category="流行", page_size=5)
    assert page["total"] == 7 and page["pages"] == 2 and len(page["items"]) == 5
    item = page["items"][0]
    assert set(item) == {"id", "name", "artists", "album", "genres", "why"}
    assert tools.get_plan_items(plan_id, category="流行", page=3, page_size=5)["items"] == []
    assert tools.get_plan_items(plan_id, show="unsure")["total"] == 3
    assert tools.get_plan_items(plan_id, page_size=999)["page_size"] == 200
    assert "error" in tools.get_plan_items(plan_id, category="摇滚")
    assert "error" in tools.get_plan_items("nope")


def test_apply_plan_only_after_terminal_approval(
    tools: Tools, backend: FakeBackend, tmp_path: Path
) -> None:
    plan_id = make_plan(tools)
    refused = tools.apply_plan(plan_id)
    assert f"tunetidy approve {plan_id}" in refused["error"]
    conn = connect(tmp_path / "t.db")
    plan = plan_store.load_plan(conn, plan_id)
    plan_store.record_approval(conn, plan_id, plan.content_hash, "t")  # 相当于用户在终端确认
    conn.close()
    before = len(backend.playlists)
    started = tools.apply_plan(plan_id)
    done = wait(tools, started["job_id"])
    assert done["status"] == "done" and len(backend.playlists) == before + 2
    assert [p["count"] for p in done["created"]] == [7, 5]


def test_get_job_status_unknown(tools: Tools) -> None:
    assert "找不到任务" in tools.get_job_status("nope")["error"]


def test_not_logged_in_message_for_every_account_tool(tmp_path: Path) -> None:
    logged_out = build_library()
    logged_out.user = None
    t = Tools(tmp_path / "t.db", CONFIG, lambda: logged_out)
    assert t.list_playlists() == {"error": NOT_LOGGED_IN}
    assert t.create_plan(["100"]) == {"error": NOT_LOGGED_IN}
    assert t.apply_plan("any") == {"error": NOT_LOGGED_IN}
    # 不碰账号的两个工具照常工作
    assert "找不到任务" in t.get_job_status("nope")["error"]
    assert "error" in t.get_plan_items("nope")

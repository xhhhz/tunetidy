"""MCP 服务：工具恰好五个、写回标为破坏性、参数里没有限速类设置、stdout 只有协议消息。"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.apply.test_writer import build_library
from tests.conftest import FAKE_KEYRING
from tests.mcp.test_tools import CONFIG
from tunetidy.mcp.server import build_server
from tunetidy.mcp.tools import Tools

ROOT = Path(__file__).resolve().parents[2]
# 每个工具允许的参数，多一个少一个都算失败
PARAMS = {
    "list_playlists": set(),
    "create_plan": {"playlists", "level", "only", "match", "top_n", "other", "groups_toml"},
    "get_job_status": {"job_id"},
    "get_plan_items": {"plan_id", "category", "show", "page", "page_size"},
    "apply_plan": {"plan_id"},
}
FORBIDDEN = (
    "rate",
    "delay",
    "interval",
    "sleep",
    "concurren",
    "limit",
    "batch",
    "pause",
    "throttle",
)


@pytest.fixture
def tools(tmp_path: Path) -> Tools:
    b = build_library()
    return Tools(tmp_path / "t.db", CONFIG, lambda: b)


def listed(tools: Tools) -> dict[str, Any]:
    return {t.name: t for t in asyncio.run(build_server(tools).list_tools())}


def test_tool_names_exact(tools: Tools) -> None:
    """恰好五个工具；没有确认、撤销、续传之类的工具。"""
    assert set(listed(tools)) == set(PARAMS)


def test_apply_plan_is_destructive(tools: Tools) -> None:
    ann = listed(tools)["apply_plan"].annotations
    assert (
        ann.destructive_hint is True
        and ann.idempotent_hint is False
        and ann.open_world_hint is True
    )
    for name in ("list_playlists", "get_job_status", "get_plan_items"):
        assert listed(tools)[name].annotations.read_only_hint is True


def test_parameters_are_whitelisted(tools: Tools) -> None:
    """参数表与白名单完全一致，且没有任何限速、间隔、并发类参数：限速只读配置文件。"""
    for name, tool in listed(tools).items():
        params = set(tool.input_schema.get("properties", {}))
        assert params == PARAMS[name], name
        for p in params:
            assert not any(word in p.lower() for word in FORBIDDEN), (name, p)


def test_call_tool_round_trip(tools: Tools) -> None:
    result = asyncio.run(build_server(tools).call_tool("list_playlists", {}))
    text = json.dumps(result.model_dump(), ensure_ascii=False)
    assert "我喜欢的音乐" in text


def test_mcp_command_writes_only_protocol_to_stdout(tmp_path: Path) -> None:
    """以子进程运行 tunetidy mcp：HOME 指向临时目录、钥匙串是假的；stdout 每一行都是 JSON-RPC。"""
    import mcp.types as t

    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PYTHON_KEYRING_BACKEND": FAKE_KEYRING,
        "PYTHONPATH": str(ROOT),
    }
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": t.LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_playlists", "arguments": {}},
        },
    ]
    proc = subprocess.Popen(
        [sys.executable, "-c", "from tunetidy.cli import app; app()", "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    assert proc.stdin and proc.stdout
    replies: dict[int, Any] = {}
    for req in requests:
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        if "id" not in req:
            continue
        while True:
            line = proc.stdout.readline()
            assert line, "服务提前退出"
            msg = json.loads(line)  # 不是 JSON 就说明有东西写进了 stdout
            assert msg["jsonrpc"] == "2.0"
            if msg.get("id") == req["id"]:
                replies[req["id"]] = msg
                break
    proc.stdin.close()
    proc.wait(timeout=10)
    assert {x["name"] for x in replies[2]["result"]["tools"]} == set(PARAMS)
    # 假钥匙串里没有登录信息：返回固定提示，不会连网易云
    assert "tunetidy login" in json.dumps(replies[3], ensure_ascii=False)

"""扫码后端写入方法：只在假服务器上测，不碰真实账号。"""

from __future__ import annotations

import json
import random
from typing import Any

import pytest

from tests.backends.web.fake_netease import FakeClock, FakeNetease, plain_weapi
from tests.conftest import load_fixture
from tunetidy.backends.base import BackendError, NotLoggedInError
from tunetidy.backends.web.backend import NeteaseWebBackend
from tunetidy.backends.web.session import MemoryStore, Session
from tunetidy.config import Config

ACCOUNT = load_fixture("netease/account_get.json")


@pytest.fixture(autouse=True)
def _plain(monkeypatch: pytest.MonkeyPatch) -> None:
    plain_weapi(monkeypatch)


def make(server: FakeNetease, logged_in: bool = True) -> NeteaseWebBackend:
    s = Session(cookies={"MUSIC_U": "u", "__csrf": "c"}) if logged_in else Session()
    clock = FakeClock()
    return NeteaseWebBackend(
        Config(), MemoryStore(s), transport=server.transport(), sleep=clock.sleep, clock=clock,
        rng=random.Random(0),
    )  # fmt: skip


def detail(ids: list[int]) -> dict[str, Any]:
    return {"code": 200, "playlist": {"id": 555, "name": "x", "trackIds": [{"id": i} for i in ids]}}


def test_create_playlist_private_then_writes_description() -> None:
    server = FakeNetease()
    server.route("/api/w/nuser/account/get", ACCOUNT)
    server.route("/api/playlist/create", {"code": 200, "id": 555})
    server.route("/api/playlist/desc/update", {"code": 200})
    pid = make(server).create_playlist("摇滚 · 整理自 x", "说明 tunetidy:run-1/p001")
    assert pid == "555"
    create = next(c for c in server.calls if c.path == "/api/playlist/create")
    assert create.data["privacy"] == "10" and create.data["type"] == "NORMAL"
    desc = next(c for c in server.calls if c.path == "/api/playlist/desc/update")
    assert desc.data == {**desc.data, "id": 555, "desc": "说明 tunetidy:run-1/p001"}
    assert server.paths().index("/api/playlist/create") < server.paths().index(
        "/api/playlist/desc/update"
    )


def test_create_public_playlist() -> None:
    server = FakeNetease()
    server.route("/api/w/nuser/account/get", ACCOUNT)
    server.route("/api/playlist/create", {"code": 200, "playlist": {"id": 7}})
    server.route("/api/playlist/desc/update", {"code": 200})
    assert make(server).create_playlist("a", "b", private=False) == "7"
    assert next(c for c in server.calls if c.path == "/api/playlist/create").data["privacy"] == "0"


def test_create_playlist_description_failure_raises() -> None:
    """简介写失败：抛错，由 apply 续传时按"无标记的空歌单"处理。"""
    server = FakeNetease()
    server.route("/api/w/nuser/account/get", ACCOUNT)
    server.route("/api/playlist/create", {"code": 200, "id": 555})
    server.route("/api/playlist/desc/update", {"code": 405, "message": "操作频繁"})
    with pytest.raises(BackendError, match="写入歌单简介"):
        make(server).create_playlist("a", "b")


def test_create_requires_login() -> None:
    with pytest.raises(NotLoggedInError):
        make(FakeNetease(), logged_in=False).create_playlist("a", "b")


def test_add_tracks_uses_read_back() -> None:
    server = FakeNetease()
    server.route("/api/playlist/manipulate/tracks", {"code": 200, "count": 2})
    server.route("/api/v6/playlist/detail", detail([3, 1]))  # 2 没进去
    res = make(server).add_tracks("555", ["1", "2", "3"])
    assert res.added == ("1", "3") and res.rejected == ("2",)
    call = next(c for c in server.calls if c.path == "/api/playlist/manipulate/tracks")
    assert call.data["op"] == "add" and call.data["pid"] == 555
    assert json.loads(call.data["trackIds"]) == [1, 2, 3]
    assert call.data["imme"] == "true"


def test_add_tracks_all_unavailable_401() -> None:
    server = FakeNetease()
    server.route("/api/playlist/manipulate/tracks", {"code": 401, "message": "下架歌曲无法收藏"})
    server.route("/api/v6/playlist/detail", detail([]))
    res = make(server).add_tracks("555", ["9"])
    assert res.added == () and res.rejected == ("9",)
    assert res.message == "下架歌曲无法收藏"


def test_add_tracks_risk_code_raises() -> None:
    server = FakeNetease()
    server.route("/api/playlist/manipulate/tracks", {"code": 405, "message": "操作频繁"})
    with pytest.raises(BackendError, match="405"):
        make(server).add_tracks("555", ["1"])
    assert "/api/v6/playlist/detail" not in server.paths()


def test_add_tracks_batch_limit() -> None:
    with pytest.raises(ValueError, match="100"):
        make(FakeNetease()).add_tracks("555", [str(i) for i in range(101)])


def test_remove_tracks_and_delete_playlist() -> None:
    server = FakeNetease()
    server.route("/api/playlist/manipulate/tracks", {"code": 200})
    server.route("/api/playlist/remove", {"code": 200})
    b = make(server)
    b.remove_tracks("555", ["1", "2"])
    b.delete_playlist("555")
    rm = next(c for c in server.calls if c.path == "/api/playlist/manipulate/tracks")
    assert rm.data["op"] == "del" and json.loads(rm.data["trackIds"]) == [1, 2]
    assert next(c for c in server.calls if c.path == "/api/playlist/remove").data["ids"] == "[555]"


def test_delete_failure_raises() -> None:
    server = FakeNetease()
    server.route("/api/playlist/remove", {"code": 404, "message": "歌单不存在"})
    with pytest.raises(BackendError, match="删除歌单"):
        make(server).delete_playlist("555")

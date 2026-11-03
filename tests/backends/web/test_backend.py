"""扫码后端只读部分：用假服务器离线测试。"""

from __future__ import annotations

import random
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from tests.backends.web.fake_netease import FakeClock, FakeNetease, plain_weapi
from tests.conftest import load_fixture
from tunetidy.backends.base import BackendError, LoginExpiredError, NotLoggedInError
from tunetidy.backends.web.backend import NeteaseWebBackend
from tunetidy.backends.web.session import MemoryStore, Session
from tunetidy.config import Config

ACCOUNT = load_fixture("netease/account_get.json")


@pytest.fixture(autouse=True)
def _plain(monkeypatch: pytest.MonkeyPatch) -> None:
    plain_weapi(monkeypatch)


@pytest.fixture
def server() -> FakeNetease:
    return FakeNetease()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make(
    server: FakeNetease, clock: FakeClock, session: Session | None = None
) -> NeteaseWebBackend:
    return NeteaseWebBackend(
        Config(),
        MemoryStore(session),
        transport=server.transport(),
        sleep=clock.sleep,
        clock=clock,
        rng=random.Random(0),
    )


def logged_in() -> Session:
    return Session(cookies={"MUSIC_U": "fake-u", "__csrf": "fake-csrf"}, device_id="D" * 52)


# ------------------------------------------------------------------ 登录


def test_qr_login_flow_with_duplicate_cookie_names(server: FakeNetease, clock: FakeClock) -> None:
    polls: Iterator[int] = iter([801, 801, 802, 803])
    server.route("/api/login/qrcode/unikey", load_fixture("netease/login_qr_unikey.json"))
    server.route("/api/w/nuser/account/get", ACCOUNT)
    shown: list[str] = []
    store = MemoryStore()
    b = NeteaseWebBackend(
        Config(), store, transport=server.transport(), sleep=clock.sleep, clock=clock
    )

    def poll(_d: dict[str, Any]) -> dict[str, Any] | httpx.Response:
        code = next(polls)
        # 只有 803 下发登录态，且同名 Cookie 按不同 Path 下发多份（实测）
        if code == 803:
            return httpx.Response(
                200,
                json={"code": 803, "message": "授权登陆成功"},
                headers=[
                    ("Set-Cookie", "MUSIC_A_T=sub; Path=/eapi/clientlog"),
                    ("Set-Cookie", "MUSIC_A_T=root; Path=/"),
                    ("Set-Cookie", "MUSIC_U=new-u; Path=/"),
                    ("Set-Cookie", "__csrf=new-csrf; Path=/"),
                ],
            )
        return {"code": code, "message": "m"}

    server.route("/api/login/qrcode/client/login", poll)
    user = b.login(shown.append)

    assert user.id == "10000001"
    assert shown and shown[0].startswith("https://music.163.com/login?codekey=")
    assert store.session.cookies["MUSIC_U"] == "new-u"
    assert store.session.cookies["MUSIC_A_T"] == "root"
    # 账号接口带上了新的登录态
    assert "MUSIC_U=new-u" in (server.calls[-1].cookie or "")
    # 每次请求之间都按限速等待了 1–2 秒
    assert all(1.0 <= s <= 2.0 for s in clock.sleeps)


@pytest.mark.parametrize(("code", "msg"), [(800, "过期"), (8821, "code=8821")])
def test_qr_login_failures(server: FakeNetease, clock: FakeClock, code: int, msg: str) -> None:
    server.route("/api/login/qrcode/unikey", {"code": 200, "unikey": "k" * 36})
    server.route("/api/login/qrcode/client/login", {"code": code, "message": "需要行为验证码"})
    with pytest.raises(BackendError, match=msg):
        make(server, clock).login(lambda _url: None)


def test_qr_login_times_out(server: FakeNetease, clock: FakeClock) -> None:
    server.route("/api/login/qrcode/unikey", {"code": 200, "unikey": "k" * 36})
    server.route("/api/login/qrcode/client/login", {"code": 801})
    with pytest.raises(BackendError, match="超时"):
        make(server, clock).login(lambda _url: None)
    assert 60 <= len(server.calls) <= 200  # 3 分钟内按 1–2 秒间隔轮询


def test_qr_login_reports_scanned_once(server: FakeNetease, clock: FakeClock) -> None:
    polls: Iterator[int] = iter([801, 802, 802, 803])
    server.route("/api/login/qrcode/unikey", {"code": 200, "unikey": "k" * 36})
    server.route("/api/w/nuser/account/get", ACCOUNT)

    def poll(_d: dict[str, Any]) -> dict[str, Any] | httpx.Response:
        code = next(polls)
        if code == 803:
            return httpx.Response(
                200, json={"code": 803}, headers=[("Set-Cookie", "MUSIC_U=u; Path=/")]
            )
        return {"code": code}

    server.route("/api/login/qrcode/client/login", poll)
    scanned: list[bool] = []
    user = make(server, clock).login(lambda _url: None, on_scanned=lambda: scanned.append(True))
    assert user.id == "10000001"
    assert scanned == [True]


def test_qr_login_expired_and_timeout_are_login_expired(
    server: FakeNetease, clock: FakeClock
) -> None:
    server.route("/api/login/qrcode/unikey", {"code": 200, "unikey": "k" * 36})
    server.route("/api/login/qrcode/client/login", {"code": 800})
    with pytest.raises(LoginExpiredError):
        make(server, clock).login(lambda _url: None)
    server.route("/api/login/qrcode/client/login", {"code": 801})
    with pytest.raises(LoginExpiredError):
        make(server, clock).login(lambda _url: None)


def test_login_discards_old_cookies_but_keeps_device(server: FakeNetease, clock: FakeClock) -> None:
    server.route("/api/login/qrcode/unikey", {"code": 200, "unikey": "k" * 36})
    server.route("/api/login/qrcode/client/login", {"code": 800})
    b = make(server, clock, logged_in())
    with pytest.raises(BackendError):
        b.login(lambda _url: None)
    assert "MUSIC_U" not in b.session.cookies
    assert b.session.device_id == "D" * 52


def test_whoami_without_login_makes_no_request(server: FakeNetease, clock: FakeClock) -> None:
    assert make(server, clock).whoami() is None
    assert server.calls == []


def test_logout_clears_store(server: FakeNetease, clock: FakeClock) -> None:
    store = MemoryStore(logged_in())
    b = NeteaseWebBackend(Config(), store, transport=server.transport(), sleep=clock.sleep)
    b.logout()
    assert not store.session.logged_in
    assert not b.session.logged_in


# ------------------------------------------------------------------ 读取


def test_list_playlists_paginates(server: FakeNetease, clock: FakeClock) -> None:
    pls = load_fixture("netease/user_playlist.json")["playlist"]
    pages = iter([{"code": 200, "more": True, "playlist": pls[:2]}, {"code": 200, "more": False,
                  "playlist": pls[2:]}])  # fmt: skip
    server.route("/api/w/nuser/account/get", ACCOUNT)
    server.route("/api/user/playlist", lambda _d: next(pages))
    out = make(server, clock, logged_in()).list_playlists()
    assert len(out) == 3
    offsets = [c.data["offset"] for c in server.calls if c.path == "/api/user/playlist"]
    assert offsets == [0, 2]


def test_find_playlists_by_marker_only_owned(server: FakeNetease, clock: FakeClock) -> None:
    server.route("/api/w/nuser/account/get", ACCOUNT)
    server.route(
        "/api/user/playlist",
        {
            "code": 200,
            "more": False,
            "playlist": [
                {"id": 1, "name": "a", "subscribed": False, "description": "x tunetidy:run-1"},
                {"id": 2, "name": "b", "subscribed": True, "description": "tunetidy:run-1"},
                {"id": 3, "name": "c", "subscribed": False, "description": "tunetidy:run-2"},
                {"id": 4, "name": "d", "subscribed": False, "description": None},
            ],
        },
    )
    found = make(server, clock, logged_in()).find_playlists_by_marker("tunetidy:run-1")
    assert [p.id for p in found] == ["1"]


def test_not_logged_in_code_raises(server: FakeNetease, clock: FakeClock) -> None:
    server.route("/api/w/nuser/account/get", ACCOUNT)
    server.route("/api/user/playlist", {"code": 301, "msg": "需要登录"})
    with pytest.raises(NotLoggedInError):
        make(server, clock, logged_in()).list_playlists()


def test_list_playlists_requires_login(server: FakeNetease, clock: FakeClock) -> None:
    with pytest.raises(NotLoggedInError):
        make(server, clock).list_playlists()


def test_get_tracks_batches_and_keeps_order(server: FakeNetease, clock: FakeClock) -> None:
    ids = list(range(1, 1033))  # 1032 首，与作者歌单同规模
    missing = 500

    def detail(data: dict[str, Any]) -> dict[str, Any]:
        wanted = [x["id"] for x in __import__("json").loads(data["c"])]
        # 故意倒序返回，并少返回一首
        return {
            "code": 200,
            "songs": [{"id": x, "name": f"s{x}", "ar": [], "al": {}} for x in reversed(wanted)
                      if x != missing],
            "privileges": [{"id": x, "st": 0, "fee": 8} for x in wanted],
        }  # fmt: skip

    server.route(
        "/api/v6/playlist/detail",
        {"code": 200, "playlist": {"id": 9, "name": "p", "trackIds": [{"id": x} for x in ids]}},
    )
    server.route("/api/v3/song/detail", detail)
    songs = make(server, clock, logged_in()).get_tracks("9")
    assert [s.key.id for s in songs] == [str(x) for x in ids if x != missing]
    batches = [c for c in server.calls if c.path == "/api/v3/song/detail"]
    assert len(batches) == 2  # 1000 + 32


def test_get_song_tags_is_anonymous_and_lazy(server: FakeNetease, clock: FakeClock) -> None:
    wiki = load_fixture("netease/song_wiki_0.json")
    server.route("/api/song/play/about/block/page", wiki)
    b = make(server, clock, logged_in())
    it = b.get_song_tags(["1", "2", "3"])
    first = next(it)
    assert len(server.calls) == 1  # 逐首请求，调用方可边取边存
    assert [g.name for g in first.genres] == ["流行"]
    assert first.raw_json and "SONG_PLAY_ABOUT_SONG_BASIC" in first.raw_json
    rest = list(it)
    assert [t.key.id for t in rest] == ["2", "3"]
    # 匿名：即使已登录，百科请求也不带任何 Cookie
    assert all(c.cookie is None for c in server.calls)
    assert [c.data["songId"] for c in server.calls] == ["1", "2", "3"]


def test_anonymous_request_after_cookie_response_has_no_cookie(
    server: FakeNetease, clock: FakeClock
) -> None:
    server.route("/api/w/nuser/account/get", ACCOUNT)
    server.set_cookies["/api/w/nuser/account/get"] = ["MUSIC_U=rotated; Path=/"]
    server.route("/api/song/play/about/block/page", load_fixture("netease/song_wiki_1.json"))
    b = make(server, clock, logged_in())
    assert b.whoami() is not None
    list(b.get_song_tags(["7"]))
    assert server.calls[-1].cookie is None
    assert b.session.cookies["MUSIC_U"] == "rotated"


def test_get_song_tags_error_code_raises(server: FakeNetease, clock: FakeClock) -> None:
    server.route("/api/song/play/about/block/page", {"code": 405, "message": "操作频繁"})
    with pytest.raises(BackendError, match="405"):
        list(make(server, clock).get_song_tags(["1"]))


def test_get_style_tree(server: FakeNetease, clock: FakeClock) -> None:
    server.route("/api/tag/list/get", load_fixture("netease/style_list.json"))
    nodes = make(server, clock).get_style_tree()
    assert len(nodes) == 648


def test_get_playlist_snapshot(server: FakeNetease, clock: FakeClock) -> None:
    server.route("/api/v6/playlist/detail", load_fixture("netease/playlist_detail.json"))
    snap = make(server, clock, logged_in()).get_playlist("19723756")
    assert len(snap.track_ids) == 20
    assert server.calls[0].data["id"] == 19723756
    assert "MUSIC_U=fake-u" in (server.calls[0].cookie or "")


# ------------------------------------------------------------------ 网络


def test_network_errors_retry_then_succeed(clock: FakeClock) -> None:
    attempts = {"n": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise httpx.ConnectError("boom", request=req)
        return httpx.Response(200, json=load_fixture("netease/style_list.json"))

    b = NeteaseWebBackend(
        Config(), MemoryStore(), transport=httpx.MockTransport(flaky), sleep=clock.sleep,
        clock=clock,
    )  # fmt: skip
    assert len(b.get_style_tree()) == 648
    assert attempts["n"] == 3
    assert 2.0 in clock.sleeps and 5.0 in clock.sleeps  # 退避


def test_network_errors_give_up_without_leaking_cookie(clock: FakeClock) -> None:
    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=req)

    b = NeteaseWebBackend(
        Config(), MemoryStore(logged_in()), transport=httpx.MockTransport(down),
        sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    with pytest.raises(BackendError) as ei:
        b.get_playlist("1")
    assert "ConnectError" in str(ei.value)
    assert "fake-u" not in str(ei.value) and "fake-csrf" not in str(ei.value)


def test_non_json_response(clock: FakeClock) -> None:
    b = NeteaseWebBackend(
        Config(), MemoryStore(),
        transport=httpx.MockTransport(lambda r: httpx.Response(502, text="<html>")),
        sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    with pytest.raises(BackendError, match="HTTP 502"):
        b.get_style_tree()


def test_client_ignores_system_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    b = NeteaseWebBackend(Config(), MemoryStore())
    assert b.client.http.trust_env is False


def test_whoami_remembers_user_locally(server: FakeNetease, clock: FakeClock) -> None:
    """登录态里顺带记下用户 id 和昵称（只存本机钥匙串），供本地 pre-commit hook 比对。"""
    server.route("/api/w/nuser/account/get", ACCOUNT)
    b = make(server, clock, logged_in())
    user = b.whoami()
    assert user is not None
    assert (b.session.user_id, b.session.nickname) == (user.id, user.nickname)
    again = Session.from_json(b.session.to_json())
    assert (again.user_id, again.nickname) == (user.id, user.nickname)


def test_old_session_json_still_loads() -> None:
    old = Session.from_json('{"cookies": {"MUSIC_U": "x"}, "device_id": "D"}')
    assert old.logged_in and old.user_id is None and old.nickname is None

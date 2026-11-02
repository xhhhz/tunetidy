"""扫码登录后端：直接调用网易云 Web/客户端接口。整个 backends/web/ 目录可以单独删除。"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from typing import Any

import httpx

from tunetidy.backends.base import BackendError, MusicBackend, NotLoggedInError
from tunetidy.backends.web import parse
from tunetidy.backends.web.client import Client
from tunetidy.backends.web.session import Session, SessionStore
from tunetidy.config import Config
from tunetidy.models import (
    AddResult,
    PlaylistInfo,
    PlaylistSnapshot,
    Song,
    SongTags,
    StyleNode,
    User,
)

QR_TIMEOUT = 180.0
QR_WAITING, QR_SCANNED, QR_EXPIRED, QR_OK = 801, 802, 800, 803
NOT_LOGGED_IN = 301
UNADDABLE = 401  # 加歌：整批都是下架歌曲
SONG_DETAIL_BATCH = 1000  # 实测 500 与 1000 都能一次返回
PLAYLIST_PAGE = 100


def _check(body: dict[str, Any], what: str) -> dict[str, Any]:
    code = body.get("code")
    if code == 200:
        return body
    if code == NOT_LOGGED_IN:
        raise NotLoggedInError("登录已失效，请重新运行 tunetidy login")
    msg = body.get("message") or body.get("msg") or ""
    raise BackendError(f"{what}失败：code={code} {msg}".strip())


class NeteaseWebBackend:
    platform = parse.PLATFORM
    max_batch_size = 100  # 只验证到 100

    def __init__(
        self,
        config: Config,
        store: SessionStore,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self._clock = clock
        self.client = Client(
            store.load(), config.network, transport=transport, sleep=sleep, clock=clock, rng=rng
        )

    @property
    def session(self) -> Session:
        return self.client.session

    # ------------------------------------------------------------ 登录

    def login(self, show_qr: Callable[[str], None]) -> User:
        self.client.session = Session(device_id=self.session.device_id)  # 丢弃旧 Cookie
        body = self.client.eapi("/api/login/qrcode/unikey", {"type": 3})
        unikey = body.get("unikey")
        if body.get("code") != 200 or not unikey:
            raise BackendError(f"获取登录二维码失败：code={body.get('code')}")
        show_qr(f"https://music.163.com/login?codekey={unikey}")

        deadline = self._clock() + QR_TIMEOUT
        while self._clock() < deadline:
            body = self.client.eapi("/api/login/qrcode/client/login", {"key": unikey, "type": 3})
            code = body.get("code")
            if code in (QR_WAITING, QR_SCANNED):
                continue
            if code == QR_OK:
                self.store.save(self.session)
                user = self.whoami()
                if user is None:
                    raise BackendError("扫码成功，但没能取到账号信息，请重试")
                return user
            if code == QR_EXPIRED:
                raise BackendError("二维码已过期，请重新登录")
            raise BackendError(f"登录失败：code={code} {body.get('message') or ''}".strip())
        raise BackendError("等待扫码超时，请重新登录")

    def logout(self) -> None:
        self.store.clear()
        self.client.session = Session(device_id=self.session.device_id)

    def whoami(self) -> User | None:
        if not self.session.logged_in:
            return None
        body = self.client.weapi("/api/w/nuser/account/get")
        user = parse.parse_user(body) if body.get("code") == 200 else None
        if user is not None:
            self.session.user_id, self.session.nickname = user.id, user.nickname
            self.store.save(self.session)  # 保存服务器可能下发的新 Cookie
        return user

    def _require_user(self) -> User:
        user = self.whoami()
        if user is None:
            raise NotLoggedInError("未登录或登录已失效，请先运行 tunetidy login")
        return user

    # ------------------------------------------------------------ 读取

    def _user_playlist_pages(self) -> list[dict[str, Any]]:
        uid = self._require_user().id
        entries: list[dict[str, Any]] = []
        offset = 0
        while True:
            body = _check(
                self.client.weapi(
                    "/api/user/playlist", {"uid": uid, "limit": PLAYLIST_PAGE, "offset": offset}
                ),
                "读取歌单列表",
            )
            page = body.get("playlist") or []
            entries += page
            if not body.get("more") or not page:
                return entries
            offset += len(page)

    def list_playlists(self) -> list[PlaylistInfo]:
        return parse.parse_playlists({"playlist": self._user_playlist_pages()})

    def find_playlists_by_marker(self, marker: str) -> list[PlaylistInfo]:
        body = {"playlist": self._user_playlist_pages()}
        desc = parse.playlist_descriptions(body)
        return [p for p in parse.parse_playlists(body) if p.owned and marker in desc[p.id]]

    def get_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        body = self.client.eapi(
            "/api/v6/playlist/detail", {"id": int(playlist_id), "n": 100000, "s": 8}
        )
        return parse.parse_playlist_detail(_check(body, f"读取歌单 {playlist_id}"))

    def get_tracks(self, playlist_id: str) -> list[Song]:
        ids = self.get_playlist(playlist_id).track_ids
        songs: dict[str, Song] = {}
        for i in range(0, len(ids), SONG_DETAIL_BATCH):
            chunk = ids[i : i + SONG_DETAIL_BATCH]
            c = "[" + ",".join(f'{{"id":{x}}}' for x in chunk) + "]"
            body = _check(self.client.eapi("/api/v3/song/detail", {"c": c}), "读取歌曲详情")
            songs.update(parse.parse_song_detail(body))
        return [songs[x] for x in ids if x in songs]

    def get_song_tags(self, song_ids: Sequence[str]) -> Iterator[SongTags]:
        for sid in song_ids:
            body = self.client.anonymous_get("/api/song/play/about/block/page", {"songId": sid})
            _check(body, f"读取歌曲 {sid} 的百科")
            block = parse.wiki_basic_block(body)
            tags = parse.parse_song_wiki(sid, body)
            raw = json.dumps(block, ensure_ascii=False) if block else None
            yield replace(tags, raw_json=raw)

    def get_style_tree(self) -> list[StyleNode]:
        body = _check(self.client.weapi("/api/tag/list/get"), "读取曲风树")
        return parse.parse_style_tree(body)

    # ------------------------------------------------------------ 写入
    # 接口与参数均经过实测。
    # 只供 apply/ 调用，且只作用于本工具新建的歌单。

    def create_playlist(self, name: str, description: str, private: bool = True) -> str:
        self._require_user()
        body = _check(
            self.client.weapi(
                "/api/playlist/create",
                {
                    "name": name,
                    "privacy": str(parse.PRIVATE_PRIVACY if private else 0),
                    "type": "NORMAL",
                },
            ),
            "新建歌单",
        )
        pid = body.get("id") or (body.get("playlist") or {}).get("id")
        if not pid:
            raise BackendError("新建歌单成功但没有返回歌单 id")
        # 创建时写不进简介（实测），再调一次修改接口写入运行标记
        _check(
            self.client.eapi("/api/playlist/desc/update", {"id": int(pid), "desc": description}),
            "写入歌单简介",
        )
        return str(pid)

    def _manipulate(self, op: str, playlist_id: str, song_ids: Sequence[str]) -> dict[str, Any]:
        if len(song_ids) > self.max_batch_size:
            raise ValueError(f"单次最多 {self.max_batch_size} 首")
        return self.client.eapi(
            "/api/playlist/manipulate/tracks",
            {
                "op": op,
                "pid": int(playlist_id),
                "trackIds": json.dumps([int(s) for s in song_ids]),
                "imme": "true",
            },
        )

    def add_tracks(self, playlist_id: str, song_ids: Sequence[str]) -> AddResult:
        body = self._manipulate("add", playlist_id, song_ids)
        code = body.get("code")
        if code not in (200, UNADDABLE):
            _check(body, "加入歌曲")
        # 以回读为准：整批都是下架歌曲时平台返回 401；混在一起时会一起加入（实测）
        present = set(self.get_playlist(playlist_id).track_ids)
        message = None if code == 200 else str(body.get("message") or body.get("msg") or "")
        return AddResult(
            added=tuple(s for s in song_ids if s in present),
            rejected=tuple(s for s in song_ids if s not in present),
            message=message,
        )

    def remove_tracks(self, playlist_id: str, song_ids: Sequence[str]) -> None:
        _check(self._manipulate("del", playlist_id, song_ids), "移除歌曲")

    def delete_playlist(self, playlist_id: str) -> None:
        _check(
            self.client.weapi("/api/playlist/remove", {"ids": f"[{int(playlist_id)}]"}),
            "删除歌单",
        )


def _conforms(backend: NeteaseWebBackend) -> MusicBackend:
    """让 mypy 静态检查本类完整实现了 MusicBackend 接口。"""
    return backend

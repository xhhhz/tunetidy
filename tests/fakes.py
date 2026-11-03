"""内存里的假后端，实现 MusicBackend。不依赖 backends/web。

读取部分供 analyze 测试；写入部分照搬实测的平台行为。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field

from tunetidy.backends.base import BackendError
from tunetidy.models import (
    AddResult,
    CopyrightStatus,
    PlaylistInfo,
    PlaylistSnapshot,
    Song,
    SongKey,
    SongTags,
    StyleNode,
    User,
)


@dataclass
class FakePlaylist:
    info: PlaylistInfo
    songs: list[Song]
    description: str = ""
    private: bool = False
    subscribed_count: int = 0


@dataclass
class FakeBackend:
    style_tree: list[StyleNode]
    playlists: dict[str, FakePlaylist] = field(default_factory=dict)
    tags: dict[str, SongTags] = field(default_factory=dict)  # song id → 标签
    user: User | None = field(default_factory=lambda: User("netease", "10000001", "测试用户"))
    platform: str = "netease"
    max_batch_size: int = 100
    calls: Counter[str] = field(default_factory=Counter)
    tag_requests: list[str] = field(default_factory=list)
    fail_tags_after: int | None = None  # 取到第 n 首后抛错，模拟中断
    # 写入故障注入：方法名 → 第几次调用时出错。before：动作前出错；after：动作完成后才出错
    fail_before: dict[str, set[int]] = field(default_factory=dict)
    fail_after: dict[str, set[int]] = field(default_factory=dict)
    crash_between_create_and_desc: bool = False  # 建好歌单、写简介前中断
    unavailable_ids: set[str] = field(default_factory=set)  # 下架：整批都是这类歌时拒绝
    reject_ids: set[str] = field(default_factory=set)  # 平台静默不收的歌
    writes: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    _next_id: int = 900000

    def add_playlist(
        self, pid: str, name: str, songs: list[Song], owned: bool = True
    ) -> PlaylistInfo:
        info = PlaylistInfo(self.platform, pid, name, len(songs), owned)
        self.playlists[pid] = FakePlaylist(info, list(songs))
        return info

    # -------------------------------------------------------------- 读取

    def login(
        self, show_qr: Callable[[str], None], on_scanned: Callable[[], None] | None = None
    ) -> User:
        show_qr("https://example.invalid/qr")
        if on_scanned is not None:
            on_scanned()
        assert self.user is not None
        return self.user

    def whoami(self) -> User | None:
        self.calls["whoami"] += 1
        return self.user

    def logout(self) -> None:
        self.user = None

    def _info(self, p: FakePlaylist) -> PlaylistInfo:
        return PlaylistInfo(self.platform, p.info.id, p.info.name, len(p.songs), p.info.owned)

    def list_playlists(self) -> list[PlaylistInfo]:
        self.calls["list_playlists"] += 1
        return [self._info(p) for p in self.playlists.values()]

    def find_playlists_by_marker(self, marker: str) -> list[PlaylistInfo]:
        self.calls["find_playlists_by_marker"] += 1
        return [
            self._info(p)
            for p in self.playlists.values()
            if p.info.owned and marker in p.description
        ]

    def get_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        self.calls["get_playlist"] += 1
        p = self.playlists[playlist_id]
        return PlaylistSnapshot(
            self.platform,
            playlist_id,
            p.info.name,
            p.description,
            p.private,
            p.subscribed_count,
            tuple(s.key.id for s in p.songs),
        )

    def get_tracks(self, playlist_id: str) -> list[Song]:
        self.calls["get_tracks"] += 1
        return list(self.playlists[playlist_id].songs)

    def get_song_tags(self, song_ids: Sequence[str]) -> Iterator[SongTags]:
        for sid in song_ids:
            if self.fail_tags_after is not None and len(self.tag_requests) >= self.fail_tags_after:
                raise BackendError("模拟网络中断")
            self.tag_requests.append(sid)
            if sid in self.tags:
                yield self.tags[sid]
            else:
                yield SongTags(SongKey(self.platform, sid), (), (), None, None)

    def get_style_tree(self) -> list[StyleNode]:
        self.calls["get_style_tree"] += 1
        return list(self.style_tree)

    # -------------------------------------------------------------- 写入（照搬实测行为）

    def _tick(self, method: str) -> int:
        self.calls[method] += 1
        n = self.calls[method]
        if n in self.fail_before.get(method, set()):
            raise BackendError(f"模拟 {method} 第 {n} 次调用失败（动作前）")
        return n

    def _after(self, method: str, n: int) -> None:
        if n in self.fail_after.get(method, set()):
            raise BackendError(f"模拟 {method} 第 {n} 次调用失败（动作后）")

    def _song(self, sid: str) -> Song:
        for p in self.playlists.values():
            for s in p.songs:
                if s.key.id == sid:
                    return s
        return Song(SongKey(self.platform, sid), sid, (), None, None, None, None,
                    CopyrightStatus.NORMAL)  # fmt: skip

    def owned_by_tool(self) -> list[FakePlaylist]:
        return [p for p in self.playlists.values() if "tunetidy:run-" in p.description]

    def create_playlist(self, name: str, description: str, private: bool = True) -> str:
        n = self._tick("create_playlist")
        self._next_id += 1
        pid = str(self._next_id)
        info = PlaylistInfo(self.platform, pid, name, 0, True)
        self.playlists[pid] = FakePlaylist(info, [], "", private)
        self.writes.append(("create_playlist", pid, (name,)))
        if self.crash_between_create_and_desc:
            self.crash_between_create_and_desc = False
            raise BackendError("模拟：歌单已建好，写简介前中断")
        # 平台不支持创建时写简介，后端内部再调一次修改接口（实测）
        self.playlists[pid].description = description
        self._after("create_playlist", n)
        return pid

    def add_tracks(self, playlist_id: str, song_ids: Sequence[str]) -> AddResult:
        n = self._tick("add_tracks")
        assert len(song_ids) <= self.max_batch_size, "超过单次上限"
        self.writes.append(("add_tracks", playlist_id, tuple(song_ids)))
        p = self.playlists[playlist_id]
        if song_ids and all(s in self.unavailable_ids for s in song_ids):
            # 整批都是下架歌曲：平台返回 401（实测）
            return AddResult((), tuple(song_ids), "下架歌曲无法收藏")
        present = {s.key.id for s in p.songs}
        for sid in song_ids:  # 新歌加在顶部，批内倒序
            if sid not in present and sid not in self.reject_ids:
                p.songs.insert(0, self._song(sid))
                present.add(sid)
        self._after("add_tracks", n)
        now = {s.key.id for s in p.songs}
        return AddResult(
            tuple(s for s in song_ids if s in now), tuple(s for s in song_ids if s not in now)
        )

    def remove_tracks(self, playlist_id: str, song_ids: Sequence[str]) -> None:
        n = self._tick("remove_tracks")
        self.writes.append(("remove_tracks", playlist_id, tuple(song_ids)))
        p = self.playlists[playlist_id]
        p.songs = [s for s in p.songs if s.key.id not in set(song_ids)]
        self._after("remove_tracks", n)

    def delete_playlist(self, playlist_id: str) -> None:
        n = self._tick("delete_playlist")
        self.writes.append(("delete_playlist", playlist_id, ()))
        del self.playlists[playlist_id]
        self._after("delete_playlist", n)

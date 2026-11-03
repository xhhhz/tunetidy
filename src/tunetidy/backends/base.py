"""MusicBackend 接口。上层模块只依赖这里，不直接访问任何平台。"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import Protocol

from tunetidy.models import (
    AddResult,
    PlaylistInfo,
    PlaylistSnapshot,
    Song,
    SongTags,
    StyleNode,
    User,
)


class BackendError(RuntimeError):
    """平台返回错误或网络失败。message 已脱敏，可直接展示给用户。"""


class NotLoggedInError(BackendError):
    pass


class LoginExpiredError(BackendError):
    """登录二维码过期，或等待扫码超时；重新开始登录即可。"""


class MusicBackend(Protocol):
    platform: str
    max_batch_size: int  # 单次 add_tracks 允许的最大歌曲数

    def login(
        self, show_qr: Callable[[str], None], on_scanned: Callable[[], None] | None = None
    ) -> User:
        """交互式登录。show_qr 收到二维码内容（URL），由调用方负责展示；扫码后、手机上还没
        确认时调用一次 on_scanned。二维码过期或等待超时抛 LoginExpiredError。"""
        ...

    def whoami(self) -> User | None:
        """当前登录用户；未登录或登录失效返回 None。"""
        ...

    def logout(self) -> None:
        """删掉本机保存的登录态，不请求平台。"""
        ...

    def list_playlists(self) -> list[PlaylistInfo]: ...

    def get_tracks(self, playlist_id: str) -> list[Song]:
        """歌单全部歌曲，按歌单当前顺序。"""
        ...

    def get_song_tags(self, song_ids: Sequence[str]) -> Iterator[SongTags]:
        """逐首产出标签；后端内部限速。调用方可边取边存，中断后续传。"""
        ...

    def get_style_tree(self) -> list[StyleNode]: ...

    def create_playlist(self, name: str, description: str, private: bool = True) -> str:
        """新建歌单并写入简介，返回歌单 id。平台不支持创建时写简介的，后端内部再调一次修改接口。"""
        ...

    def find_playlists_by_marker(self, marker: str) -> list[PlaylistInfo]:
        """在当前用户自己创建的歌单里，按简介中的运行标记查找。"""
        ...

    def get_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        """歌单现状（名称、简介、私密、收藏数、歌曲 id），用于续传与撤销前回读。"""
        ...

    def add_tracks(self, playlist_id: str, song_ids: Sequence[str]) -> AddResult:
        """一次请求加入一批歌（不超过 max_batch_size）。分批、排序和记录由调用方负责。"""
        ...

    def remove_tracks(self, playlist_id: str, song_ids: Sequence[str]) -> None:
        """仅用于撤销本工具加入的歌。"""
        ...

    def delete_playlist(self, playlist_id: str) -> None:
        """仅用于撤销本工具创建的歌单。"""
        ...

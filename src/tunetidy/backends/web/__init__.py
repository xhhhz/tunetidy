"""扫码登录后端（非官方接口）。可整体删除，不影响其他模块。"""

from __future__ import annotations

from tunetidy.backends.web.backend import NeteaseWebBackend
from tunetidy.backends.web.session import KeyringStore
from tunetidy.config import Config, data_dir


def create_backend(config: Config) -> NeteaseWebBackend:
    store = KeyringStore(fallback=data_dir() / "credentials" / "netease-web.json")
    return NeteaseWebBackend(config, store)

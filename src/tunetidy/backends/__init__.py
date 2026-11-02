"""后端注册表。具体后端在用到时才导入，删除某个后端目录不影响其他模块。"""

from __future__ import annotations

import importlib
import importlib.util

from tunetidy.backends.base import MusicBackend
from tunetidy.config import Config

# 名称 → (模块, 工厂函数)；工厂函数签名为 (Config) -> MusicBackend
_REGISTRY = {"web": ("tunetidy.backends.web", "create_backend")}


class UnknownBackendError(LookupError):
    pass


def available_backends() -> list[str]:
    out = []
    for name, (module, _) in _REGISTRY.items():
        if importlib.util.find_spec(module) is not None:
            out.append(name)
    return out


def get_backend(name: str, config: Config) -> MusicBackend:
    if name not in _REGISTRY:
        raise UnknownBackendError(f"未知后端：{name}")
    module, factory = _REGISTRY[name]
    if importlib.util.find_spec(module) is None:
        raise UnknownBackendError(f"后端 {name} 未安装或已被移除")
    backend: MusicBackend = getattr(importlib.import_module(module), factory)(config)
    return backend

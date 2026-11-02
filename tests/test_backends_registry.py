import pytest

from tunetidy import backends
from tunetidy.backends import UnknownBackendError, get_backend
from tunetidy.config import Config


def test_unknown_backend_name() -> None:
    with pytest.raises(UnknownBackendError, match="未知后端"):
        get_backend("nope", Config())


def test_removed_backend_reports_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟后端目录被整体删除：注册表里有名字，但模块不存在。"""
    monkeypatch.setitem(backends._REGISTRY, "gone", ("tunetidy.backends.gone", "create_backend"))
    assert "gone" not in backends.available_backends()
    with pytest.raises(UnknownBackendError, match="已被移除"):
        get_backend("gone", Config())

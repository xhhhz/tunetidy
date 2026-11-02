"""真实接口测试：只读，需要用户在场并已登录（tunetidy login）。默认不运行：uv run pytest -m live"""

import pytest

from tunetidy.backends.web import create_backend
from tunetidy.config import load_config

pytestmark = pytest.mark.live

PUBLIC_SONG = "186016"


@pytest.fixture(scope="module")
def backend():  # type: ignore[no-untyped-def]
    b = create_backend(load_config())
    if b.whoami() is None:
        pytest.skip("未登录，先运行 tunetidy login")
    return b


def test_list_playlists(backend) -> None:  # type: ignore[no-untyped-def]
    assert backend.list_playlists()


def test_style_tree(backend) -> None:  # type: ignore[no-untyped-def]
    assert len(backend.get_style_tree()) > 600


def test_song_tags_anonymous(backend) -> None:  # type: ignore[no-untyped-def]
    (tags,) = list(backend.get_song_tags([PUBLIC_SONG]))
    assert tags.genres

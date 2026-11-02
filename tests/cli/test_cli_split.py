"""analyze 的拆分参数：--level / --groups / --only / --match / --top / --no-other。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.cli.conftest import invoke
from tests.fakes import FakeBackend
from tunetidy import cli
from tunetidy.classify.split import SplitError, SplitSettings

EXAMPLES = Path(__file__).resolve().parents[2] / "taxonomies" / "examples"
SRC = "我喜欢的音乐"  # 流行 7、R&B+流行 5、R&B+嘻哈说唱 2、下架 2、云盘 1


def playlists(out: str) -> dict[str, int]:
    """从 analyze 输出的表格里读出 {分类: 歌曲数}。"""
    return {
        m.group(1): int(m.group(2)) for m in re.finditer(r"│ (.+?) · 整理自 \S+\s+│ (\d+)\s+│", out)
    }


def test_default_is_level1(backend: FakeBackend) -> None:
    code, out = invoke("analyze", SRC)
    assert code == 0 and "（按一级曲风）" in out
    assert playlists(out) == {"流行": 7, "R&B": 5}


def test_only_primary(backend: FakeBackend) -> None:
    code, out = invoke("analyze", SRC, "--only", "流行")
    assert code == 0 and "只分出 流行（纯风格）" in out
    # R&B+流行 去掉泛化后是 R&B；R&B+嘻哈说唱 两个候选都落在"其他"，直接归入
    assert playlists(out) == {"流行": 7, "其他": 7}


def test_only_any(backend: FakeBackend) -> None:
    code, out = invoke("analyze", SRC, "--only", "流行", "--match", "any")
    assert code == 0 and "（含该标签）" in out
    assert playlists(out) == {"流行": 12, "其他": 2}


def test_only_full_path(backend: FakeBackend) -> None:
    code, out = invoke("analyze", SRC, "--only", "流行-华语流行")
    assert code == 0
    assert playlists(out) == {"华语流行": 7, "其他": 7}


def test_groups_file_with_no_other(backend: FakeBackend) -> None:
    code, out = invoke("analyze", SRC, "--groups", str(EXAMPLES / "pop-only.toml"), "--no-other")
    assert code == 0, out
    assert playlists(out) == {"流行": 7}
    assert "未分组 7 首" in out and '不建"其他"' in out


def test_level2(backend: FakeBackend) -> None:
    code, out = invoke("analyze", SRC, "--level", "2")
    assert code == 0 and "（按二级曲风）" in out
    assert playlists(out) == {"华语流行": 7, "R&B · 其他": 5}


def test_top(backend: FakeBackend) -> None:
    code, out = invoke("analyze", SRC, "--top", "1")
    assert code == 0 and "前 1 类" in out
    assert playlists(out) == {"流行": 7, "其他": 5}


@pytest.mark.parametrize(
    ("args", "msg"),
    [
        (["--groups", str(EXAMPLES / "pop-only.toml"), "--only", "流行"], "只能二选一"),
        (["--level", "2", "--only", "流行"], "--level 只用于"),
        (["--match", "any"], "--match 只对自定义分组"),
        (["--groups", "/nope/missing.toml"], "找不到分组文件"),
    ],
)
def test_conflicts_fail_before_any_request(backend: FakeBackend, args: list[str], msg: str) -> None:
    code, out = invoke("analyze", SRC, *args)
    assert code == 1 and msg in out and "Traceback" not in out
    assert backend.calls["list_playlists"] == 0 and backend.tag_requests == []


def test_unknown_genre_fails_before_fetching_tags(backend: FakeBackend) -> None:
    code, out = invoke("analyze", SRC, "--only", "流形")
    assert code == 1 and "曲风树里没有「流形」" in out
    assert backend.tag_requests == []


@pytest.mark.parametrize("args", [["--top", "0"], ["--level", "3"], ["--match", "fuzzy"]])
def test_out_of_range_values(backend: FakeBackend, args: list[str]) -> None:
    code, _ = invoke("analyze", SRC, *args)
    assert code == 2  # typer 参数校验


def test_flags_override_file() -> None:
    s = cli.build_split(None, EXAMPLES / "mixed.toml", None, cli.MatchMode.any, 2, True)
    assert (s.match, s.top_n, s.other, len(s.groups)) == ("any", 2, False, 4)
    plain = cli.build_split(None, EXAMPLES / "mixed.toml", None, None, None, False)
    assert (plain.match, plain.top_n, plain.other) == ("primary", 6, True)
    assert cli.build_split(2, None, None, None, None, False) == SplitSettings(level=2)
    with pytest.raises(SplitError):
        cli.build_split(1, EXAMPLES / "mixed.toml", None, None, None, False)

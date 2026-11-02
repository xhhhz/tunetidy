"""拆分设置：TOML 加载、校验、曲风名解析（一级名与全路径两种写法）。"""

from pathlib import Path

import pytest

from tests.classify.helpers import netease_taxonomy, tag_id
from tunetidy.classify.split import (
    Group,
    SplitError,
    SplitSettings,
    load_split,
    only,
    parse_split,
    resolve_groups,
)
from tunetidy.classify.taxonomy import TaxonomyLookupError

EXAMPLES = Path(__file__).resolve().parents[2] / "taxonomies" / "examples"


# ------------------------------------------------------------------ 曲风树查找


def test_find_by_level1_name_and_full_path() -> None:
    tax = netease_taxonomy()
    assert tax.find("流行").tag_id == tag_id("流行")
    assert tax.find("流行-华语流行").tag_id == tag_id("流行-华语流行")
    assert tax.find("华语流行").tag_id == tag_id("流行-华语流行")  # 当前树里名称唯一
    assert tax.path(tag_id("流行-华语流行")) == "流行-华语流行"


def test_find_name_containing_hyphen() -> None:
    """名称本身带 "-"（Jump-Up）：既能按名称找，也能按全路径找。"""
    tax = netease_taxonomy()
    node = tax.find("Jump-Up")
    assert tax.find(tax.path(node.tag_id)) == node
    assert tax.path(node.tag_id).endswith("-Jump-Up")


def test_find_unknown_and_ambiguous() -> None:
    from tunetidy.classify.taxonomy import Taxonomy
    from tunetidy.models import StyleNode

    with pytest.raises(TaxonomyLookupError, match="没有「不存在」"):
        netease_taxonomy().find("不存在")
    tax = Taxonomy(
        [
            StyleNode("1", "流行", None, 1),
            StyleNode("2", "摇滚", None, 1),
            StyleNode("3", "其他", "1", 2),
            StyleNode("4", "其他", "2", 2),
        ]
    )
    with pytest.raises(TaxonomyLookupError, match="重名，请写全路径") as ei:
        tax.find("其他")
    assert "流行-其他" in str(ei.value) and "摇滚-其他" in str(ei.value)
    assert tax.find("摇滚-其他").tag_id == "4"


def test_is_at_or_below() -> None:
    tax = netease_taxonomy()
    pop, cpop = tag_id("流行"), tag_id("流行-华语流行")
    assert tax.is_at_or_below(cpop, pop)
    assert tax.is_at_or_below(pop, pop)
    assert not tax.is_at_or_below(pop, cpop)  # 上级不在下级之下
    assert not tax.is_at_or_below(cpop, tag_id("摇滚"))
    assert not tax.is_at_or_below("99999999", pop)


# ------------------------------------------------------------------ TOML


def test_load_examples_and_resolve() -> None:
    tax = netease_taxonomy()
    pop = load_split(EXAMPLES / "pop-only.toml")
    assert pop.match == "primary" and pop.other is True
    assert [g.name for g in resolve_groups(pop, tax, "其他")] == ["流行"]
    mixed = load_split(EXAMPLES / "mixed.toml")
    assert mixed.top_n == 6
    groups = resolve_groups(mixed, tax, "其他")
    assert [g.name for g in groups] == ["华语流行", "其他流行", "R&B 与说唱", "轻音乐与纯音乐"]
    assert groups[0].tag_ids == (tag_id("流行-华语流行"),)
    assert groups[1].tag_ids == (tag_id("流行"),)


def test_parse_defaults() -> None:
    s = parse_split({})
    assert s == SplitSettings()
    assert s.describe() == "按一级曲风"


@pytest.mark.parametrize(
    ("raw", "msg"),
    [
        ({"match": "fuzzy"}, "match 只能是"),
        ({"level": 3}, "level 只能是"),
        ({"top_n": 0}, "top_n 至少为 1"),
        ({"other": "no"}, "true 或 false"),
        ({"allow_duplicates": True}, "暂不支持"),
        ({"colour": 1}, "未知配置项"),
        ({"groups": []}, "groups 不能为空"),
        ({"groups": [{"genres": ["流行"]}]}, "缺少 name"),
        ({"groups": [{"name": "a", "genres": "流行"}]}, "字符串列表"),
        ({"groups": [{"name": "a", "genres": []}]}, "至少要列一个曲风"),
        ({"groups": [{"name": "a", "genres": ["流行"], "x": 1}]}, "未知配置项"),
    ],
)
def test_parse_rejects(raw: dict[str, object], msg: str) -> None:
    with pytest.raises(SplitError, match=msg):
        parse_split(raw)


def test_load_missing_and_invalid(tmp_path: Path) -> None:
    with pytest.raises(SplitError, match="找不到"):
        load_split(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[[groups]\n", encoding="utf-8")
    with pytest.raises(SplitError, match="TOML"):
        load_split(bad)


@pytest.mark.parametrize(
    ("groups", "msg"),
    [
        ((Group("a", ("不存在的曲风",)),), "曲风树里没有"),
        ((Group("其他", ("流行",)),), "不能叫「其他」"),
        ((Group("a", ("流行",)), Group("a", ("摇滚",))), "两个分组都叫「a」"),
        ((Group("a", ("流行",)), Group("b", ("流行",))), "同时写在了「a」和「b」"),
        ((Group(" ", ("流行",)),), "不能为空"),
    ],
)
def test_resolve_rejects(groups: tuple[Group, ...], msg: str) -> None:
    with pytest.raises(SplitError, match=msg):
        resolve_groups(SplitSettings(groups=groups), netease_taxonomy(), "其他")


def test_parent_and_child_in_different_groups_is_allowed() -> None:
    """写在前面的具体组先接走它的歌，后面的上级组接剩下的——是合法写法。"""
    s = SplitSettings(groups=(Group("华语流行", ("流行-华语流行",)), Group("流行", ("流行",))))
    assert len(resolve_groups(s, netease_taxonomy(), "其他")) == 2


def test_only_builds_one_group_per_genre() -> None:
    s = SplitSettings(groups=only(["流行", "流行-华语流行"]), match="any")
    names = [g.name for g in resolve_groups(s, netease_taxonomy(), "其他")]
    assert names == ["流行", "华语流行"]
    assert s.describe() == "自定义分组（2 组）（含该标签）"
    assert SplitSettings(groups=only(["流行"])).describe() == "只分出 流行（纯风格）"


def test_describe_and_roundtrip() -> None:
    s = SplitSettings(level=2, top_n=5, other=False)
    assert s.describe() == '按二级曲风（前 5 类，不建"其他"）'
    assert SplitSettings.from_dict(s.to_dict()) == s
    g = SplitSettings(groups=(Group("a", ("流行", "摇滚")),), match="any")
    assert SplitSettings.from_dict(g.to_dict()) == g

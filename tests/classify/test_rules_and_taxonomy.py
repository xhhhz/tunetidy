import pytest

from tests.classify.helpers import netease_taxonomy, tag_id
from tunetidy.classify.rules import apply_multilabel_rules
from tunetidy.classify.taxonomy import Taxonomy
from tunetidy.models import GenreTag, StyleNode

USAGE = ("原声带", "二次元", "儿童")
GENERIC = ("流行",)


@pytest.mark.parametrize(
    ("genres", "kept"),
    [
        ({"原声带", "流行"}, ("流行",)),  # 先去用途类，再去泛化：不能变成"原声带"
        ({"R&B", "流行"}, ("R&B",)),
        ({"原声带", "流行", "R&B"}, ("R&B",)),
        ({"儿童", "流行"}, ("流行",)),
        ({"二次元", "电子"}, ("电子",)),
        ({"原声带"}, ("原声带",)),  # 只有用途类：保留
        ({"原声带", "二次元"}, ("二次元", "原声带")),  # 只有用途类：都保留，进待确认
        ({"流行"}, ("流行",)),
        ({"R&B", "嘻哈说唱"}, ("R&B", "嘻哈说唱")),  # 真多风格：并列
        ({"R&B", "嘻哈说唱", "流行"}, ("R&B", "嘻哈说唱")),
        (set(), ()),
    ],
)
def test_multilabel_rules(genres: set[str], kept: tuple[str, ...]) -> None:
    assert apply_multilabel_rules(genres, USAGE, GENERIC).kept == kept


def test_rules_report_what_was_dropped() -> None:
    r = apply_multilabel_rules({"原声带", "流行", "R&B"}, USAGE, GENERIC)
    assert r.dropped_usage == ("原声带",)
    assert r.dropped_generic == ("流行",)


def test_rule_lists_are_configurable() -> None:
    assert apply_multilabel_rules({"原声带", "流行"}, (), ()).kept == ("原声带", "流行")
    assert apply_multilabel_rules({"摇滚", "流行"}, (), ("摇滚",)).kept == ("流行",)


def test_taxonomy_matches_m0() -> None:
    tax = netease_taxonomy()
    assert len(tax.nodes) == 648
    assert len(tax.level1_names()) == 28
    assert tax.version.startswith("netease-l1-")


@pytest.mark.parametrize(
    ("name", "root"),
    [("流行-华语流行", "流行"), ("原声带-综艺原声", "原声带"), ("R&B", "R&B")],
)
def test_root_name_by_tag_id(name: str, root: str) -> None:
    tax = netease_taxonomy()
    assert tax.root_name(GenreTag(tag_id(name), name)) == (root, True)


def test_root_name_level3() -> None:
    tax = netease_taxonomy()
    l3 = next(n for n in tax.nodes.values() if n.level == 3)
    parent = tax.nodes[l3.parent_id or ""]
    grand = tax.nodes[parent.parent_id or ""]
    assert tax.root_name(GenreTag(l3.tag_id, "whatever")) == (grand.name, True)


def test_root_name_ignores_misleading_name() -> None:
    """按 tag_id 找祖先，不按名称拆分。"""
    tax = netease_taxonomy()
    assert tax.root_name(GenreTag(tag_id("摇滚"), "流行-假名字")) == ("摇滚", True)


def test_unknown_tag_id_falls_back_to_name_prefix() -> None:
    tax = netease_taxonomy()
    assert tax.root_name(GenreTag("99999999", "摇滚-新子类")) == ("摇滚", False)


def test_taxonomy_version_changes_with_tree() -> None:
    a = Taxonomy([StyleNode("1", "流行", None, 1)])
    b = Taxonomy([StyleNode("1", "流行", None, 1), StyleNode("2", "摇滚", None, 1)])
    assert a.version != b.version
    assert a.version == Taxonomy([StyleNode("1", "流行", None, 1)]).version


def test_taxonomy_rejects_dangling_parent_and_cycle() -> None:
    with pytest.raises(ValueError, match="父节点"):
        Taxonomy([StyleNode("2", "x", "1", 2)])
    with pytest.raises(ValueError, match="环"):  # 构造时就会算全路径，环在这里发现
        Taxonomy([StyleNode("1", "a", "2", 2), StyleNode("2", "b", "1", 2)])

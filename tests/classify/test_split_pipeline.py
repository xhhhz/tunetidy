"""按拆分方式分类：命中规则"等于或下级"、两种组写法、primary/any、二级、合并、前 N 类。"""

from __future__ import annotations

import random

import pytest

from tests.classify.helpers import netease_taxonomy, song, tags
from tunetidy.classify.pipeline import Assignment, classify_songs, summarize
from tunetidy.classify.split import Group, SplitError, SplitSettings, only
from tunetidy.config import ClassifyConfig
from tunetidy.models import CopyrightStatus, Song, SongTags

ONE = ClassifyConfig(min_size=1)


def run(
    library: dict[int, tuple[str, ...]],
    split: SplitSettings,
    cfg: ClassifyConfig = ONE,
) -> dict[int, tuple[str, str | None]]:
    songs = [song(i) for i in library]
    tag_map = {t.key: t for t in (tags(i, *g) for i, g in library.items())}
    out = classify_songs(songs, tag_map, netease_taxonomy(), cfg, split=split)
    return {int(a.key.id): (a.status, a.category) for a in out}


def ok(category: str) -> tuple[str, str | None]:
    return ("assigned", category)


PENDING = ("pending", None)


# ------------------------------------------------------------------ 组里写一级名："流行"

LIB_L1 = {
    1: ("流行-华语流行",),
    2: ("流行-欧美流行",),
    3: ("流行",),
    4: ("R&B",),
    5: ("R&B", "流行"),
    6: (),
    7: ("R&B", "嘻哈说唱"),
}


def test_group_level1_name_primary() -> None:
    r = run(LIB_L1, SplitSettings(groups=only(["流行"])))
    assert r == {
        1: ok("流行"),  # 华语流行在流行之下：命中
        2: ok("流行"),
        3: ok("流行"),
        4: ok("其他"),
        5: ok("其他"),  # primary：多标签规则去掉泛化的"流行"，剩 R&B
        6: PENDING,  # 无标签
        7: ok("其他"),  # 两个候选都落在"其他"：直接归入，不进待确认
    }


def test_group_level1_name_any() -> None:
    r = run(LIB_L1, SplitSettings(groups=only(["流行"]), match="any"))
    assert r[5] == ok("流行")  # any：带流行标签就命中
    assert {k: v for k, v in r.items() if k != 5} == {
        1: ok("流行"), 2: ok("流行"), 3: ok("流行"), 4: ok("其他"), 6: PENDING, 7: ok("其他"),
    }  # fmt: skip


# ------------------------------------------------------------------ 组里写全路径："流行-华语流行"

LIB_PATH = {
    1: ("流行-华语流行",),
    2: ("流行-欧美流行",),
    3: ("流行",),
    4: ("流行", "流行-华语流行"),
    5: ("R&B", "流行-华语流行"),
}


def test_group_full_path_primary() -> None:
    r = run(LIB_PATH, SplitSettings(groups=only(["流行-华语流行"])))
    assert r == {
        1: ok("华语流行"),
        2: ok("其他"),  # 欧美流行不在华语流行之下
        3: ok("其他"),  # 单独的"流行"是上级，不命中
        4: ok("华语流行"),  # 同一路径只留最具体的"华语流行"，不会被上级拉去"其他"
        5: ok("其他"),  # primary：R&B 与流行并列时去掉流行，剩 R&B
    }


def test_group_full_path_any() -> None:
    r = run(LIB_PATH, SplitSettings(groups=only(["流行-华语流行"]), match="any"))
    assert r == {
        1: ok("华语流行"),
        2: ok("其他"),
        3: ok("其他"),
        4: ok("华语流行"),
        5: ok("华语流行"),  # any：带华语流行标签就命中
    }


# ------------------------------------------------------------------ 多个组：书写顺序与并列


def test_first_matching_group_by_written_order() -> None:
    split = SplitSettings(
        groups=(Group("华语流行", ("流行-华语流行",)), Group("流行", ("流行",))), match="any"
    )
    r = run({1: ("流行-华语流行",), 2: ("流行-欧美流行", "R&B"), 3: ("R&B",)}, split)
    assert r == {1: ok("华语流行"), 2: ok("流行"), 3: ok("其他")}
    # 顺序反过来：上级组写在前面，就先接走所有流行
    rev = SplitSettings(groups=tuple(reversed(split.groups)), match="any")
    assert run({1: ("流行-华语流行",)}, rev) == {1: ok("流行")}


def test_primary_candidates_in_different_groups_are_pending() -> None:
    split = SplitSettings(groups=(Group("R&B", ("R&B",)), Group("说唱", ("嘻哈说唱",))))
    r = run({1: ("R&B", "嘻哈说唱"), 2: ("R&B",), 3: ("摇滚", "电子")}, split)
    assert r == {1: PENDING, 2: ok("R&B"), 3: ok("其他")}


def test_unknown_group_genre_raises() -> None:
    with pytest.raises(SplitError, match="曲风树里没有"):
        run({1: ("流行",)}, SplitSettings(groups=only(["不存在"])))


# ------------------------------------------------------------------ 二级粒度


def test_level2_mapping() -> None:
    tax = netease_taxonomy()
    l3 = next(n for n in tax.nodes.values() if n.level == 3)
    l3_path = tax.path(l3.tag_id)
    l2_name = tax.chain(l3.tag_id)[1].name
    r = run(
        {
            1: ("流行-华语流行",),
            2: ("流行",),  # 只有一级标签
            3: ("流行-华语流行", "流行-欧美流行"),  # 两个二级并列
            4: ("流行", "流行-华语流行"),  # 同一路径：只留华语流行
            5: (l3_path,),  # 三级标签归到它的二级
        },
        SplitSettings(level=2),
    )
    assert r == {
        1: ok("华语流行"),
        2: ok("流行 · 其他"),
        3: PENDING,
        4: ok("华语流行"),
        5: ok(l2_name),
    }


def test_level2_small_categories_merge_upward_then_to_other() -> None:
    lib = {i: ("流行-华语流行",) for i in range(1, 5)}
    lib[5] = ("流行-欧美流行",)
    lib[6] = ("流行",)
    lib[7] = ("摇滚-流行摇滚",)
    # min_size=2：欧美流行(1) 并入"流行 · 其他"（与只有一级标签的 6 号凑成 2）；
    # 流行摇滚(1) 并入"摇滚 · 其他"仍只有 1 首，再并入"其他"
    r = run(lib, SplitSettings(level=2), ClassifyConfig(min_size=2))
    assert r[5] == r[6] == ok("流行 · 其他")
    assert r[7] == ok("其他")
    assert r[1] == ok("华语流行")
    out = classify_songs(
        [song(i) for i in lib],
        {t.key: t for t in (tags(i, *g) for i, g in lib.items())},
        netease_taxonomy(),
        ClassifyConfig(min_size=2),
        split=SplitSettings(level=2),
    )
    merged = {int(a.key.id): a.reason["merged_from"] for a in out}
    assert merged[5] == "欧美流行" and merged[7] == "流行摇滚"  # 记录最初的分类


# ------------------------------------------------------------------ 前 N 类


def test_top_n() -> None:
    lib: dict[int, tuple[str, ...]] = {}
    n = 0
    for genre, count in [("流行", 5), ("R&B", 4), ("摇滚", 3), ("电子", 3)]:
        for _ in range(count):
            n += 1
            lib[n] = (genre,)
    r = run(lib, SplitSettings(top_n=2))
    counts = {c: sum(v == ok(c) for v in r.values()) for c in ("流行", "R&B", "其他")}
    assert counts == {"流行": 5, "R&B": 4, "其他": 6}


def test_top_n_with_groups_and_min_size() -> None:
    split = SplitSettings(
        groups=(Group("流行", ("流行",)), Group("摇滚", ("摇滚",)), Group("电子", ("电子",))),
        top_n=1,
    )
    lib = {1: ("流行",), 2: ("流行",), 3: ("摇滚",), 4: ("电子",), 5: ("R&B",)}
    r = run(lib, split)
    assert [r[i] for i in range(1, 6)] == [ok("流行")] * 2 + [ok("其他")] * 3


# ------------------------------------------------------------------ 与顺序无关


def _random_library(rng: random.Random, n: int) -> tuple[list[Song], list[SongTags]]:
    pool = ["流行", "流行-华语流行", "流行-欧美流行", "R&B", "R&B-当代R&B", "嘻哈说唱",
            "摇滚", "摇滚-流行摇滚", "原声带-电影原声", "电子", "爵士", "国风"]  # fmt: skip
    songs, tag_list = [], []
    for i in range(n):
        status = rng.choice([CopyrightStatus.NORMAL] * 8 + [CopyrightStatus.UNAVAILABLE])
        songs.append(song(i, status))
        tag_list.append(tags(i, *rng.sample(pool, rng.choice([0, 1, 1, 2, 2, 3]))))
    return songs, tag_list


@pytest.mark.parametrize(
    "split",
    [
        SplitSettings(level=2),
        SplitSettings(groups=only(["流行"])),
        SplitSettings(groups=only(["流行-华语流行", "R&B"]), match="any", top_n=1),
        SplitSettings(level=2, top_n=3),
    ],
)
def test_split_results_independent_of_order(split: SplitSettings) -> None:
    rng = random.Random(7)
    songs, tag_list = _random_library(rng, 200)
    positions = {s.key: i for i, s in enumerate(songs)}
    tag_map = {t.key: t for t in tag_list}
    cfg = ClassifyConfig(min_size=5)
    base = classify_songs(songs, tag_map, netease_taxonomy(), cfg, positions, split)
    for _ in range(30):
        shuffled = songs[:]
        rng.shuffle(shuffled)
        again = classify_songs(shuffled, tag_map, netease_taxonomy(), cfg, positions, split)
        assert again == base


def test_default_split_is_level1() -> None:
    lib = {1: ("流行-华语流行",), 2: ("R&B", "流行")}
    assert run(lib, SplitSettings()) == {1: ok("流行"), 2: ok("R&B")}
    s = summarize(
        [Assignment(song(1).key, 0, "assigned", "流行", 1.0, {})]  # 摘要不受拆分方式影响
    )
    assert s["categories"] == {"流行": 1}

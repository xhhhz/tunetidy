import random

import pytest

from tests.classify.helpers import netease_taxonomy, song, tags
from tunetidy.classify.pipeline import (
    Assignment,
    FirstPass,
    classify_songs,
    finish_classification,
    merge_small_categories,
    summarize,
)
from tunetidy.classify.split import Group, SplitSettings, only
from tunetidy.config import ClassifyConfig
from tunetidy.models import CopyrightStatus, Song, SongKey, SongTags

CFG = ClassifyConfig()


def run(
    songs: list[Song], tag_list: list[SongTags], cfg: ClassifyConfig = CFG
) -> dict[str, Assignment]:
    out = classify_songs(songs, {t.key: t for t in tag_list}, netease_taxonomy(), cfg)
    return {a.key.id: a for a in out}


def test_basic_outcomes() -> None:
    cfg = ClassifyConfig(min_size=1)
    songs = [song(i) for i in range(1, 8)] + [song(8, CopyrightStatus.UNAVAILABLE)]
    tag_list = [
        tags(1, "流行-华语流行"),
        tags(2, "原声带-综艺原声", "流行"),  # 去用途类 → 流行
        tags(3, "R&B-当代R&B", "流行-欧美流行"),  # 去泛化 → R&B
        tags(4, "R&B", "嘻哈说唱"),  # 真多风格 → 待确认
        tags(5),  # 无标签 → 待确认
        tags(6, "原声带-电影原声"),  # 只有用途类 → 原声带
        # 7：完全没有标签记录（如云盘歌曲）→ 待确认
        tags(8, "摇滚"),  # 下架：分类照算，但状态为无法加入
    ]
    r = run(songs, tag_list, cfg)
    assert (r["1"].status, r["1"].category, r["1"].confidence) == ("assigned", "流行", 1.0)
    assert (r["2"].category, r["2"].reason["official"]["dropped_usage"]) == ("流行", ["原声带"])
    assert (r["3"].category, r["3"].reason["official"]["dropped_generic"]) == ("R&B", ["流行"])
    assert (r["4"].status, r["4"].category, r["4"].reason["why"]) == (
        "pending",
        None,
        "multi_genre",
    )
    assert r["4"].reason["suggested"] == "R&B"
    assert (r["5"].status, r["5"].reason["why"]) == ("pending", "no_tags")
    assert (r["6"].status, r["6"].category) == ("assigned", "原声带")
    assert (r["7"].status, r["7"].reason["why"]) == ("pending", "no_tags")
    assert (r["8"].status, r["8"].category, r["8"].reason["why"]) == (
        "unavailable",
        "摇滚",
        "unavailable",
    )


def test_level1_dedup_counts_one_tag() -> None:
    """ "流行"和"华语流行"在一级粒度下只算一个"流行"。"""
    r = run(
        [song(1)], [tags(1, "流行", "流行-华语流行", "流行-欧美流行")], ClassifyConfig(min_size=1)
    )
    assert (r["1"].status, r["1"].category, r["1"].confidence) == ("assigned", "流行", 1.0)


def test_small_categories_merge_into_other() -> None:
    songs, tag_list = [], []
    n = 0
    for genre, count in [("流行", 12), ("摇滚", 10), ("爵士", 3), ("朋克", 1)]:
        for _ in range(count):
            n += 1
            songs.append(song(n))
            tag_list.append(tags(n, genre))
    # 下架和待确认的歌不计数：摇滚另有 5 首下架，也不能让 9 首变成够数
    for _ in range(5):
        n += 1
        songs.append(song(n, CopyrightStatus.UNAVAILABLE))
        tag_list.append(tags(n, "爵士"))
    s = summarize(classify_songs(songs, {t.key: t for t in tag_list}, netease_taxonomy(), CFG))
    assert s["categories"] == {"流行": 12, "摇滚": 10, "其他": 4}  # 等于 min_size 保留
    assert s["unavailable"] == 5


def test_merge_records_origin_and_rewrites_unavailable_too() -> None:
    a = [
        Assignment(SongKey("netease", "1"), 0, "assigned", "爵士", 1.0, {"merged_from": None}),
        Assignment(SongKey("netease", "2"), 1, "unavailable", "爵士", 1.0, {"merged_from": None}),
        Assignment(SongKey("netease", "3"), 2, "pending", None, 0.5, {"merged_from": None}),
    ]
    out = merge_small_categories(a, 10, "其他")
    assert [x.category for x in out] == ["其他", "其他", None]
    assert out[0].reason["merged_from"] == "爵士"


def test_other_is_kept_even_if_small() -> None:
    a = [Assignment(SongKey("netease", "1"), 0, "assigned", "其他", 1.0, {"merged_from": None})]
    assert merge_small_categories(a, 10, "其他")[0].category == "其他"


def test_duplicates_rejected() -> None:
    with pytest.raises(ValueError, match="重复"):
        run([song(1), song(1)], [])


def _random_library(rng: random.Random, n: int) -> tuple[list[Song], list[SongTags]]:
    pool = ["流行", "流行-华语流行", "R&B", "嘻哈说唱", "摇滚", "原声带-电影原声", "二次元",
            "电子", "爵士", "国风", "儿童"]  # fmt: skip
    songs, tag_list = [], []
    for i in range(n):
        status = rng.choice([CopyrightStatus.NORMAL] * 8 + [CopyrightStatus.VIP,
                            CopyrightStatus.UNAVAILABLE, CopyrightStatus.CLOUD])  # fmt: skip
        songs.append(song(i, status))
        if status != CopyrightStatus.CLOUD:
            tag_list.append(tags(i, *rng.sample(pool, rng.choice([0, 1, 1, 1, 2, 3]))))
    return songs, tag_list


def test_result_is_independent_of_processing_order() -> None:
    rng = random.Random(20261030)
    songs, tag_list = _random_library(rng, 300)
    positions = {s.key: i for i, s in enumerate(songs)}
    tag_map = {t.key: t for t in tag_list}
    base = classify_songs(songs, tag_map, netease_taxonomy(), CFG, positions)
    for _ in range(100):
        shuffled = songs[:]
        rng.shuffle(shuffled)
        tag_shuffled = dict(rng.sample(list(tag_map.items()), len(tag_map)))
        again = classify_songs(shuffled, tag_shuffled, netease_taxonomy(), CFG, positions)
        assert again == base


def test_output_sorted_by_position() -> None:
    songs = [song(3), song(1), song(2)]
    out = classify_songs(
        songs,
        {},
        netease_taxonomy(),
        CFG,
        {SongKey("netease", "1"): 0, SongKey("netease", "2"): 1, SongKey("netease", "3"): 2},
    )
    assert [a.key.id for a in out] == ["1", "2", "3"]


MIXED_SONGS = [song(i) for i in range(1, 14)] + [
    song(14, CopyrightStatus.UNAVAILABLE),
    song(15, CopyrightStatus.CLOUD),
]
MIXED_TAGS = [
    *(tags(i, "流行-华语流行") for i in range(1, 6)),
    *(tags(i, "R&B-当代R&B", "流行") for i in range(6, 9)),
    tags(9, "R&B", "嘻哈说唱"),
    tags(10, "摇滚"),
    tags(11, "电子-浩室舞曲"),
    tags(12, "电子"),
    tags(13),
    tags(14, "流行"),
]


@pytest.mark.parametrize(
    "split",
    [
        SplitSettings(),
        SplitSettings(level=2),
        SplitSettings(groups=only(["流行"])),
        SplitSettings(
            groups=(Group("律动", ("R&B", "嘻哈说唱")), Group(None, ("电子",))), match="any"
        ),
        SplitSettings(top_n=2, other=False),
    ],
)
def test_first_pass_then_finish_equals_classify_songs(split: SplitSettings) -> None:
    """逐首做第一遍、再整体收尾，和一次性分类的结果完全相同（进度页统计靠这一点）。"""
    cfg = ClassifyConfig(min_size=2)
    tax = netease_taxonomy()
    by_key = {t.key: t for t in MIXED_TAGS}
    whole = classify_songs(MIXED_SONGS, by_key, tax, cfg, split=split)
    fp = FirstPass(tax, cfg, split)
    # 逐首顺序打乱也一样：每首只看自己的标签
    order = list(enumerate(MIXED_SONGS))
    random.Random(1).shuffle(order)
    first = [fp.classify(s, by_key.get(s.key), i) for i, s in order]
    assert finish_classification(first, cfg, split) == whole

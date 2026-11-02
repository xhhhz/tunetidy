import pytest

from tunetidy.classify.scoring import WeightedSignal, decide


def sig(dist: dict[str, float], w: float = 1.0) -> WeightedSignal:
    return WeightedSignal("s", w, dist)


def test_single_genre_is_confident() -> None:
    d = decide([sig({"摇滚": 1.0})], 0.6, 0.1, 1.0)
    assert (d.category, d.confident, d.confidence) == ("摇滚", True, 1.0)


def test_two_genres_split_goes_pending() -> None:
    d = decide([sig({"R&B": 0.5, "嘻哈说唱": 0.5})], 0.6, 0.1, 1.0)
    assert d.confident is False
    assert d.confidence == 0.5
    assert d.category == "R&B"  # 同分按名称排序，结果确定


def test_no_signal() -> None:
    d = decide([sig({})], 0.6, 0.1, 1.0)
    assert (d.category, d.confident, d.confidence, d.scores) == (None, False, 0.0, {})


def test_threshold_is_inclusive_after_rounding() -> None:
    # 0.6 本身：确定
    assert decide([sig({"a": 0.6, "b": 0.4})], 0.6, 0.1, 1.0).confident
    # 浮点误差 0.59999999 四舍五入到 0.6：确定
    assert decide([sig({"a": 0.59999999, "b": 0.40000001})], 0.6, 0.1, 1.0).confident
    # 0.5994 → 0.599：不确定
    assert not decide([sig({"a": 0.5994, "b": 0.4006})], 0.6, 0.1, 1.0).confident


def test_tie_margin() -> None:
    assert not decide([sig({"a": 0.64, "b": 0.36}, w=1.0)], 0.6, 0.3, 1.0).confident
    assert decide([sig({"a": 0.65, "b": 0.35})], 0.6, 0.3, 1.0).confident


def test_evidence_strength_caps_confidence() -> None:
    # 只有权重 0.6 的信号可用、W₀=1：置信度上限 0.6
    d = decide([sig({"a": 1.0}, w=0.6), sig({}, w=1.0)], 0.6, 0.1, 1.0)
    assert d.confidence == 0.6 and d.confident
    d = decide([sig({"a": 1.0}, w=0.5)], 0.6, 0.1, 1.0)
    assert d.confidence == 0.5 and not d.confident


def test_weighted_merge_matches_design_example() -> None:
    # 官方"流行""摇滚"各 0.5（权重 1.0）+ 同专辑多数派摇滚（权重 0.6）→ 摇滚约 0.69
    d = decide([sig({"流行": 0.5, "摇滚": 0.5}, 1.0), sig({"摇滚": 1.0}, 0.6)], 0.6, 0.1, 1.0)
    assert d.category == "摇滚"
    assert d.confidence == pytest.approx(0.6875, abs=1e-3)
    assert d.confident

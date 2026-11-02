from pathlib import Path

import pytest

from tunetidy.config import Config, ConfigError, load_config, parse_config


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg == Config()
    assert cfg.network.proxy is None
    assert cfg.classify.usage_genres == ("原声带", "二次元", "儿童")
    assert cfg.classify.generic_genres == ("流行",)
    assert cfg.classify.min_size == 10
    assert cfg.apply.batch_size == 100


def test_overrides_from_toml(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        '[network]\nproxy = "http://127.0.0.1:7890"\nmin_interval = 1\n'
        '[classify]\nmin_size = 5\nusage_genres = ["原声带"]\n'
        "[apply]\nbatch_size = 50\nprivate = false\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.network.proxy == "http://127.0.0.1:7890"
    assert cfg.network.min_interval == 1
    assert cfg.classify.min_size == 5
    assert cfg.classify.usage_genres == ("原声带",)
    assert cfg.classify.generic_genres == ("流行",)  # 未覆盖的保持默认
    assert cfg.apply.batch_size == 50
    assert cfg.apply.private is False


@pytest.mark.parametrize(
    ("raw", "msg"),
    [
        ({"bogus": {}}, "未知配置段"),
        ({"network": {"prox": "x"}}, "未知配置项"),
        ({"apply": {"batch_size": 101}}, "batch_size"),
        ({"apply": {"batch_size": 1.5}}, "整数"),
        ({"apply": {"private": 1}}, "true/false"),
        ({"classify": {"usage_genres": "原声带"}}, "字符串列表"),
        ({"classify": {"threshold": 0}}, "threshold"),
        ({"classify": {"min_size": 0}}, "min_size"),
        ({"classify": {"official_weight": 0}}, "official_weight"),
        ({"network": {"min_interval": 3, "max_interval": 2}}, "min_interval"),
        ({"apply": {"name_template": "整理自 {source}"}}, "{category}"),
        ({"network": {"proxy": 7890}}, "字符串"),
    ],
)
def test_invalid_values_rejected(raw: dict[str, dict[str, object]], msg: str) -> None:
    with pytest.raises(ConfigError, match=msg.replace("{", r"\{").replace("}", r"\}")):
        parse_config(raw)


def test_invalid_toml(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("[network\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="TOML"):
        load_config(p)

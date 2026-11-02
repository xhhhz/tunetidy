"""配置：默认值 + 用户 TOML 覆盖。

配置文件位置：platformdirs 的用户配置目录下 config.toml（macOS 为
~/Library/Application Support/tunetidy/config.toml）。只读，不由程序写入。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

import platformdirs

APP_NAME = "tunetidy"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class NetworkConfig:
    proxy: str | None = None  # 默认直连，不继承系统代理；需要时显式填 http://host:port
    min_interval: float = 1.0  # 每个请求前的随机间隔（秒）
    max_interval: float = 2.0
    timeout: float = 20.0


@dataclass(frozen=True)
class ClassifyConfig:
    threshold: float = 0.6  # 置信度 >= 阈值即确定（比较前四舍五入到 3 位小数）
    tie_margin: float = 0.1  # 第一、第二名得分差小于它就进"待确认"
    official_weight: float = 1.0  # 信号权重：网易云官方曲风标签
    evidence_w0: float = 1.0  # 证据强度基准 W₀：可用信号权重和达到它即视为证据充分
    usage_genres: tuple[str, ...] = ("原声带", "二次元", "儿童")
    generic_genres: tuple[str, ...] = ("流行",)
    min_size: int = 10  # 一级粒度下不足这个数的分类并入"其他"
    other_name: str = "其他"


@dataclass(frozen=True)
class ApplyConfig:
    batch_size: int = 100  # 只测到 100
    batch_pause_min: float = 3.0
    batch_pause_max: float = 5.0
    name_template: str = "{category} · 整理自 {source}"
    private: bool = True


@dataclass(frozen=True)
class StorageConfig:
    tag_cache_days: int = 30


@dataclass(frozen=True)
class Config:
    network: NetworkConfig = field(default_factory=NetworkConfig)
    classify: ClassifyConfig = field(default_factory=ClassifyConfig)
    apply: ApplyConfig = field(default_factory=ApplyConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)


def config_path() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME)) / "config.toml"


def data_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME))


def _section(cls: type[Any], name: str, raw: dict[str, Any]) -> Any:
    base = cls()
    known = {f.name: f for f in fields(cls)}
    updates: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in known:
            raise ConfigError(f"[{name}] 未知配置项：{key}")
        default = getattr(base, key)
        if isinstance(default, tuple):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ConfigError(f"[{name}] {key} 应为字符串列表")
            value = tuple(value)
        elif isinstance(default, bool):
            if not isinstance(value, bool):
                raise ConfigError(f"[{name}] {key} 应为 true/false")
        elif isinstance(default, int | float) and not isinstance(default, bool):
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ConfigError(f"[{name}] {key} 应为数字")
            if isinstance(default, int) and not isinstance(value, int):
                raise ConfigError(f"[{name}] {key} 应为整数")
        elif default is None or isinstance(default, str):
            if not isinstance(value, str):
                raise ConfigError(f"[{name}] {key} 应为字符串")
        updates[key] = value
    return replace(base, **updates)


def _validate(cfg: Config) -> None:
    n, c, a = cfg.network, cfg.classify, cfg.apply
    if not 0 <= n.min_interval <= n.max_interval:
        raise ConfigError("[network] 需要 0 <= min_interval <= max_interval")
    if not 0 < c.threshold <= 1:
        raise ConfigError("[classify] threshold 应在 (0, 1] 内")
    if c.official_weight <= 0 or c.evidence_w0 <= 0:
        raise ConfigError("[classify] official_weight 与 evidence_w0 应大于 0")
    if c.min_size < 1:
        raise ConfigError("[classify] min_size 至少为 1")
    if not 1 <= a.batch_size <= 100:
        raise ConfigError("[apply] batch_size 应在 1–100 之间（只验证到 100）")
    if not 0 <= a.batch_pause_min <= a.batch_pause_max:
        raise ConfigError("[apply] 需要 0 <= batch_pause_min <= batch_pause_max")
    if "{category}" not in a.name_template:
        raise ConfigError("[apply] name_template 必须包含 {category}")


def parse_config(raw: dict[str, Any]) -> Config:
    sections = {
        "network": NetworkConfig,
        "classify": ClassifyConfig,
        "apply": ApplyConfig,
        "storage": StorageConfig,
    }
    unknown = set(raw) - set(sections)
    if unknown:
        raise ConfigError(f"未知配置段：{sorted(unknown)}")
    cfg = Config(**{name: _section(cls, name, raw.get(name, {})) for name, cls in sections.items()})
    _validate(cfg)
    return cfg


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} 不是合法的 TOML：{e}") from e
    return parse_config(raw)

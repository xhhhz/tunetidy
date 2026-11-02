"""版本号只有一个：pyproject.toml 与 tunetidy.__version__ 必须一致。"""

import tomllib
from pathlib import Path

import tunetidy

ROOT = Path(__file__).resolve().parents[1]


def test_version_matches_pyproject() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == tunetidy.__version__ == "1.0.0"

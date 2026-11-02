"""CLI 测试共用：假后端注入、伪造交互式终端、调用命令。"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.apply.test_writer import build_library
from tests.fakes import FakeBackend
from tunetidy import cli
from tunetidy.apply import approval
from tunetidy.config import ApplyConfig, ClassifyConfig, Config
from tunetidy.storage.db import connect

runner = CliRunner()
CONFIG = Config(
    classify=ClassifyConfig(min_size=2),
    apply=ApplyConfig(batch_size=2, batch_pause_min=0, batch_pause_max=0),
)


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    b = build_library()
    db = tmp_path / "t.db"
    monkeypatch.setattr(cli, "make_context", lambda: cli.Context(CONFIG, connect(db), b))
    return b


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approval, "_interactive", lambda: True)
    monkeypatch.setattr(approval, "new_code", lambda: "1234")


def invoke(*args: str, input: str | None = None) -> tuple[int, str]:
    r = runner.invoke(cli.app, list(args), input=input)
    return r.exit_code, r.output

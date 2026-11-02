"""评估数字：覆盖率、"没分好"比例、人工改动比例，按有标签 / 无标签两组。"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.apply.test_writer import build_library
from tests.test_analyze import CFG, Clock
from tunetidy.analyze import analyze, move_songs
from tunetidy.evaluate import evaluate, to_markdown
from tunetidy.models import SongKey
from tunetidy.plan import Plan
from tunetidy.storage.db import connect

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = connect(db)
    yield c
    c.close()


@pytest.fixture
def plan(conn: sqlite3.Connection) -> Plan:
    """17 首：16 首有曲风（其中 2 首多曲风并列、2 首下架），1 首云盘没有标签。"""
    b = build_library()
    return analyze(b, conn, CFG, [b.list_playlists()[0]], now=Clock())


def test_evaluate_counts(plan: Plan) -> None:
    r = evaluate(plan)
    assert r["total"] == 17
    assert r["tagged"]["songs"] == 16 and r["untagged"]["songs"] == 1
    assert r["coverage"] == pytest.approx(16 / 17)
    assert r["pending"] == pytest.approx(3 / 17)
    assert r["tagged"]["pending"] == pytest.approx(2 / 16)
    assert r["untagged"]["pending"] == pytest.approx(1.0)
    assert r["manual"] == 0


def test_manual_rate_counts_moves(conn: sqlite3.Connection, plan: Plan) -> None:
    moved = move_songs(
        conn, plan.id, [SongKey("netease", "1"), SongKey("netease", "17")], "R&B"
    ).plan
    r = evaluate(moved)
    assert r["manual"] == pytest.approx(2 / 17)
    assert r["tagged"]["manual"] == pytest.approx(1 / 16)
    assert r["untagged"]["manual"] == pytest.approx(1.0)


def test_markdown_has_no_song_names(plan: Plan) -> None:
    md = to_markdown(evaluate(plan))
    assert "94.1%" in md and "17" in md
    assert "s1" not in md  # 只有汇总数字，不含歌名


def test_script_reads_database_read_only(db: Path, plan: Plan) -> None:
    before = db.stat().st_mtime_ns
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "evaluate.py"), plan.id, "--db", str(db)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "94.1%" in out
    assert db.stat().st_mtime_ns == before

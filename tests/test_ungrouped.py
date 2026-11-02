"""不建"其他"歌单：未分组状态、迁移、内容哈希兼容、写回不发送、导出单列。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from pathlib import Path

import pytest

from tests.apply.test_writer import APPLY, build_library, writer
from tests.classify.helpers import netease_taxonomy, song, tags
from tests.test_analyze import CFG, Clock
from tunetidy.analyze import analyze
from tunetidy.classify.pipeline import classify_songs, summarize
from tunetidy.classify.split import SplitSettings, only
from tunetidy.config import ClassifyConfig
from tunetidy.export import export_plan
from tunetidy.models import CopyrightStatus
from tunetidy.plan import Plan
from tunetidy.storage import db, repo
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import MIGRATIONS, connect

# ------------------------------------------------------------------ 分类


def test_other_off_marks_ungrouped() -> None:
    lib = {1: ("流行",), 2: ("流行-华语流行",), 3: ("R&B",), 4: ("摇滚",), 5: (), 6: ("R&B",)}
    songs = [song(i) for i in range(1, 6)] + [song(6, CopyrightStatus.UNAVAILABLE)]
    tag_map = {t.key: t for t in (tags(i, *g) for i, g in lib.items())}
    out = classify_songs(
        songs, tag_map, netease_taxonomy(), ClassifyConfig(min_size=1),
        split=SplitSettings(groups=only(["流行"]), other=False),
    )  # fmt: skip
    r = {int(a.key.id): (a.status, a.category) for a in out}
    assert r == {
        1: ("assigned", "流行"),
        2: ("assigned", "流行"),
        3: ("ungrouped", "其他"),
        4: ("ungrouped", "其他"),
        5: ("pending", None),  # 无标签仍是待确认
        6: ("unavailable", "其他"),  # 下架仍是无法加入
    }
    assert summarize(out)["ungrouped"] == 2


def test_other_off_also_drops_merged_small_categories() -> None:
    lib = {i: ("流行",) for i in range(1, 4)}
    lib[4] = ("爵士",)  # 不足 min_size，并入"其他"后成为未分组
    out = classify_songs(
        [song(i) for i in lib],
        {t.key: t for t in (tags(i, *g) for i, g in lib.items())},
        netease_taxonomy(),
        ClassifyConfig(min_size=2),
        split=SplitSettings(other=False),
    )
    by_id = {int(a.key.id): a for a in out}
    assert by_id[4].status == "ungrouped" and by_id[4].reason["merged_from"] == "爵士"
    assert {by_id[i].status for i in (1, 2, 3)} == {"assigned"}


# ------------------------------------------------------------------ 迁移

UNGROUPED_ROW = (
    "INSERT INTO assignments VALUES ('p', 'netease', '2', 1, '其他', 'ungrouped', 1, '{}', 0)"
)


def test_migration_from_v3_keeps_rows_and_allows_ungrouped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "old.db"
    monkeypatch.setattr(db, "MIGRATIONS", MIGRATIONS[:3])
    old = connect(path)
    old.execute(
        "INSERT INTO plans VALUES ('p', 't', 'approved', '{}', 'v', '{}', '{}', 'h', 'h', 't')"
    )
    old.execute(
        "INSERT INTO assignments VALUES ('p', 'netease', '1', 0, '流行', 'assigned', 1.0, '{}', 0)"
    )
    with pytest.raises(sqlite3.IntegrityError):  # 旧表不认识 ungrouped
        old.execute(UNGROUPED_ROW)
    old.close()

    monkeypatch.setattr(db, "MIGRATIONS", MIGRATIONS)
    new = connect(path)
    assert new.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    rows = new.execute("SELECT song_id, category, status FROM assignments").fetchall()
    assert [tuple(r) for r in rows] == [("1", "流行", "assigned")]
    new.execute(UNGROUPED_ROW)
    indexes = {r[0] for r in new.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "assignments_by_category" in indexes
    new.close()


# ------------------------------------------------------------------ 内容哈希


def _old_hash(plan: Plan) -> str:
    """旧版的哈希算法（没有 ungrouped 字段）。"""

    def ids(items: list) -> list[list[str]]:  # type: ignore[type-arg]
        return [[a.key.platform, a.key.id] for a in sorted(items, key=lambda a: a.position)]

    content = {
        "platform": plan.platform,
        "sources": [s.id for s in plan.sources],
        "taxonomy": plan.taxonomy_version,
        "playlists": [
            {"category": p.category, "name": p.name, "songs": [[k.platform, k.id] for k in p.songs]}
            for p in plan.playlists()
        ],
        "pending": ids(plan.pending()),
        "unavailable": ids(plan.unavailable()),
    }
    text = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def test_hash_unchanged_for_plans_without_ungrouped(tmp_path: Path) -> None:
    """升级后旧方案的哈希不变，已批准的方案不会无故失效。"""
    conn = connect(tmp_path / "t.db")
    b = build_library()
    plan = analyze(b, conn, CFG, b.list_playlists(), now=Clock())
    assert plan.content_hash == _old_hash(plan)
    conn.close()


def test_hash_covers_ungrouped(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    b = build_library()
    split_on = SplitSettings(groups=only(["流行"]))
    split_off = SplitSettings(groups=only(["流行"]), other=False)
    on = analyze(b, conn, CFG, b.list_playlists(), now=Clock(), split=split_on)
    off = analyze(b, conn, CFG, b.list_playlists(), now=Clock(), split=split_off)
    assert off.ungrouped() and not on.ungrouped()
    assert on.content_hash != off.content_hash
    assert plan_store.load_plan(conn, off.id).content_hash == off.content_hash
    conn.close()


# ------------------------------------------------------------------ 写回与导出


def test_ungrouped_songs_are_never_written(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    b = build_library()
    plan = analyze(
        b, conn, CFG, [b.list_playlists()[0]], now=Clock(),
        split=SplitSettings(groups=only(["流行"]), other=False),
    )  # fmt: skip
    plan_store.record_approval(conn, plan.id, plan.content_hash, "t")
    result = writer(b, conn).execute(plan_store.load_plan(conn, plan.id))
    assert [name for name, _, _ in result.created] == ["流行 · 整理自 我喜欢的音乐"]
    ungrouped = {a.key.id for a in plan.ungrouped()}
    assert ungrouped  # R&B 那几首
    sent = {sid for w in b.writes if w[0] == "add_tracks" for sid in w[2]}
    assert not sent & ungrouped
    assert APPLY.batch_size == 2
    conn.close()


def test_export_lists_ungrouped(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    b = build_library()
    plan = analyze(
        b, conn, CFG, b.list_playlists(), now=Clock(),
        split=SplitSettings(groups=only(["流行"]), other=False),
    )  # fmt: skip
    songs = repo.get_songs(conn, [a.key for a in plan.assignments])
    md = export_plan(plan, songs, "md")
    n = len(plan.ungrouped())
    assert f"## 未分组（{n} 首，不写入）" in md and f"未分组 {n}" in md
    rows = list(csv.DictReader(io.StringIO(export_plan(plan, songs, "csv"))))
    ungrouped_rows = [r for r in rows if r["状态"] == "未分组"]
    assert len(ungrouped_rows) == n
    assert all('不建"其他"歌单' in r["依据"] and r["新歌单"] == "" for r in ungrouped_rows)
    conn.close()

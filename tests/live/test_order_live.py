"""真实账号上核对写回顺序：只读，不做任何写操作。

用法：用户自己执行一次 tunetidy apply（先不要 undo），然后
  uv run pytest -m live tests/live/test_order_live.py -s
核对最近一次已完成写回里的每个新歌单：歌单里的实际顺序必须与方案顺序一致。
"""

import pytest

from tunetidy.backends.web import create_backend
from tunetidy.config import load_config
from tunetidy.storage import runs as run_store
from tunetidy.storage.db import connect

pytestmark = pytest.mark.live


def test_latest_run_order_matches_plan() -> None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id FROM runs WHERE status = 'done' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("没有已完成且未撤销的写回；先运行 tunetidy apply")
        run = run_store.load_run(conn, row["id"])
        backend = create_backend(load_config())
        if backend.whoami() is None:
            pytest.skip("未登录，先运行 tunetidy login")
        for rp in run.playlists:
            assert rp.playlist_id, f"{rp.name} 没有建成"
            expected = [
                t.key.id for t in run_store.tracks(conn, run.id, rp.category) if t.status == "added"
            ]
            actual = list(backend.get_playlist(rp.playlist_id).track_ids)
            same = actual == expected
            print(f"{rp.name}：方案 {len(expected)} 首，歌单 {len(actual)} 首，顺序一致={same}")
            assert actual == expected, f"{rp.name} 顺序与方案不一致"
    finally:
        conn.close()

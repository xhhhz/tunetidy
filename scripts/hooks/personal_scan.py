"""本地防误提交：扫描暂存内容，发现作者的个人信息就拒绝提交。

真实值只在本机运行时读取、只在内存里比对，不写进仓库的任何文件，提示里也不回显：
- 本机数据库（只读）：来源歌单 id 与歌单名、"XX喜欢的音乐"里的昵称、理音新建的歌单 id
- 系统钥匙串里的登录态：用户 id、昵称、Cookie 值

装法：git config core.hooksPath scripts/hooks（scripts/hooks/pre-commit 会调用本脚本）。
没有数据库或没有登录的电脑上不拦截。确需跳过时用 git commit --no-verify。
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

MIN_LEN = 2  # 中文昵称常只有两三个字，再短的不比对
LIKED = "喜欢的音乐"


def _from_db(db_path: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {
        "歌单 id": set(),
        "歌单名": set(),
        "昵称": set(),
        "新建的歌单 id": set(),
    }
    if not db_path.exists():
        return out
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for pid, name in conn.execute(
            "SELECT DISTINCT json_extract(value, '$[0]'), json_extract(value, '$[1]')"
            " FROM plans, json_each(json_extract(plans.sources_json, '$.items'))"
        ):
            out["歌单 id"].add(str(pid))
            out["歌单名"].add(str(name))
            if str(name).endswith(LIKED):
                out["昵称"].add(str(name)[: -len(LIKED)])
        out["新建的歌单 id"] |= {
            str(r[0])
            for r in conn.execute(
                "SELECT playlist_id FROM run_playlists WHERE playlist_id IS NOT NULL"
            )
        }
    except sqlite3.Error:
        pass  # 库结构太旧或损坏：只用能读到的部分
    finally:
        conn.close()
    return out


def _from_login(fallback: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {"用户 id": set(), "昵称": set(), "登录凭据": set()}
    try:
        from tunetidy.backends.web.session import KeyringStore
    except ImportError:  # 扫码后端被删掉了
        return out
    session = KeyringStore(fallback).load()
    if session.user_id:
        out["用户 id"].add(session.user_id)
    if session.nickname:
        out["昵称"].add(session.nickname)
    out["登录凭据"] |= {v for v in session.cookies.values() if len(v) >= 16}
    return out


def collect_values(db_path: Path, fallback: Path) -> dict[str, set[str]]:
    values = _from_db(db_path)
    for kind, found in _from_login(fallback).items():
        values.setdefault(kind, set()).update(found)
    return {k: {v for v in vs if len(v) >= MIN_LEN} for k, vs in values.items()}


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def scan(repo: Path, values: dict[str, set[str]]) -> list[tuple[str, str]]:
    """返回 (文件, 信息类型) 列表：暂存区里每个新增或修改的文件，连同文件名一起比对。"""
    names = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split(b"\0")
    hits = []
    for raw in filter(None, names):
        path = raw.decode("utf-8", "replace")
        text = path + "\n" + _git(repo, "show", f":{path}").decode("utf-8", "replace")
        hits += [(path, kind) for kind, vs in values.items() if any(v in text for v in vs)]
    return hits


def main(repo: Path, db_path: Path, fallback: Path) -> int:
    values = collect_values(db_path, fallback)
    if not any(values.values()):
        print(
            "pre-commit：本机没有可比对的个人信息（没有数据库或没有登录），跳过检查",
            file=sys.stderr,
        )
        return 0
    hits = scan(repo, values)
    for path, kind in hits:
        print(f"✗ {path}：含有你的{kind}", file=sys.stderr)
    if hits:
        print(
            "已拒绝提交：请把这些值换成编的示例。真实值不会显示在这里；"
            "确认是误报时可以用 git commit --no-verify",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    from tunetidy.config import data_dir
    from tunetidy.storage.db import default_db_path

    top = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
    sys.exit(main(top, default_db_path(), data_dir() / "credentials" / "netease-web.json"))

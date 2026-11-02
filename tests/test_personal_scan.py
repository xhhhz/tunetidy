"""本地 pre-commit hook：从本机数据库和钥匙串读出个人信息，暂存内容命中就拒绝提交。"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import keyring
import pytest

from tests.apply.test_writer import APPLY, build_library
from tests.test_analyze import Clock
from tunetidy.analyze import analyze
from tunetidy.apply.writer import Writer
from tunetidy.backends.web.session import KEYRING_SERVICE, KEYRING_USER, Session
from tunetidy.config import ClassifyConfig, Config
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import connect

ROOT = Path(__file__).resolve().parents[1]
PLAYLIST_ID, NICK, UID, COOKIE = "5550001", "小测试", "77770001", "c" * 40


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "personal_scan", ROOT / "scripts/hooks/personal_scan.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def hook() -> ModuleType:
    return load_hook()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """本机库里有一份整理（来源歌单 5550001「小测试喜欢的音乐」）和一次写回建的歌单。"""
    path = tmp_path / "t.db"
    b = build_library()
    b.add_playlist(PLAYLIST_ID, f"{NICK}喜欢的音乐", b.get_tracks("100"))
    conn = connect(path)
    cfg = Config(classify=ClassifyConfig(min_size=2), apply=APPLY)
    plan = analyze(b, conn, cfg, [b.list_playlists()[1]], now=Clock())
    plan_store.record_approval(conn, plan.id, plan.content_hash, "t")
    Writer(b, conn, APPLY, sleep=lambda _s: None).execute(plan_store.load_plan(conn, plan.id))
    conn.close()
    return path


@pytest.fixture
def login() -> None:
    session = Session(cookies={"MUSIC_U": COOKIE}, user_id=UID, nickname=NICK)
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, session.to_json())
    yield
    keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)


def repo_with(tmp_path: Path, text: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "notes.md").write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "notes.md"], check=True)
    return repo


def test_collects_values_from_db_and_keyring(
    hook: ModuleType, db: Path, login: None, tmp_path: Path
) -> None:
    values = hook.collect_values(db, tmp_path / "no-fallback.json")
    assert PLAYLIST_ID in values["歌单 id"]
    assert f"{NICK}喜欢的音乐" in values["歌单名"]
    assert NICK in values["昵称"]
    assert UID in values["用户 id"]
    assert COOKIE in values["登录凭据"]
    assert values["新建的歌单 id"]  # 写回建的歌单


@pytest.mark.parametrize("leak", [PLAYLIST_ID, NICK, UID, COOKIE])
def test_blocks_staged_personal_info(
    hook: ModuleType,
    db: Path,
    login: None,
    tmp_path: Path,
    leak: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = repo_with(tmp_path, f"实测：tunetidy analyze {leak}\n")
    assert hook.main(repo, db, tmp_path / "no-fallback.json") == 1
    err = capsys.readouterr().err
    assert "notes.md" in err and leak not in err  # 提示里不回显真实值


def test_allows_clean_commit(hook: ModuleType, db: Path, login: None, tmp_path: Path) -> None:
    repo = repo_with(tmp_path, "实测：tunetidy analyze <歌单id>\n")
    assert hook.main(repo, db, tmp_path / "no-fallback.json") == 0


def test_without_local_data_it_does_not_block(hook: ModuleType, tmp_path: Path) -> None:
    """别人的电脑上没有数据库也没有登录：不拦截，只提示没有可比对的值。"""
    repo = repo_with(tmp_path, "随便什么内容\n")
    assert hook.main(repo, tmp_path / "missing.db", tmp_path / "no-fallback.json") == 0


def test_hook_reads_database_read_only(hook: ModuleType, db: Path, tmp_path: Path) -> None:
    before = db.stat().st_mtime_ns
    hook.collect_values(db, tmp_path / "no-fallback.json")
    assert db.stat().st_mtime_ns == before

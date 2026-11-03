"""SQLite 连接与表结构。

Web 服务、MCP 服务和 CLI 可能同时访问同一个库：开启 WAL，写操作走短事务，
并设置 busy_timeout，让并发写入排队而不是立即失败。
表结构变化通过 MIGRATIONS 追加，版本号记在 PRAGMA user_version。
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from tunetidy.config import data_dir

BUSY_TIMEOUT_MS = 5000

# 只追加，不修改已发布的条目
MIGRATIONS: list[str] = [
    """
    CREATE TABLE songs (
        platform     TEXT NOT NULL,
        id           TEXT NOT NULL,
        name         TEXT NOT NULL,
        artists_json TEXT NOT NULL,
        album_id     TEXT,
        album_name   TEXT,
        duration_ms  INTEGER,
        publish_year INTEGER,
        language     TEXT,
        bpm          INTEGER,
        copyright    TEXT NOT NULL
                     CHECK (copyright IN ('normal', 'vip', 'unavailable', 'cloud')),
        updated_at   TEXT NOT NULL,
        PRIMARY KEY (platform, id)
    );

    CREATE TABLE song_signals (
        platform   TEXT NOT NULL,
        song_id    TEXT NOT NULL,
        source     TEXT NOT NULL,
        tags_json  TEXT NOT NULL,
        raw_json   TEXT,
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (platform, song_id, source)
    );

    CREATE TABLE plans (
        id               TEXT PRIMARY KEY,
        created_at       TEXT NOT NULL,
        status           TEXT NOT NULL
                         CHECK (status IN ('draft', 'approved', 'applying', 'applied', 'undone')),
        sources_json     TEXT NOT NULL,
        taxonomy_version TEXT NOT NULL,
        settings_json    TEXT NOT NULL,
        snapshot_json    TEXT NOT NULL,
        content_hash     TEXT NOT NULL,
        approved_hash    TEXT,
        approved_at      TEXT
    );

    CREATE TABLE assignments (
        plan_id     TEXT NOT NULL REFERENCES plans (id),
        platform    TEXT NOT NULL,
        song_id     TEXT NOT NULL,
        position    INTEGER NOT NULL,
        category    TEXT,
        status      TEXT NOT NULL CHECK (status IN ('assigned', 'pending', 'unavailable')),
        confidence  REAL NOT NULL,
        reason_json TEXT NOT NULL,
        manual      INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (plan_id, platform, song_id)
    );

    CREATE TABLE runs (
        id         TEXT PRIMARY KEY,
        plan_id    TEXT NOT NULL REFERENCES plans (id),
        marker     TEXT NOT NULL UNIQUE,
        status     TEXT NOT NULL
                   CHECK (status IN ('running', 'interrupted', 'done', 'undoing', 'undone')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE run_playlists (
        run_id      TEXT NOT NULL REFERENCES runs (id),
        category    TEXT NOT NULL,
        name        TEXT NOT NULL,
        playlist_id TEXT,
        status      TEXT NOT NULL CHECK (status IN ('planned', 'created', 'filled', 'removed')),
        PRIMARY KEY (run_id, category)
    );

    CREATE TABLE run_tracks (
        run_id   TEXT NOT NULL,
        category TEXT NOT NULL,
        platform TEXT NOT NULL,
        song_id  TEXT NOT NULL,
        position INTEGER NOT NULL,
        status   TEXT NOT NULL CHECK (status IN ('pending', 'added', 'unaddable', 'removed')),
        batch    INTEGER,
        PRIMARY KEY (run_id, category, platform, song_id),
        FOREIGN KEY (run_id, category) REFERENCES run_playlists (run_id, category)
    );

    CREATE INDEX assignments_by_category ON assignments (plan_id, category);
    CREATE INDEX run_tracks_by_status ON run_tracks (run_id, status);
    """,
    """
    CREATE TABLE style_trees (
        platform   TEXT PRIMARY KEY,
        version    TEXT NOT NULL,
        nodes_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL
    );
    """,
    # 撤销时要判断"是否被设为公开"，需要知道建歌单时是不是私密的
    """
    ALTER TABLE run_playlists ADD COLUMN private INTEGER NOT NULL DEFAULT 1;
    """,
    # "其他"不建歌单时的新状态 ungrouped（未分组）。SQLite 不能修改 CHECK，只能重建表
    """
    CREATE TABLE assignments_new (
        plan_id     TEXT NOT NULL REFERENCES plans (id),
        platform    TEXT NOT NULL,
        song_id     TEXT NOT NULL,
        position    INTEGER NOT NULL,
        category    TEXT,
        status      TEXT NOT NULL
                    CHECK (status IN ('assigned', 'pending', 'unavailable', 'ungrouped')),
        confidence  REAL NOT NULL,
        reason_json TEXT NOT NULL,
        manual      INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (plan_id, platform, song_id)
    );
    INSERT INTO assignments_new
        SELECT plan_id, platform, song_id, position, category, status, confidence,
               reason_json, manual
        FROM assignments;
    DROP TABLE assignments;
    ALTER TABLE assignments_new RENAME TO assignments;
    CREATE INDEX assignments_by_category ON assignments (plan_id, category);
    """,
    # 首页按来源显示最近更新的那份整理；旧方案的更新时间取创建时间
    """
    ALTER TABLE plans ADD COLUMN updated_at TEXT;
    UPDATE plans SET updated_at = created_at;
    """,
    # 后台任务（分析、写回）：MCP 用；同一时间只允许一个 running
    """
    CREATE TABLE jobs (
        id           TEXT PRIMARY KEY,
        kind         TEXT NOT NULL CHECK (kind IN ('analyze', 'apply')),
        status       TEXT NOT NULL
                     CHECK (status IN ('running', 'done', 'failed', 'interrupted')),
        phase        TEXT,
        done         INTEGER NOT NULL DEFAULT 0,
        total        INTEGER NOT NULL DEFAULT 0,
        plan_id      TEXT,
        run_id       TEXT,
        error        TEXT,
        pid          INTEGER NOT NULL,
        heartbeat_at TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    );
    CREATE INDEX jobs_by_status ON jobs (status);
    """,
    # 网页撤销也走后台任务，撤销结果（删了哪些、只移除了哪些）存进 result。
    # SQLite 不能改 CHECK，只能重建表
    """
    CREATE TABLE jobs_new (
        id           TEXT PRIMARY KEY,
        kind         TEXT NOT NULL CHECK (kind IN ('analyze', 'apply', 'undo')),
        status       TEXT NOT NULL
                     CHECK (status IN ('running', 'done', 'failed', 'interrupted')),
        phase        TEXT,
        done         INTEGER NOT NULL DEFAULT 0,
        total        INTEGER NOT NULL DEFAULT 0,
        plan_id      TEXT,
        run_id       TEXT,
        error        TEXT,
        pid          INTEGER NOT NULL,
        heartbeat_at TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        result       TEXT
    );
    INSERT INTO jobs_new (id, kind, status, phase, done, total, plan_id, run_id, error, pid,
                          heartbeat_at, created_at, updated_at)
        SELECT id, kind, status, phase, done, total, plan_id, run_id, error, pid,
               heartbeat_at, created_at, updated_at
        FROM jobs;
    DROP TABLE jobs;
    ALTER TABLE jobs_new RENAME TO jobs;
    CREATE INDEX jobs_by_status ON jobs (status);
    """,
    # 写回记录记下建它的账号（平台的用户 id），"撤销上次那批"只找当前账号建的；旧记录为空
    """
    ALTER TABLE runs ADD COLUMN account_id TEXT;
    """,
]


def default_db_path() -> Path:
    return data_dir() / "tunetidy.db"


def connect(path: Path | None = None, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """打开数据库并迁移到最新版本。调用方负责关闭。

    check_same_thread=False 只给 Web 服务用：一个请求的连接可能在线程池的不同线程里
    打开、使用和关闭，但同一时间只被这一个请求使用。
    """
    path = path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path,
        timeout=BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
        check_same_thread=check_same_thread,
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    _enable_wal(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def _enable_wal(conn: sqlite3.Connection) -> None:
    """WAL 模式记录在库文件里，只需第一个连接切换一次。

    新建的库被多个连接同时打开时，切换 journal_mode 可能直接返回"database is locked"，
    不走 busy_timeout，所以这里自己重试到超时为止。
    """
    deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    while True:
        try:
            if conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
                return
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or time.monotonic() > deadline:
                raise
            time.sleep(0.02)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """短写事务：BEGIN IMMEDIATE 先拿写锁，避免并发时读后写升级失败。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _statements(script: str) -> list[str]:
    # 迁移脚本里只有 DDL，没有含分号的字符串或触发器，按分号拆分是安全的
    return [s.strip() for s in script.split(";") if s.strip()]


def migrate(conn: sqlite3.Connection) -> None:
    (current,) = conn.execute("PRAGMA user_version").fetchone()
    if current >= len(MIGRATIONS):
        return
    # 多个进程可能同时首次打开数据库：拿到写锁后重新读版本，避免重复建表；
    # 整个迁移在一个事务里，失败时不会留下半套表
    conn.execute("BEGIN IMMEDIATE")
    try:
        (current,) = conn.execute("PRAGMA user_version").fetchone()
        for version in range(current + 1, len(MIGRATIONS) + 1):
            for stmt in _statements(MIGRATIONS[version - 1]):
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {version}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

"""plans / assignments 的读写。"""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from tunetidy.classify.pipeline import Assignment
from tunetidy.models import SongKey
from tunetidy.plan import Plan, PlanStatus, Source
from tunetidy.storage.db import transaction


class PlanNotFoundError(LookupError):
    pass


def new_plan_id(now: datetime) -> str:
    """便于手敲：日期 + 4 位随机十六进制，如 20261030-1a2b。"""
    return f"{now:%Y%m%d}-{secrets.token_hex(2)}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def save_plan(conn: sqlite3.Connection, plan: Plan) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO plans (id, created_at, status, sources_json, taxonomy_version,
                               settings_json, snapshot_json, content_hash, approved_hash,
                               approved_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.id,
                plan.created_at,
                plan.status.value,
                json.dumps(
                    {"platform": plan.platform, "items": [[s.id, s.name] for s in plan.sources]},
                    ensure_ascii=False,
                ),
                plan.taxonomy_version,
                json.dumps(plan.settings, ensure_ascii=False, sort_keys=True),
                json.dumps(plan.snapshot, ensure_ascii=False, sort_keys=True),
                plan.content_hash,
                plan.approved_hash,
                plan.approved_at,
                plan.created_at,
            ),
        )
        _insert_assignments(conn, plan.id, plan.assignments)


def _insert_assignments(
    conn: sqlite3.Connection, plan_id: str, assignments: Sequence[Assignment]
) -> None:
    conn.executemany(
        """
        INSERT INTO assignments (plan_id, platform, song_id, position, category, status,
                                 confidence, reason_json, manual)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                plan_id,
                a.key.platform,
                a.key.id,
                a.position,
                a.category,
                a.status,
                a.confidence,
                json.dumps(a.reason, ensure_ascii=False, sort_keys=True),
                int(a.manual),
            )
            for a in assignments
        ],
    )


def load_plan(conn: sqlite3.Connection, plan_id: str) -> Plan:
    r = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if r is None:
        raise PlanNotFoundError(f"找不到方案 {plan_id}")
    src = json.loads(r["sources_json"])
    rows = conn.execute(
        "SELECT * FROM assignments WHERE plan_id = ? ORDER BY position", (plan_id,)
    ).fetchall()
    assignments = tuple(
        Assignment(
            key=SongKey(a["platform"], a["song_id"]),
            position=a["position"],
            status=a["status"],
            category=a["category"],
            confidence=a["confidence"],
            reason=json.loads(a["reason_json"]),
            manual=bool(a["manual"]),
        )
        for a in rows
    )
    # 内容哈希在 Plan 构造时按实际内容重算，不信任库里存的值
    return Plan(
        id=r["id"],
        created_at=r["created_at"],
        status=PlanStatus(r["status"]),
        platform=src["platform"],
        sources=tuple(Source(i, n) for i, n in src["items"]),
        taxonomy_version=r["taxonomy_version"],
        settings=json.loads(r["settings_json"]),
        snapshot=json.loads(r["snapshot_json"]),
        assignments=assignments,
        approved_hash=r["approved_hash"],
        approved_at=r["approved_at"],
    )


def list_plans(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT id, created_at, updated_at, status, sources_json, settings_json FROM plans"
        " ORDER BY updated_at DESC, created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return rows


class PlanStateError(RuntimeError):
    """方案当前状态不允许这个操作，或内容在确认期间被改动。"""


def record_approval(
    conn: sqlite3.Connection, plan_id: str, previewed_hash: str, approved_at: str
) -> None:
    """记录批准。在写事务里重新加载方案并核对哈希：批准的必须正是用户看过的那份预览。"""
    with transaction(conn):
        current = load_plan(conn, plan_id)
        if current.content_hash != previewed_hash:
            raise PlanStateError("方案内容在你确认期间发生了变化，请重新查看后再批准")
        if current.status not in (PlanStatus.DRAFT, PlanStatus.APPROVED):
            raise PlanStateError(f"方案状态为 {current.status.value}，不能批准")
        conn.execute(
            "UPDATE plans SET status = ?, approved_hash = ?, approved_at = ?, content_hash = ?,"
            " updated_at = ? WHERE id = ?",
            (
                PlanStatus.APPROVED.value,
                previewed_hash,
                approved_at,
                previewed_hash,
                _now(),
                plan_id,
            ),
        )


def transition(
    conn: sqlite3.Connection, plan_id: str, allowed_from: tuple[PlanStatus, ...], to: PlanStatus
) -> None:
    """条件更新状态；当前状态不在 allowed_from 里就报错（防止两个进程同时执行同一方案）。"""
    with transaction(conn):
        cur = conn.execute(
            f"UPDATE plans SET status = ?, updated_at = ? WHERE id = ? AND status IN "
            f"({', '.join('?' for _ in allowed_from)})",
            (to.value, _now(), plan_id, *(s.value for s in allowed_from)),
        )
        if cur.rowcount != 1:
            row = conn.execute("SELECT status FROM plans WHERE id = ?", (plan_id,)).fetchone()
            if row is None:
                raise PlanNotFoundError(f"找不到方案 {plan_id}")
            raise PlanStateError(f"方案状态为 {row['status']}，不能变为 {to.value}")


def replace_assignments(
    conn: sqlite3.Connection, plan: Plan, expected_hash: str, expected_status: PlanStatus
) -> None:
    """用重新分类的结果替换方案内容。写事务里先核对方案没在这期间被改动或执行。"""
    with transaction(conn):
        current = load_plan(conn, plan.id)
        if current.content_hash != expected_hash or current.status != expected_status:
            raise PlanStateError("方案在重新分类期间被修改或执行，请重新打开后再试")
        conn.execute("DELETE FROM assignments WHERE plan_id = ?", (plan.id,))
        _insert_assignments(conn, plan.id, plan.assignments)
        conn.execute(
            "UPDATE plans SET settings_json = ?, content_hash = ?, status = ?,"
            " approved_hash = ?, approved_at = ?, updated_at = ? WHERE id = ?",
            (
                json.dumps(plan.settings, ensure_ascii=False, sort_keys=True),
                plan.content_hash,
                plan.status.value,
                plan.approved_hash,
                plan.approved_at,
                _now(),
                plan.id,
            ),
        )

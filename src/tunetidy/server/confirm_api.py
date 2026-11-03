"""网页确认、建歌单、历史与撤销的接口。

确认码只由 ConfirmGate 生成并显示在启动理音的终端（或控制窗口）里；这里的接口只能申请码、
提交码，任何响应里都没有码。提交正确后先登记确认，再启动后台任务，网页轮询任务进度。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from tunetidy import jobs
from tunetidy.apply.approval import ApprovalError, approval_is_valid
from tunetidy.backends.base import MusicBackend, NotLoggedInError
from tunetidy.plan import Plan, PlanStatus
from tunetidy.server.api import Cfg, Conn
from tunetidy.server.confirm import Action, ConfirmGate, apply_summary, resume_summary
from tunetidy.storage import jobs as job_store
from tunetidy.storage import plans as plan_store

router = APIRouter(prefix="/api")

NOT_LOGGED_IN = "还没有登录网易云，或登录已失效：请先在终端运行 tunetidy login"


def gate(request: Request) -> ConfirmGate:
    g: ConfirmGate = request.app.state.gate
    return g


def backend_factory(request: Request) -> Callable[[], MusicBackend]:
    f: Callable[[], MusicBackend] = request.app.state.backend_factory
    return f


Gate = Annotated[ConfirmGate, Depends(gate)]
Backends = Annotated[Callable[[], MusicBackend], Depends(backend_factory)]


def logged_in(factory: Callable[[], MusicBackend]) -> MusicBackend:
    backend = factory()
    if backend.whoami() is None:
        raise NotLoggedInError(NOT_LOGGED_IN)
    return backend


@router.get("/confirm")
def confirm_status(g: Gate) -> dict[str, Any]:
    """网页确认是否可用；锁定时给出剩余秒数。"""
    return {"available": g.available, "locked_seconds": g.locked_seconds()}


# ------------------------------------------------------------------ 确认并建歌单


def _action(plan: Plan) -> Action:
    """草稿或已确认：新建；正在创建（上次中断）：继续建。其余状态不能确认。"""
    if plan.status in (PlanStatus.DRAFT, PlanStatus.APPROVED):
        if not plan.playlists():
            raise ApprovalError("按现在的分法没有歌能放进新歌单，无需确认")
        return "apply"
    if plan.status == PlanStatus.APPLYING:
        if plan.approved_hash != plan.content_hash:
            raise ApprovalError("这份整理在确认后变了，不能继续")
        return "resume"
    raise ApprovalError(f"这份整理已经{'建好' if plan.status == PlanStatus.APPLIED else '撤销'}了")


class ConfirmIn(BaseModel):
    expected_hash: str  # 页面看到的内容；不一致说明在别处被改过


class CodeIn(BaseModel):
    code: str = Field(max_length=20)


@router.post("/plans/{plan_id}/confirm")
def request_apply_code(
    plan_id: str, body: ConfirmIn, conn: Conn, cfg: Cfg, g: Gate
) -> dict[str, Any]:
    plan = plan_store.load_plan(conn, plan_id)
    if plan.content_hash != body.expected_hash:
        raise plan_store.PlanStateError("这份整理已在别处更新，请刷新后再试")
    action = _action(plan)
    private = cfg.apply.private
    summary = apply_summary(plan, private) if action == "apply" else resume_summary(plan, private)
    ticket = g.request(plan.id, action, plan.content_hash, summary)
    return {
        "action": action,
        "summary": summary,
        "expires_in": ticket.expires_in,
        "attempts_left": ticket.attempts_left,
    }


@router.post("/plans/{plan_id}/confirm/submit")
def submit_apply_code(
    plan_id: str, body: CodeIn, request: Request, conn: Conn, cfg: Cfg, g: Gate, factory: Backends
) -> dict[str, Any]:
    plan = plan_store.load_plan(conn, plan_id)
    action = _action(plan)
    backend = logged_in(factory)  # 没登录就先说清楚，不消耗确认码
    g.verify(plan.id, action, plan.content_hash, body.code)
    db_path = request.app.state.db_path
    if action == "resume":
        job_id, _ = jobs.start_apply(db_path, cfg, backend, plan.id, resume=True)
        return {"job_id": job_id}
    if not approval_is_valid(plan):
        # 与 CLI approve 同一个函数：写事务里再核对一次内容哈希
        now = datetime.now(UTC).isoformat(timespec="seconds")
        plan_store.record_approval(conn, plan.id, plan.content_hash, now)
    job_id, _ = jobs.start_apply(db_path, cfg, backend, plan.id)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, conn: Conn) -> dict[str, Any]:
    return jobs.job_summary(conn, job_store.load_job(conn, job_id))

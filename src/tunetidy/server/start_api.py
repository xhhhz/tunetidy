"""网页上开始一次整理要用的接口：扫码登录、账号状态、列歌单、开始读取、读取进度与取消。

这些都不写网易云账号（退出登录只删本机的登录态），所以不需要确认码；登录态由后端存进钥匙串，这里的响应只有昵称。
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tunetidy import jobs
from tunetidy.analyze import resolve_sources
from tunetidy.classify.split import SplitSettings
from tunetidy.server.api import Cfg, Conn
from tunetidy.server.confirm_api import Backends, logged_in
from tunetidy.server.login import LoginFlow
from tunetidy.storage import jobs as job_store

router = APIRouter(prefix="/api")


def login_flow(request: Request) -> LoginFlow:
    flow: LoginFlow = request.app.state.login
    return flow


Login = Annotated[LoginFlow, Depends(login_flow)]


@router.get("/me")
def me(factory: Backends) -> dict[str, Any]:
    user = factory().whoami()
    return {"logged_in": user is not None, "nickname": user.nickname if user else None}


@router.post("/login/start")
def start_login(flow: Login) -> dict[str, Any]:
    return asdict(flow.start())


@router.get("/login")
def login_state(flow: Login) -> dict[str, Any]:
    return asdict(flow.state())


@router.post("/logout")
def logout(conn: Conn, factory: Backends) -> dict[str, bool]:
    """退出登录：删掉本机保存的登录态，整理记录留在本机。后台任务还在运行时不退出：
    它手里的登录态会在查账号时写回钥匙串，等它结束再退。"""
    job_store.mark_orphans(conn, datetime.now(UTC))
    if job_store.running_job(conn) is not None:
        raise job_store.JobBusyError("有任务正在运行，等它结束后再退出登录")
    factory().logout()
    return {"ok": True}


@router.get("/playlists")
def list_playlists(factory: Backends) -> dict[str, Any]:
    """自己创建的和收藏的歌单，顺序与平台返回的一致（网易云把"我喜欢的音乐"排在最前）。"""
    return {
        "playlists": [
            {"id": p.id, "name": p.name, "track_count": p.track_count, "owned": p.owned}
            for p in logged_in(factory).list_playlists()
        ]
    }


class AnalyzeIn(BaseModel):
    playlists: list[str] = Field(min_length=1, max_length=20)  # 歌单 id，按勾选顺序


@router.post("/analyze")
def start_analyze(body: AnalyzeIn, request: Request, cfg: Cfg, factory: Backends) -> dict[str, Any]:
    """按勾选顺序合并这些歌单开始读取（先按大类分，拆法在结果页再改），立即返回任务 id。"""
    backend = logged_in(factory)
    sources = resolve_sources(backend.list_playlists(), body.playlists)
    job_id, _ = jobs.start_analyze(
        request.app.state.db_path, cfg, backend, sources, SplitSettings()
    )
    return {"job_id": job_id}


@router.get("/jobs/current")
def current_job(conn: Conn) -> dict[str, Any] | None:
    """正在运行的后台任务（首页显示"正在读取"用）；没有时为 null。"""
    job_store.mark_orphans(conn, datetime.now(UTC))
    job = job_store.running_job(conn)
    return jobs.job_summary(conn, job) if job else None


@router.post("/jobs/{job_id}/cancel", response_model=None)
def cancel_job(job_id: str) -> dict[str, bool] | JSONResponse:
    """取消这个网页开始的读取。建歌单和撤销不能取消（中途停下会留下半批歌单）。"""
    try:
        jobs.cancel_job(job_id)
    except ValueError as e:
        return JSONResponse({"detail": str(e), "code": "invalid_state"}, status_code=409)
    return {"ok": True}

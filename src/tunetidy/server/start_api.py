"""网页上开始一次整理要用的接口：扫码登录、账号状态、列歌单、开始读取。

这些都不写网易云账号，所以不需要确认码；登录态由后端存进钥匙串，这里的响应只有昵称。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from tunetidy import jobs
from tunetidy.analyze import resolve_sources
from tunetidy.classify.split import SplitSettings
from tunetidy.server.api import Cfg
from tunetidy.server.confirm_api import Backends, logged_in
from tunetidy.server.login import LoginFlow

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

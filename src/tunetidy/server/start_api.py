"""网页上开始一次整理要用的接口：扫码登录、账号状态。

登录不写网易云账号，所以不需要确认码；登录态由后端存进钥匙串，这里的响应只有昵称。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from tunetidy.server.confirm_api import Backends
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

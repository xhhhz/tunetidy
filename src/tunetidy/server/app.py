"""本地 Web 服务：审阅与调整整理方案。

网页可以确认、建歌单和撤销，但确认码只显示在启动理音的终端里（见 confirm.py）。
只监听 127.0.0.1 并不够，用户浏览器里的任何网页都能向本机端口发请求，
所以每个请求都要过三道检查：

- Host 头只能是 127.0.0.1 或 localhost 加本服务端口，挡住 DNS 重绑定；
- 带 Origin 头的请求只接受本服务自己的源；不发任何 CORS 允许头，跨站脚本读不到响应；
- /api/ 下的接口都要在自定义请求头里带上启动时生成的随机 token。跨站页面无法在不经
  CORS 预检的情况下加自定义头，所以它同时起到"写操作要求自定义请求头"的作用；
  写操作只注册为 POST。
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tunetidy.analyze import ConfirmResetError, MoveError, ReclassifyError
from tunetidy.apply.approval import ApprovalError
from tunetidy.apply.undo import UndoError
from tunetidy.backends import get_backend
from tunetidy.backends.base import BackendError, MusicBackend, NotLoggedInError
from tunetidy.classify.split import SplitError
from tunetidy.config import Config, ConfigError
from tunetidy.server.api import router
from tunetidy.server.confirm import Channel, ConfirmError, ConfirmGate
from tunetidy.server.confirm_api import router as confirm_router
from tunetidy.storage import jobs
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import connect
from tunetidy.storage.runs import RunNotFoundError

TOKEN_HEADER = "X-TuneTidy-Token"
LOCAL_HOSTS = ("127.0.0.1", "localhost")
STATIC_DIR = Path(__file__).parent / "static"  # web/ 的构建产物，不提交进仓库
BUILD_HINT = "cd web && npm ci && npm run build"


class MissingBuildError(RuntimeError):
    pass


def find_static(static_dir: Path | None = None) -> Path:
    """从源码运行而没有构建前端时直接报错，而不是返回空白页。"""
    static_dir = static_dir or STATIC_DIR
    if not (static_dir / "index.html").is_file():
        raise MissingBuildError(
            f"找不到前端构建产物（{static_dir}）。从源码运行时请先构建：{BUILD_HINT}"
        )
    return static_dir


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _error(status: int, message: str, code: str | None = None) -> JSONResponse:
    body = {"detail": message} if code is None else {"detail": message, "code": code}
    return JSONResponse(body, status_code=status)


def create_app(
    db_path: Path,
    token: str,
    port: int,
    config: Config | None = None,
    static_dir: Path | None = None,
    *,
    backend_factory: Callable[[], MusicBackend] | None = None,
    channel: Channel | None = None,
) -> FastAPI:
    """channel 是显示确认码的地方（启动理音的终端）；为 None 时网页确认不可用。"""
    # 关掉自动生成的接口文档：它们不在 /api/ 下，会绕过 token
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db_path = db_path
    conn = connect(db_path)
    try:  # 上次被关掉的进程留下的"进行中"任务，启动时标为已中断
        jobs.mark_orphans(conn, datetime.now(UTC))
    finally:
        conn.close()
    app.state.config = config or Config()
    cfg = app.state.config
    app.state.gate = ConfirmGate(channel)
    app.state.backend_factory = backend_factory or (lambda: get_backend("web", cfg))
    hosts = {f"{h}:{port}" for h in LOCAL_HOSTS}
    origins = {f"http://{h}" for h in hosts}
    expected = token.encode()

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.headers.get("host") not in hosts:
            return _error(403, "Host 不是本机地址")
        origin = request.headers.get("origin")
        if origin is not None and origin not in origins:
            return _error(403, "不接受跨站请求")
        if request.url.path.startswith("/api/"):
            given = request.headers.get(TOKEN_HEADER, "").encode()
            if not hmac.compare_digest(given, expected):
                return _error(401, "缺少或错误的访问口令，请用 tunetidy ui 打开的网址访问")
        response = await call_next(request)
        # 不许别的网站把本页面嵌进框架里诱导点击，也不把地址泄露给外链
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(plan_store.PlanNotFoundError)
    async def not_found(_: Request, e: Exception) -> JSONResponse:
        return _error(404, str(e))

    @app.exception_handler(ReclassifyError)
    async def not_editable(_: Request, e: Exception) -> JSONResponse:
        return _error(409, str(e), "not_editable")

    @app.exception_handler(ConfirmResetError)
    async def confirm_reset(_: Request, e: Exception) -> JSONResponse:
        return _error(409, str(e), "confirm_reset")

    @app.exception_handler(plan_store.PlanStateError)
    async def stale(_: Request, e: Exception) -> JSONResponse:
        return _error(409, str(e), "stale")

    for bad_input in (SplitError, ConfigError, MoveError):
        app.add_exception_handler(bad_input, lambda _r, e: _error(400, str(e), "invalid"))

    @app.exception_handler(ConfirmError)
    async def confirm_failed(_: Request, e: Exception) -> JSONResponse:
        assert isinstance(e, ConfirmError)
        status = {"unavailable": 403, "locked": 423, "rate_limited": 429}.get(e.kind, 409)
        body: dict[str, object] = {"detail": str(e), "code": e.kind}
        if e.retry_after is not None:
            body["retry_after"] = e.retry_after
        return JSONResponse(body, status_code=status)

    for refused in (ApprovalError, UndoError):
        app.add_exception_handler(refused, lambda _r, e: _error(409, str(e), "invalid_state"))
    app.add_exception_handler(jobs.JobBusyError, lambda _r, e: _error(409, str(e), "busy"))
    app.add_exception_handler(NotLoggedInError, lambda _r, e: _error(409, str(e), "not_logged_in"))
    app.add_exception_handler(BackendError, lambda _r, e: _error(502, str(e), "backend"))
    for missing in (jobs.JobNotFoundError, RunNotFoundError):
        app.add_exception_handler(missing, lambda _r, e: _error(404, str(e)))

    app.include_router(router)
    app.include_router(confirm_router)
    if static_dir is not None:
        _serve_frontend(app, find_static(static_dir))
    return app


def _serve_frontend(app: FastAPI, static_dir: Path) -> None:
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
    index = static_dir / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        # 前端自己处理 /、/plans/<id> 等路径；/api/ 下没有的接口照常 404
        if path == "api" or path.startswith("api/"):
            return _error(404, "没有这个接口")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

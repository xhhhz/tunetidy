"""命令行入口（Typer + Rich）。

approve、apply（含 --yes）、undo 只能在交互式终端里由用户本人执行；apply --dry-run 不写账号。
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn

import qrcode
import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

from tunetidy import __version__
from tunetidy.analyze import analyze as run_analyze
from tunetidy.analyze import resolve_sources
from tunetidy.apply import approval
from tunetidy.apply.approval import ApprovalError
from tunetidy.apply.undo import UndoError, undo_interactive
from tunetidy.apply.writer import ApplyError, Writer, dry_run_lines
from tunetidy.backends import UnknownBackendError, get_backend
from tunetidy.backends.base import BackendError, MusicBackend
from tunetidy.classify.split import SplitError, SplitSettings, load_split, only
from tunetidy.config import Config, ConfigError, load_config
from tunetidy.export import export_plan
from tunetidy.models import PlaylistInfo
from tunetidy.plan import Plan, PlanStatus
from tunetidy.storage import jobs, repo
from tunetidy.storage import plans as plan_store
from tunetidy.storage import runs as run_store
from tunetidy.storage.db import connect, default_db_path

app = typer.Typer(
    help="理音 TuneTidy：把网易云歌单按曲风拆成多个子歌单。非官方项目。",
    no_args_is_help=True,
    add_completion=False,
)
console = Console(highlight=False)

KNOWN_ERRORS = (
    ApprovalError,
    ApplyError,
    UndoError,
    BackendError,
    ConfigError,
    UnknownBackendError,
    plan_store.PlanNotFoundError,
    plan_store.PlanStateError,
    run_store.RunNotFoundError,
    SplitError,
)


@dataclass
class Context:
    config: Config
    conn: sqlite3.Connection
    backend: MusicBackend


def make_context() -> Context:
    config = load_config()
    return Context(config, connect(), get_backend("web", config))


@contextmanager
def session() -> Iterator[Context]:
    """打开配置、数据库与后端；已知错误只打印信息，不打印调用栈（避免泄露任何凭据）。"""
    try:
        ctx = make_context()
    except KNOWN_ERRORS as e:
        fail(str(e))
    try:
        # 上次被关掉的进程留下的"进行中"任务，在任何入口启动时标为已中断
        jobs.mark_orphans(ctx.conn, datetime.now(UTC))
        yield ctx
    except KNOWN_ERRORS as e:
        fail(str(e))
    finally:
        ctx.conn.close()


def fail(message: str) -> NoReturn:
    console.print(f"[red]✗ {escape(message)}[/red]")
    raise typer.Exit(1)


def ok(message: str) -> None:
    console.print(f"[green]✓[/green] {escape(message)}")


def say(line: str) -> None:
    console.print(escape(line))


# ------------------------------------------------------------------ login / playlists


def _show_qr(url: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)
    console.print("请用网易云音乐 App 扫码，并在手机上确认登录（3 分钟内有效）…")


@app.command()
def login() -> None:
    """扫码登录网易云。登录态存进系统钥匙串。"""
    with session() as ctx:
        user = ctx.backend.login(_show_qr)
        ok(f"已登录：{user.nickname}")


def playlists_table(rows: list[PlaylistInfo]) -> Table:
    """歌单 id 要能完整复制：id 列不换行、不截断；终端窄时让名称换行。"""
    table = Table()
    table.add_column("#", no_wrap=True)
    table.add_column("类型", no_wrap=True)
    table.add_column("歌曲数", no_wrap=True, justify="right")
    table.add_column("歌单 id", no_wrap=True, min_width=max((len(p.id) for p in rows), default=4))
    table.add_column("名称", overflow="fold")
    return table


@app.command()
def playlists() -> None:
    """列出你创建和收藏的歌单。"""
    with session() as ctx:
        rows = ctx.backend.list_playlists()
        table = playlists_table(rows)
        for i, p in enumerate(rows, start=1):
            table.add_row(
                str(i), "创建" if p.owned else "收藏", str(p.track_count), p.id, escape(p.name)
            )
        console.print(table)
        console.print(f"共 {len(rows)} 个。整理：tunetidy analyze <歌单 id 或名称>")


# ------------------------------------------------------------------ analyze / export


def print_plan_summary(plan: Plan) -> None:
    table = Table("将新建的歌单", "歌曲数")
    for p in plan.playlists():
        table.add_row(escape(p.name), str(len(p.songs)))
    console.print(table)
    console.print(
        f"待确认 {len(plan.pending())} 首、无法加入 {len(plan.unavailable())} 首"
        + (f"、未分组 {len(plan.ungrouped())} 首" if plan.ungrouped() else "")
        + "，不会写入。"
    )


class MatchMode(StrEnum):
    primary = "primary"
    any = "any"


def build_split(
    level: int | None,
    groups_file: Path | None,
    only_genres: list[str] | None,
    match: MatchMode | None,
    top: int | None,
    no_other: bool,
) -> SplitSettings:
    """把命令行参数合成拆分设置。命令行里的 --match/--top/--no-other 覆盖文件里的同名设置。"""
    if groups_file is not None and only_genres:
        raise SplitError("--groups 和 --only 只能二选一")
    if groups_file is not None:
        base = load_split(groups_file)
    else:
        base = SplitSettings(groups=only(only_genres) if only_genres else ())
    if level is not None and base.groups:
        raise SplitError("--level 只用于按一级或二级拆分，不能和 --groups、--only 一起用")
    if match is not None and not base.groups:
        raise SplitError("--match 只对自定义分组（--groups 或 --only）有效")
    return SplitSettings(
        level=2 if level == 2 else base.level,
        groups=base.groups,
        match=match.value if match is not None else base.match,
        top_n=top if top is not None else base.top_n,
        other=False if no_other else base.other,
    )


@app.command()
def analyze(
    sources: Annotated[list[str], typer.Argument(help="歌单 id 或完整名称，可多个（合并去重）")],
    level: Annotated[
        int | None, typer.Option("--level", min=1, max=2, help="按一级或二级曲风拆分（默认 1）")
    ] = None,
    groups_file: Annotated[
        Path | None, typer.Option("--groups", help="自定义分组 TOML（示例见 taxonomies/examples）")
    ] = None,
    only_genres: Annotated[
        list[str] | None,
        typer.Option("--only", help='只分出该曲风，其余归"其他"，可多次'),
    ] = None,
    match: Annotated[
        MatchMode | None,
        typer.Option("--match", help="primary 纯风格（默认）/ any 含该标签"),
    ] = None,
    top: Annotated[
        int | None, typer.Option("--top", min=1, help='只保留歌曲最多的 N 类，其余归"其他"')
    ] = None,
    no_other: Annotated[
        bool, typer.Option("--no-other", help='不建"其他"歌单，那些歌不写入')
    ] = False,
) -> None:
    """拉取歌曲与曲风标签，生成整理方案（不写账号）。"""
    try:
        split = build_split(level, groups_file, only_genres, match, top, no_other)
    except SplitError as e:
        fail(str(e))
    with session() as ctx:
        chosen = resolve_sources(ctx.backend.list_playlists(), sources)
        with Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        ) as bar:
            tasks: dict[str, int] = {}
            labels = {"tracks": "读取歌单", "tags": "获取曲风标签"}

            def progress(stage: str, done: int, total: int) -> None:
                if stage not in tasks:
                    tasks[stage] = int(bar.add_task(labels.get(stage, stage), total=total))
                bar.update(tasks[stage], completed=done, total=total)  # type: ignore[arg-type]

            plan = run_analyze(
                ctx.backend, ctx.conn, ctx.config, chosen, on_progress=progress, split=split
            )
        ok(f"已生成方案 {plan.id}（{split.describe()}）")
        print_plan_summary(plan)
        console.print(
            f"查看明细：tunetidy export {plan.id} --format md\n"
            f"批准：tunetidy approve {plan.id}（需在终端里亲自确认）"
        )


class ExportFormat(StrEnum):
    csv = "csv"
    json = "json"
    md = "md"


@app.command()
def export(
    plan_id: Annotated[str, typer.Argument(help="方案 id")],
    fmt: Annotated[ExportFormat, typer.Option("--format", "-f")] = ExportFormat.md,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="默认输出到屏幕")] = None,
) -> None:
    """导出方案；"待确认"和"无法加入"的歌单独列出。"""
    with session() as ctx:
        plan = plan_store.load_plan(ctx.conn, plan_id)
        songs = repo.get_songs(ctx.conn, [a.key for a in plan.assignments])
        text = export_plan(plan, songs, fmt.value)
        if output is None:
            typer.echo(text, nl=False)
        else:
            output.write_text(text, encoding="utf-8")
            ok(f"已导出到 {output}")


# ------------------------------------------------------------------ approve / apply / undo


@app.command()
def approve(plan_id: Annotated[str, typer.Argument(help="方案 id")]) -> None:
    """批准方案。只能在交互式终端里输入确认码完成。"""
    with session() as ctx:
        approval.approve_interactive(ctx.conn, plan_id, ask=input, say=say)


def _confirm_adopt(name: str, candidates: list[PlaylistInfo]) -> PlaylistInfo | None:
    say(
        f"发现 {len(candidates)} 个同名的空歌单「{name}」，没有本工具的标记，可能是上次中断留下的："
    )
    for i, c in enumerate(candidates, start=1):
        say(f"  {i}. {c.id}")
    answer = input("输入序号沿用它，直接回车则另建新歌单：").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(candidates):
        return candidates[int(answer) - 1]
    return None


def _writer(ctx: Context, on_progress: Callable[[str, int, int], None]) -> Writer:
    return Writer(
        ctx.backend,
        ctx.conn,
        ctx.config.apply,
        on_progress=on_progress,
        confirm_adopt=_confirm_adopt,
    )


@app.command()
def apply(
    plan_id: Annotated[str, typer.Argument(help="方案 id")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只打印将要做的事")] = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="跳过执行确认（仍需交互式终端，不能代替批准）")
    ] = False,
) -> None:
    """按已批准的方案新建私密歌单并写入；上次中断的会自动续传。"""
    with session() as ctx:
        plan = plan_store.load_plan(ctx.conn, plan_id)
        if dry_run:
            for line in dry_run_lines(plan, ctx.config.apply):
                say(line)
            return
        approval.require_interactive("执行写回")
        just_approved = False
        if plan.status == PlanStatus.DRAFT:
            plan = approval.approve_interactive(ctx.conn, plan_id, ask=input, say=say)
            just_approved = True
        resuming = plan.status == PlanStatus.APPLYING
        if resuming:
            say(f"方案 {plan.id} 上次没有执行完，将从中断处续传。")
            approval.require_interactive("续传写回")
            code = approval.new_code()
            if not yes and input(f"继续请输入 {code}（其他输入取消）：").strip() != code:
                fail("确认码不符，已取消")
        else:
            approval.confirm_execution(plan, yes=yes or just_approved, ask=input, say=say)

        with Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=console,
            transient=True,
        ) as bar:
            tasks: dict[str, int] = {}

            def progress(name: str, done: int, total: int) -> None:
                if name not in tasks:
                    tasks[name] = int(bar.add_task(escape(name), total=total))
                bar.update(tasks[name], completed=done, total=total)  # type: ignore[arg-type]

            w = _writer(ctx, progress)
            try:
                result = w.resume(plan) if resuming else w.execute(plan)
            except KeyboardInterrupt:
                fail(f"已中断。再次运行 tunetidy apply {plan.id} 可从中断处续传")
            except BackendError as e:
                fail(f"{e}。已写入的进度已保存，稍后再次运行 tunetidy apply {plan.id} 续传")

        for name, pid, count in result.created:
            ok(f"{name}（{pid}）：写入 {count} 首")
        if result.unaddable:
            console.print(f"[yellow]有 {result.unaddable} 首平台没有收下，已记为无法加入[/yellow]")
        for wmsg in result.warnings:
            console.print(f"[yellow]注意：{escape(wmsg)}[/yellow]")
        console.print(f"运行 id：{result.run_id}。撤销：tunetidy undo {result.run_id}")


@app.command()
def undo(
    target: Annotated[str, typer.Argument(help="运行 id，或方案 id（撤销它最近一次写回）")],
) -> None:
    """撤销一次写回：先回读对比；一致才删除歌单，否则只移除本工具加入的歌。"""
    with session() as ctx:
        run_id = target
        try:
            run_store.load_run(ctx.conn, target)
        except run_store.RunNotFoundError:
            plan_store.load_plan(ctx.conn, target)  # 不存在会报"找不到方案"
            latest = run_store.latest_run_for_plan(ctx.conn, target)
            if latest is None:
                fail(f"方案 {target} 还没有写回过，无需撤销")
            run_id = latest.id
        result = undo_interactive(ctx.backend, ctx.conn, run_id, ask=input, say=say)
        if result is None:
            return
        for name in result.deleted:
            ok(f"已删除「{name}」")
        for name, n in result.trimmed:
            ok(f"已从「{name}」移除本工具加入的 {n} 首，歌单保留")
        for wmsg in result.warnings:
            console.print(f"[yellow]注意：{escape(wmsg)}[/yellow]")


@app.command()
def ui(
    port: Annotated[int, typer.Option(help="本机端口")] = 8765,
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="不自动打开浏览器，改为打印带口令的网址")
    ] = False,
) -> None:
    """打开网页：审阅、调整、确认并建歌单、撤销（只监听本机；确认码显示在这个终端里）。"""
    import threading
    import webbrowser

    import uvicorn

    from tunetidy.server.app import MissingBuildError, create_app, find_static, new_token

    try:
        static = find_static()
        config = load_config()
    except (MissingBuildError, ConfigError) as e:
        fail(str(e))
    from tunetidy.server.confirm import TerminalChannel

    token = new_token()
    url = f"http://127.0.0.1:{port}/#token={token}"
    # 与 CLI approve 同一个判断：标准输入和输出都是终端时，确认码才显示在这里，网页确认才可用
    channel = TerminalChannel() if approval.is_interactive() else None
    server = create_app(default_db_path(), token, port, config, static, channel=channel)
    if no_browser:
        # 口令只在本次运行有效，只能看和改方案，不能批准或写回
        console.print(f"用浏览器打开：{url}", soft_wrap=True)  # 网址不能被折行
    else:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
        console.print(f"已在浏览器打开 http://127.0.0.1:{port}（没打开的话加 --no-browser 运行）")
    if channel is not None:
        console.print("网页确认：可用，确认码会显示在这个窗口里，只在理音网页里输入。")
    else:
        console.print("网页确认：不可用（不是在交互式终端里启动的），确认请用终端命令。")
    console.print("按 Ctrl+C 退出。")
    uvicorn.run(server, host="127.0.0.1", port=port, log_level="warning")


@app.command()
def mcp() -> None:
    """以 stdio 启动 MCP 服务，供 Claude Code、Codex 等 AI 助手调用（确认仍须在终端完成）。"""
    from tunetidy.backends import get_backend
    from tunetidy.mcp.server import build_server
    from tunetidy.mcp.tools import Tools

    # stdio 模式下 stdout 只能输出协议消息：报错写 stderr
    try:
        config = load_config()
    except ConfigError as e:
        print(f"tunetidy：{e}", file=sys.stderr)
        raise typer.Exit(1) from None
    db_path = default_db_path()
    conn = connect(db_path)
    try:
        jobs.mark_orphans(conn, datetime.now(UTC))
    finally:
        conn.close()
    tools = Tools(db_path, config, lambda: get_backend("web", config))
    build_server(tools).run("stdio")


@app.command()
def version() -> None:
    """显示版本号。"""
    typer.echo(f"tunetidy {__version__}")


if __name__ == "__main__":  # pragma: no cover
    app()

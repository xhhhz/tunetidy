"""把 tools.py 的五个工具注册成 MCP 服务（官方 SDK 的 MCPServer），以 stdio 通信。

stdio 模式下 stdout 只能输出协议消息：这里和工具里都不 print，日志写 stderr。
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from tunetidy.mcp.tools import Tools

INSTRUCTIONS = (
    "理音（TuneTidy）把网易云歌单按曲风拆成多个新的私密歌单。流程：create_plan 启动分析"
    "（首次 1000 首约 25 分钟）→ get_job_status 看进度和整理摘要 → get_plan_items 分页看明细 → "
    "用户本人在终端运行 tunetidy approve <整理 id> 确认 → apply_plan 建歌单 → get_job_status。"
    "确认只能由用户在终端完成，不要替用户确认；只新建歌单，不改动用户原有歌单。"
)

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def build_server(tools: Tools) -> MCPServer:
    server = MCPServer("tunetidy", instructions=INSTRUCTIONS)
    server.add_tool(
        tools.list_playlists,
        name="list_playlists",
        description="列出你创建和收藏的网易云歌单：id、名称、歌曲数、是否自己创建。",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    server.add_tool(
        tools.create_plan,
        name="create_plan",
        description=(
            "按曲风为一个或多个歌单生成整理（只读网易云、写本地，不改账号），立即返回任务 id。"
            "playlists：歌单 id 或完整名称；level：1 按大类、2 按细类；only：只分出这些曲风，"
            "其余进「其他」；match：primary 看主要曲风、any 带标签就算"
            "（只对 only、groups_toml 有效）；"
            "top_n：最多建几个歌单（不算「其他」）；other：是否给剩下的歌建「其他」；"
            "groups_toml：自定义分组，写法同 tunetidy analyze --groups。"
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=True
        ),
    )
    server.add_tool(
        tools.get_job_status,
        name="get_job_status",
        description=(
            "查看后台任务（分析或建歌单）的状态和进度；分析完成时附整理摘要，"
            "建歌单完成时附已建歌单。"
        ),
        annotations=READ,
    )
    server.add_tool(
        tools.get_plan_items,
        name="get_plan_items",
        description=(
            "分页查看一份整理的歌曲明细（每页默认 50 首，最多 200）。category：某个将新建的歌单；"
            "show：unsure 没分好的、ungrouped 不放进新歌单的、unavailable 加不进去的。"
        ),
        annotations=READ,
    )
    server.add_tool(
        tools.apply_plan,
        name="apply_plan",
        description=(
            "在用户的网易云账号里按整理新建私密歌单并加歌，立即返回任务 id。"
            "只接受用户已在终端运行 tunetidy approve 确认、且之后内容没变的整理。"
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True
        ),
    )
    return server

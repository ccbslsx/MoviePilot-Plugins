# -*- coding: utf-8 -*-
"""
GYingBTMagnet 的智能体工具

让 MoviePilot 的 AI 助手可以直接调用：
「帮我从 BT 站找一下《盗梦空间》并下载」→ bt_magnet_search
"""

from typing import List, Optional, Type

from pydantic import BaseModel, Field

from app.agent.tools.base import MoviePilotTool
from app.core.plugin import PluginManager


class GYingBTMagnetSearchInput(BaseModel):
    """工具入参模型。"""

    query: str = Field(..., description="影片或资源名称，例如：盗梦空间 Inception")
    add: bool = Field(True, description="是否把选中的磁力直接提交到下载器")


class GYingBTMagnetSearchTool(MoviePilotTool):
    """从 BT 导航站搜索磁力的工具。"""

    name: str = "bt_magnet_search"
    description: str = (
        "Search magnet links from the configured BT navigation site, rank candidates by "
        "seeder count, and optionally submit the best one to MoviePilot's downloader. "
        "Use this when the built-in indexer search finds nothing for a title."
    )
    args_schema: Type[BaseModel] = GYingBTMagnetSearchInput

    def get_tool_message(self, **kwargs) -> Optional[str]:
        query = kwargs.get("query", "")
        return f"正在 BT 导航站搜索：{query}"

    async def run(self, query: str, add: bool = True, **kwargs) -> str:
        plugin = PluginManager().running_plugins.get("GYingBTMagnet")
        if not plugin:
            return "GYingBTMagnet 插件未运行，请先在插件页启用该插件。"
        if not plugin.get_state():
            return "GYingBTMagnet 插件未启用。"

        rpa, download = await self.run_blocking("web", plugin.run_search, query, add)
        return plugin.format_report(query, rpa, download)


def get_tools() -> List[Type[MoviePilotTool]]:
    return [GYingBTMagnetSearchTool]

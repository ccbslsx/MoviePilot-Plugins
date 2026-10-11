# -*- coding: utf-8 -*-
"""
GYingBTMagnet 的智能体工具

让 MoviePilot 的 AI 助手可以直接调用。用户随口说「磁力搜索 盗梦空间」
「BT 找一下 沙丘 2」「搜个磁力」都能命中本工具。
"""

from typing import List, Optional, Type

from pydantic import BaseModel, Field

from app.agent.tools.base import MoviePilotTool
from app.core.plugin import PluginManager


class GYingBTMagnetSearchInput(BaseModel):
    """工具入参模型。"""

    query: str = Field(
        ...,
        description=(
            "要搜索的影片名或资源关键词，例如：盗梦空间、沙丘2、甄嬛传 S01、进击的巨人。"
            "只传片名/关键词本身，不要带「磁力」「BT」「找一下」「下载」这类词。"
        ),
    )
    add: bool = Field(
        True,
        description=(
            "是否把选出的磁力直接提交到下载器，默认 true。"
            "用户说「先看看」「只搜不下载」「有哪些资源」时传 false。"
        ),
    )


class GYingBTMagnetSearchTool(MoviePilotTool):
    """从 BT 导航站搜索磁力并下载的工具。"""

    name: str = "bt_magnet_search"
    description: str = (
        "【磁力搜索 / BT 找片子】从已配置的 BT 导航站聚合搜索影视资源，"
        "按做种数优选后把磁力提交给 MoviePilot 的下载器。"
        "只要用户的意图是「用磁力/BT 找片」或「下载磁力资源」就调用本工具，"
        "这类说法包括但不限于：磁力搜索、磁力找一下、磁力下载、磁力搜、搜磁力、找磁力、"
        "BT 搜索、BT 找一下、BT 下载、BT 搜、找 BT、搜个磁力、下个磁力。"
        "内置索引站（PT 站）搜不到、或者用户明确要磁力/种子时也要用它兜底。"
        "调用时把影片名原样放进 query，例如用户说「磁力搜索 盗梦空间」→ query='盗梦空间'。"
    )
    args_schema: Type[BaseModel] = GYingBTMagnetSearchInput

    def get_tool_message(self, **kwargs) -> Optional[str]:
        query = kwargs.get("query", "")
        return f"磁力搜索中：{query}"

    async def run(self, query: str, add: bool = True, **kwargs) -> str:
        plugin = PluginManager().running_plugins.get("GYingBTMagnet")
        if not plugin:
            return "磁力搜索插件未运行，请先在 MoviePilot 插件页启用「GYing 磁力导航搜索下载」。"
        if not plugin.get_state():
            return "磁力搜索插件未启用，请到插件页打开开关并保存。"

        rpa, download = await self.run_blocking("web", plugin.run_search, query, add)
        return plugin.format_report(query, rpa, download)


def get_tools() -> List[Type[MoviePilotTool]]:
    return [GYingBTMagnetSearchTool]

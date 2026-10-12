# -*- coding: utf-8 -*-
"""
GYingBTMagnet 的智能体工具

两步式交互，让 MoviePilot 的 AI 助手可以直接调用：
  1) bt_magnet_search  —— 说「磁力搜索 兰香如故」只返回候选列表，不下载；
  2) bt_magnet_download —— 说「下载第3个」只对列表第 3 个落下载器。

用户随口说「磁力搜索 盗梦空间」「BT找一下 沙丘2」「搜个磁力」「找 BT」都命中搜索工具。
"""

from typing import List, Optional, Type

from pydantic import BaseModel, Field

from app.agent.tools.base import MoviePilotTool
from app.core.plugin import PluginManager


class GYingBTMagnetSearchInput(BaseModel):
    """搜索工具入参模型。"""

    query: str = Field(
        ...,
        description=(
            "要搜索的影片名或资源关键词，例如：盗梦空间、沙丘2、甄嬛传 S01、进击的巨人、兰香如故。"
            "只传片名/关键词本身，不要带「磁力」「BT」「找一下」「下载」这类词。"
        ),
    )


class GYingBTMagnetSearchTool(MoviePilotTool):
    """从 BT 导航站搜索磁力候选列表（只搜不下）的工具。"""

    name: str = "bt_magnet_search"
    description: str = (
        "【磁力搜索 / BT 找片子（只返回候选列表，不下载）】从已配置的 BT 导航站聚合搜索影视资源，"
        "返回按做种数排序的候选列表，并等待用户挑选。默认绝不下载任何内容。"
        "只要用户的意图是「用磁力/BT 找片」「看看有什么资源」就调用本工具，"
        "这类说法包括但不限于：磁力搜索、磁力找一下、磁力搜、搜磁力、找磁力、"
        "BT搜索、BT找一下、BT搜、找BT、搜个磁力、下个磁力。"
        "注意：本工具只搜不下载，用户如果要下载会再发「下载第N个」触发 bt_magnet_download。"
        "调用时把影片名原样放进 query，例如用户说「磁力搜索 兰香如故」→ query='兰香如故'。"
    )
    args_schema: Type[BaseModel] = GYingBTMagnetSearchInput

    def get_tool_message(self, **kwargs) -> Optional[str]:
        query = kwargs.get("query", "")
        return f"磁力搜索中（仅列表，不下载）：{query}"

    async def run(self, query: str, **kwargs) -> str:
        plugin = PluginManager().running_plugins.get("GYingBTMagnet")
        if not plugin:
            return "磁力搜索插件未运行，请先在 MoviePilot 插件页启用「GYing 磁力导航搜索下载」。"
        if not plugin.get_state():
            return "磁力搜索插件未启用，请到插件页打开开关并保存。"

        result = await self.run_blocking("web", plugin.run_search_list, query)
        return plugin.format_list(query, result)


class GYingBTMagnetDownloadInput(BaseModel):
    """下载工具入参模型。"""

    index: int = Field(
        ...,
        description="要下载的候选序号（从 1 开始），对应上一次搜索返回的列表编号。例如用户说「下载第3个」→ index=3。",
    )
    query: str = Field(
        "",
        description="可选：本次要下载的资源关键词，用于汇报上下文；留空则用上一次搜索的片名。",
    )


class GYingBTMagnetDownloadTool(MoviePilotTool):
    """按搜索列表编号下载对应磁力的工具。"""

    name: str = "bt_magnet_download"
    description: str = (
        "【磁力下载（按编号）】下载最近一次磁力搜索结果中指定编号的磁力，只对那一个落下载器。"
        "用户说「下载第3个」「下第3个」「下载3」时调用本工具，index 填编号数字。"
        "必须先有过一次 bt_magnet_search 搜索、列表还在时才有效，编号超出范围会报错提示重搜。"
    )
    args_schema: Type[BaseModel] = GYingBTMagnetDownloadInput

    def get_tool_message(self, **kwargs) -> Optional[str]:
        index = kwargs.get("index", "")
        return f"准备下载第 {index} 个磁力…"

    async def run(self, index: int, query: str = "", **kwargs) -> str:
        plugin = PluginManager().running_plugins.get("GYingBTMagnet")
        if not plugin:
            return "磁力搜索插件未运行，请先在 MoviePilot 插件页启用「GYing 磁力导航搜索下载」。"
        if not plugin.get_state():
            return "磁力搜索插件未启用，请到插件页打开开关并保存。"

        result = await self.run_blocking("web", plugin.download_by_index, int(index))
        return plugin.format_download(result)


def get_tools() -> List[Type[MoviePilotTool]]:
    return [GYingBTMagnetSearchTool, GYingBTMagnetDownloadTool]

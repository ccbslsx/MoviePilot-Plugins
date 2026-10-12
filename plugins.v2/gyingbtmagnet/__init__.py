# -*- coding: utf-8 -*-
"""
GYing 磁力导航搜索下载 (GYingBTMagnet) —— MoviePilot V2 插件

把「导航站 → 镜像站遍历 → 登录 → 搜索 → 取磁力 → 落下载器」
这条链子搬进 MoviePilot，做成**两步式**交互：

* 第一步「搜索」：`/bt 兰香如故` / 智能体说「磁力搜索 兰香如故」——只返回编号候选列表，**绝不下载**；
* 第二步「下载」：`/bt 下载第3个` / 智能体说「下载第3个」——只对指定编号的磁力落下载器。

* 浏览器部分用 MP 自带的 CloakBrowser（`app.helper.browser.PlaywrightHelper.action`），
  不再自己带 playwright 依赖；
* 下载部分用 MP 自己的下载器模块（`app.helper.downloader.DownloaderHelper`），
  不再手搓 qB WebUI API。

入口：
* 远程命令  /bt 片名 | /bt 下载第N个
* 插件 API  GET /api/v1/plugin/GYingBTMagnet/search?query=片名（只搜）| /download?index=N（下载）
* 智能体工具 bt_magnet_search / bt_magnet_download
"""

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core.event import Event, eventmanager
from app.helper.downloader import DownloaderHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType

from .rpa import BTRpaFlow

__all__ = ["GYingBTMagnet"]


class GYingBTMagnet(_PluginBase):
    # ---------------------------------------------------------------- 元数据
    plugin_name = "GYing 磁力导航搜索下载"
    plugin_desc = "从 BT 导航站聚合搜索磁力，按做种数优选后直接交给 MoviePilot 的下载器。"
    plugin_icon = "https://raw.githubusercontent.com/ccbslsx/MoviePilot-Plugins/main/icons/gyingbtmagnet.png"
    plugin_version = "1.3.2"
    plugin_author = "ccbslsx"
    author_url = "https://github.com/ccbslsx"
    plugin_config_prefix = "gyingbtmagnet_"
    plugin_order = 50
    auth_level = 1

    # ------------------------------------------------------------ 默认配置
    _enabled: bool = False
    _nav_url: str = "https://www.xn--ykq321c.com/?count=1"
    _username: str = ""
    _password: str = ""
    _downloader: str = ""
    _save_path: str = ""
    _category: str = ""
    _tag: str = "MOVIEPILOT"
    _min_seeds: int = 1
    _max_sites: int = 6
    _timeout: int = 30
    _add_directly: bool = True

    def __init__(self):
        super().__init__()
        self._running_lock = threading.Lock()

    # ------------------------------------------------------------ 生命周期
    def init_plugin(self, config: dict = None):
        config = config or {}

        def _pick(key: str, current, caster=None):
            value = config.get(key)
            if value is None or value == "":
                return current
            try:
                return caster(value) if caster else value
            except Exception:
                return current

        self._enabled = bool(config.get("enabled"))
        self._nav_url = _pick("nav_url", self._nav_url)
        self._username = _pick("username", self._username)
        self._password = _pick("password", self._password)
        self._downloader = _pick("downloader", self._downloader)
        self._save_path = _pick("save_path", self._save_path)
        self._category = _pick("category", self._category)
        self._tag = _pick("tag", self._tag)
        self._min_seeds = _pick("min_seeds", self._min_seeds, int)
        self._max_sites = _pick("max_sites", self._max_sites, int)
        self._timeout = _pick("timeout", self._timeout, int)
        self._add_directly = config.get("add_directly") is not False

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        logger.info("[GYingBTMagnet] 插件已停止")

    # ------------------------------------------------------------ 远程命令
    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return [
            {
                "cmd": "/bt",
                "event": EventType.PluginAction,
                "desc": "磁力搜索/下载：/bt 片名 只返回候选列表不下；/bt 下载第3个 才下载",
                "category": "媒体搜索",
                "data": {"action": "gyingbtmagnet_search"},
            }
        ]

    # 编号下载指令识别：/bt 下载第3个 / 下载3 / 下第3个
    DOWNLOAD_RE = None  # 延迟编译，避免模块导入期 locale 问题

    @classmethod
    def parse_arg(cls, arg_str: str) -> Dict[str, Any]:
        """
        把 /bt 的参数拆成两种意图：
          - 下载：命中「下载第N个 / 下载N / 下第N个」→ {'mode':'download','index':N}
          - 搜索：其余全部当片名 → {'mode':'search','query': 片名}
        """
        import re

        raw = (arg_str or "").strip()
        if not raw:
            return {"mode": "help"}
        m = re.search(r"下载\s*第?\s*(\d+)\s*个?|下载\s*(\d+)|下\s*第?\s*(\d+)\s*个?", raw)
        if m:
            num = m.group(1) or m.group(2) or m.group(3)
            return {"mode": "download", "index": int(num)}
        # 剥掉「搜索/搜/找/查」等前缀词，避免污染搜索词
        query = re.sub(r"^(磁力\s*)?(搜索|搜|找|查|bt|BT)\s*[:：]?", "", raw).strip(" ，,。.、")
        return {"mode": "search", "query": query}

    @eventmanager.register(EventType.PluginAction)
    def command_handler(self, event: Event):
        if not event:
            return
        event_data = event.event_data or {}
        if event_data.get("action") != "gyingbtmagnet_search":
            return

        channel = event_data.get("channel")
        source = event_data.get("source")
        userid = event_data.get("user")
        arg_str = (event_data.get("arg_str") or "").strip()
        parsed = self.parse_arg(arg_str)

        if parsed["mode"] == "help":
            self.post_message(
                channel=channel,
                title="【磁力搜索】",
                text="用法：\n/bt 片名 —— 搜索并返回候选列表（不下载）\n/bt 下载第3个 —— 下载列表中第 3 个磁力",
                userid=userid,
            )
            return

        if parsed["mode"] == "download":
            threading.Thread(
                target=self._download_and_notify,
                args=(parsed["index"], channel, userid),
                daemon=True,
            ).start()
            return

        # 搜索模式
        query = parsed["query"]
        if not query:
            self.post_message(
                channel=channel,
                title="【磁力搜索】",
                text="没识别到片名。用法：/bt 片名（例如 /bt 兰香如故）",
                userid=userid,
            )
            return

        # 浏览器流程可能要跑一两分钟，扔到后台线程，别占着事件消费线程
        threading.Thread(
            target=self._search_and_notify,
            args=(query, channel, userid),
            daemon=True,
        ).start()

    def _search_and_notify(self, query: str, channel, userid):
        try:
            result = self.run_search_list(query)
        except Exception as err:
            logger.error(f"[GYingBTMagnet] 搜索异常：{err}")
            self.post_message(
                channel=channel,
                title=f"【磁力搜索】{query}",
                text=f"搜索出错：{err}",
                userid=userid,
            )
            return
        self.post_message(
            channel=channel,
            title=f"【磁力搜索】{query}",
            text=self.format_list(query, result),
            userid=userid,
        )

    def _download_and_notify(self, index: int, channel, userid):
        try:
            result = self.download_by_index(index)
        except Exception as err:
            logger.error(f"[GYingBTMagnet] 下载异常：{err}")
            self.post_message(
                channel=channel,
                title="【磁力下载】",
                text=f"下载出错：{err}",
                userid=userid,
            )
            return
        self.post_message(
            channel=channel,
            title=f"【磁力下载】第{index}个",
            text=self.format_download(result),
            userid=userid,
        )

    # ------------------------------------------------------------ 核心能力
    def run_search_list(self, query: str) -> Dict[str, Any]:
        """
        搜索 → 收集候选列表（不取磁力、不下载），存进插件 data 供「下载第N个」复用。

        :param query: 搜索关键词
        :return: {success, candidates:[{idx,href,site,seeds,text}], count, message, trace, elapsed}
        """
        started = time.time()
        result = self.fetch_list(query)
        elapsed = round(time.time() - started, 1)
        result["elapsed"] = elapsed
        if result.get("success"):
            candidates = result.get("candidates") or []
            # 按做种数降序编号，存入插件 data，供下载步骤按 index 取用
            for i, c in enumerate(candidates, 1):
                c["idx"] = i
            self.save_data("last_list", candidates)
            self.save_data("last_query", query)
            self.save_data("last_time", time.strftime("%Y-%m-%d %H:%M:%S"))
        return result

    def download_by_index(self, index: int) -> Dict[str, Any]:
        """按列表编号下载对应磁力。"""
        candidates = self.get_data("last_list") or []
        if not candidates:
            return {"success": False, "message": "还没有可下载的列表，请先 /bt 片名 搜索"}
        if index < 1 or index > len(candidates):
            return {
                "success": False,
                "message": f"编号超出范围（列表共 {len(candidates)} 个），用 /bt 下载第N个 重选",
            }
        cand = candidates[index - 1]
        magnet = self.fetch_single_magnet(cand.get("site"), cand.get("href"))
        if not magnet:
            return {"success": False, "message": "取磁力失败（详情页没解析到磁力链接）", "candidate": cand}
        download = self.add_to_downloader(magnet)
        self._record_history(
            self.get_data("last_query") or cand.get("text", ""),
            {"title": cand.get("text"), "seeds": cand.get("seeds"), "site": cand.get("site")},
            download,
        )
        return {"success": download.get("success"), "magnet": magnet, "candidate": cand,
                "download": download, "message": download.get("message")}

    def fetch_list(self, query: str) -> Dict[str, Any]:
        """用 MP 的 CloakBrowser 跑一遍导航站流程，只收集候选列表。"""
        from app.helper.browser import PlaywrightHelper

        flow = BTRpaFlow(
            nav_url=self._nav_url,
            username=self._username,
            password=self._password,
            min_seeds=self._min_seeds,
            max_sites=self._max_sites,
            page_timeout=self._timeout,
        )
        holder: Dict[str, Any] = {}

        def _callback(page):
            holder.update(flow.run_collect(page, query))
            return True

        try:
            PlaywrightHelper().action(
                url="about:blank",
                callback=_callback,
                headless=True,
                timeout=self._timeout,
            )
        except Exception as err:
            logger.error(f"[GYingBTMagnet] 浏览器流程异常：{err}")

        if not holder or not holder.get("candidates"):
            holder.setdefault("success", False)
            holder.setdefault("candidates", [])
            if not holder:
                # PlaywrightHelper.action 返回 None：说明 CloakBrowser 初始化/导航失败，
                # 异常被 action 外层吞掉了，这里给出明确提示，避免静默空结果
                holder["message"] = (
                    "浏览器未返回任何页面（CloakBrowser 可能未就绪或站点到不了），"
                    "请查看 MoviePilot 日志里的 [GYingBTMagnet] / CloakBrowser 报错"
                )
            else:
                holder["message"] = holder.get("message") or "没有搜到可用磁力（导航站/镜像当前可能不可用）"
        else:
            holder["success"] = True

        # 无论成败都把过程 trace 落盘，便于从插件 data 直接看卡在哪一步
        self.save_data("last_trace", holder.get("trace") or [])
        self.save_data("last_query", query)
        self.save_data("last_time", time.strftime("%Y-%m-%d %H:%M:%S"))
        return holder

    def fetch_single_magnet(self, site: str, href: str) -> Optional[str]:
        """针对已确定的详情页链接，单独开一次浏览器取磁力。"""
        from app.helper.browser import PlaywrightHelper

        flow = BTRpaFlow(
            nav_url=self._nav_url,
            username=self._username,
            password=self._password,
            min_seeds=self._min_seeds,
            max_sites=self._max_sites,
            page_timeout=self._timeout,
        )
        holder: Dict[str, Any] = {"magnet": None}

        def _callback(page):
            holder["magnet"] = flow.fetch_magnet(page, site or "", href or "")
            return True

        try:
            PlaywrightHelper().action(
                url="about:blank",
                callback=_callback,
                headless=True,
                timeout=self._timeout,
            )
        except Exception as err:
            logger.error(f"[GYingBTMagnet] 取磁力浏览器流程异常：{err}")
        return holder.get("magnet")

    def add_to_downloader(self, magnet: str) -> Dict[str, Any]:
        """通过 MoviePilot 的下载器帮助类提交任务。"""
        helper = DownloaderHelper()
        service = None
        try:
            if self._downloader:
                service = helper.get_service(name=self._downloader)
            if not service:
                services = helper.get_services() or {}
                service = next(iter(services.values()), None)
        except Exception as err:
            return {"success": False, "message": f"读取下载器配置失败：{err}"}

        if not service or not getattr(service, "instance", None):
            return {"success": False, "message": "没有找到可用的下载器，请先在「设定 → 下载器」里配置"}

        try:
            ret = service.instance.add_torrent(
                content=magnet,
                is_paused=False,
                download_dir=self._save_path or None,
                tag=self._tag or None,
                category=self._category or None,
            )
        except Exception as err:
            logger.error(f"[GYingBTMagnet] 添加下载任务失败：{err}")
            return {"success": False, "message": f"下载器报错：{err}", "downloader": service.name}

        ok = False
        if isinstance(ret, (tuple, list)):
            ok = bool(ret[0]) if ret else False
        else:
            ok = bool(ret)

        return {
            "success": ok,
            "message": "已提交下载任务" if ok else "下载器拒绝了这个任务",
            "downloader": service.name,
            "detail": str(ret)[:200],
        }

    # ------------------------------------------------------------ 结果整理
    @staticmethod
    def format_list(query: str, result: Dict[str, Any]) -> str:
        lines: List[str] = []
        candidates = result.get("candidates") or []
        if not result.get("success") or not candidates:
            lines.append(f"❌ {query}：没有搜到可用磁力")
            lines.append(f"   原因：{result.get('message') or '未知'}")
            trace = result.get("trace") or []
            if trace:
                lines.append("— 过程 —")
                lines.extend(trace[-6:])
            return "\n".join(lines)

        lines.append(f"🔍 {query}　共 {len(candidates)} 个候选（⚠️ 尚未下载）")
        for c in candidates:
            seeds = c.get("seeds")
            seed_txt = f"做种 {seeds}" if isinstance(seeds, int) and seeds >= 0 else "做种 -"
            lines.append(f"{c.get('idx')}. {c.get('text') or '(无标题)'} ｜ {seed_txt} ｜ {c.get('site') or ''}")
        lines.append("")
        lines.append("回复「下载第3个」（或 /bt 下载第3个）即可下载对应磁力；列表保留到下一次搜索前。")
        return "\n".join(lines)

    @staticmethod
    def format_download(result: Dict[str, Any]) -> str:
        lines: List[str] = []
        if not result.get("success"):
            lines.append(f"❌ {result.get('message')}")
            cand = result.get("candidate") or {}
            if cand:
                lines.append(f"   目标：第{cand.get('idx')}个 {cand.get('text')}")
            return "\n".join(lines)
        cand = result.get("candidate") or {}
        dl = result.get("download") or {}
        lines.append(f"⬇️ 已对第{cand.get('idx')}个提交下载：{cand.get('text')}")
        lines.append(f"   做种 {cand.get('seeds')} ｜ {dl.get('downloader')} ｜ {dl.get('message')}")
        lines.append(f"   磁力：{result.get('magnet')}")
        return "\n".join(lines)

    def _record_history(self, query: str, rpa: Dict[str, Any], download: Dict[str, Any]):
        try:
            history = self.get_data("history") or []
            if not isinstance(history, list):
                history = []
            history.insert(
                0,
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "query": query,
                    "title": rpa.get("title", ""),
                    "seeds": rpa.get("seeds"),
                    "site": rpa.get("site", ""),
                    "ok": bool(download.get("success")),
                },
            )
            self.save_data("history", history[:20])
        except Exception as err:
            logger.debug(f"[GYingBTMagnet] 写入历史失败：{err}")

    # ------------------------------------------------------------ API
    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/search",
                "endpoint": self.api_search,
                "methods": ["GET"],
                "auth": "apikey",
                "summary": "磁力搜索（只返回候选列表，不下载）",
                "description": "从导航站搜索磁力，返回按做种数降序的候选列表，不提交下载器。",
            },
            {
                "path": "/download",
                "endpoint": self.api_download,
                "methods": ["GET"],
                "auth": "apikey",
                "summary": "按编号下载候选",
                "description": "下载最近一次搜索结果中第 N 个候选的磁力，N 由 index 参数指定。",
            },
        ]

    def api_search(self, query: str, add: bool = False) -> Dict[str, Any]:
        if not self._enabled:
            return {"success": False, "message": "插件未启用"}
        if not query:
            return {"success": False, "message": "缺少 query 参数"}
        result = self.run_search_list(query)
        return {
            "success": result.get("success"),
            "count": len(result.get("candidates") or []),
            "candidates": result.get("candidates") or [],
            "message": result.get("message"),
        }

    def api_download(self, index: int = 1) -> Dict[str, Any]:
        if not self._enabled:
            return {"success": False, "message": "插件未启用"}
        return self.download_by_index(int(index or 1))

    # ------------------------------------------------------------ 智能体工具
    def get_agent_tools(self) -> List[Any]:
        try:
            from .agenttool import get_tools
        except Exception as err:
            logger.warn(f"[GYingBTMagnet] 智能体工具未注册：{err}")
            return []
        return get_tools()

    # ------------------------------------------------------------ 配置页
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        downloader_items = []
        try:
            downloader_items = list((DownloaderHelper().get_services() or {}).keys())
        except Exception:
            pass

        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用插件"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 8},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "add_directly",
                                            "label": "搜到就自动提交下载任务",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "nav_url",
                                            "label": "导航站地址",
                                            "placeholder": "https://www.xn--ykq321c.com/?count=1",
                                            "hint": "导航页里列出的镜像会被自动遍历",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {"model": "username", "label": "站点账号"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "password",
                                            "label": "站点密码",
                                            "type": "password",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "downloader",
                                            "label": "下载器（留空用默认）",
                                            "items": downloader_items,
                                            "clearable": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "save_path",
                                            "label": "保存路径（留空用下载器默认）",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {"model": "category", "label": "分类"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "tag",
                                            "label": "标签",
                                            "hint": "保持 MOVIEPILOT 可让自动整理接手",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "min_seeds",
                                            "label": "最低做种数",
                                            "type": "number",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "max_sites",
                                            "label": "最多尝试镜像数",
                                            "type": "number",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "timeout",
                                            "label": "单页超时（秒）",
                                            "type": "number",
                                        },
                                    }
                                ],
                            },
                        ],
                    }
                ],
            }
        ], {
            "enabled": False,
            "add_directly": True,
            "nav_url": "https://www.xn--ykq321c.com/?count=1",
            "username": "",
            "password": "",
            "downloader": "",
            "save_path": "",
            "category": "",
            "tag": "MOVIEPILOT",
            "min_seeds": 1,
            "max_sites": 6,
            "timeout": 30,
        }

    # ------------------------------------------------------------ 详情页
    def get_page(self) -> Optional[List[dict]]:
        history = self.get_data("history") or []
        if not isinstance(history, list) or not history:
            return [
                {
                    "component": "VAlert",
                    "props": {
                        "type": "info",
                        "variant": "tonal",
                        "text": "还没有记录。在消息渠道里发送 /bt 片名 试试。",
                    },
                }
            ]

        rows = [
            {
                "component": "VRow",
                "props": {"align": "center"},
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 3},
                        "content": [
                            {"component": "span", "text": item.get("time", "")},
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 3},
                        "content": [
                            {"component": "span", "text": item.get("query", "")},
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 4},
                        "content": [
                            {
                                "component": "span",
                                "text": f"{item.get('seeds')} 做种 · {(item.get('title') or '')[:40]}",
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 2},
                        "content": [
                            {
                                "component": "span",
                                "text": "已提交" if item.get("ok") else "未提交",
                            }
                        ],
                    },
                ],
            }
            for item in history[:10]
        ]
        return [
            {
                "component": "VCard",
                "props": {"variant": "tonal"},
                "content": [
                    {"component": "VCardTitle", "text": "最近搜索"},
                ],
            },
            {"component": "VRow", "content": rows},
        ]

# -*- coding: utf-8 -*-
"""
BT 导航站磁力抓取流程（浏览器 RPA 部分）

设计约束：
1. 只使用 PlaywrightHelper 回调里 page 对象的通用能力：
   goto / wait_for_load_state / click / fill / query_selector / evaluate / content / url
   不依赖 eval_on_selector_all、keyboard、wait_for_timeout 等非协议方法，兼容 CloakBrowser。
2. 所有 JS 片段都是无参立即执行表达式，需要注入的数据一律用 json.dumps 拼进源码，
   避免不同实现对 evaluate(expr, arg) 参数传递的差异。
3. 页面导航抖动会抛 "Execution context was destroyed"，统一走 _js() 重试。
"""

import json
import re
import time
from typing import Any, Dict, List, Optional

from app.log import logger

# 磁力链接（info hash 32 或 40 位）
MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[0-9A-Za-z]{32,40}[^\s\"'<>]*", re.I)

# 人机校验中的提示语
CHALLENGE_HINTS = ("正在计算", "安全验证", "验证中", "Checking your browser", "Just a moment")
# 镜像站挂掉的提示语
BROKEN_HINTS = ("502 Bad Gateway", "503 Service", "504 Gateway", "Bad Gateway", "服务暂时不可用")
# 导航站自身「全挂」的提示语（页面标题常为「挂了👻」）
DEAD_HINTS = ("挂了", "无法访问", "请更换", "部分节点", "节点无法访问", "没有可用", "全部失联")
# 未登录的提示语
LOGGED_OUT_HINTS = ("立即登录", "请先登录", "登录后")
# 登录按钮候选
LOGIN_BUTTON_HINTS = ("立即登录", "登录", "登陆", "Sign in", "Login")
# 提交按钮候选
SUBMIT_BUTTON_HINTS = ("登录", "登陆", "确定", "提交", "Sign in", "Login", "确认")


class BTRpaFlow:
    """一次「导航站 → 磁力」的完整搜索流程。"""

    def __init__(
        self,
        nav_url: str,
        username: str = "",
        password: str = "",
        min_seeds: int = 1,
        max_sites: int = 6,
        page_timeout: int = 30,
    ):
        self._nav_url = nav_url
        self._username = username or ""
        self._password = password or ""
        self._min_seeds = max(0, int(min_seeds or 0))
        self._max_sites = max(1, int(max_sites or 6))
        self._page_timeout = max(5, int(page_timeout or 30)) * 1000

    # ------------------------------------------------------------------ 对外
    def run_collect(self, page, query: str) -> Dict[str, Any]:
        """
        在已经拿到的 page 上跑完整流程，但**只收集候选列表、不取磁力、不下载**，
        供「两步式」交互的第一步使用。

        :param page: PlaywrightHelper 回调传入的页面对象
        :param query: 搜索关键词
        :return: {success, candidates:[{href,site,seeds,text}], message, trace, mirrors}
        """
        result: Dict[str, Any] = {
            "success": False,
            "candidates": [],
            "message": "",
            "trace": [],
            "mirrors": 0,
        }

        def note(msg: str):
            result["trace"].append(msg)
            logger.info(f"[GYingBTMagnet] {msg}")

        # 1. 打开导航站，收集镜像
        self._goto(page, self._nav_url)
        time.sleep(2)
        self._wait_for_body(page)  # SPA 首屏常是空壳，等正文渲染
        self._dismiss_overlays(page)
        body0 = self._body(page)
        if any(h in body0 for h in DEAD_HINTS):
            result["message"] = "导航站当前所有镜像不可达（页面显示「挂了/请更换」），稍后再试"
            note(result["message"])
            return result
        mirrors = self._collect_mirrors(page)
        result["mirrors"] = len(mirrors)
        if not mirrors:
            result["message"] = "导航站没有解析出任何可用镜像（SPA 可能未渲染或页面结构变化）"
            note(result["message"])
            return result
        note(f"发现 {len(mirrors)} 个镜像: " + ", ".join(m["name"] or m["href"] for m in mirrors[: self._max_sites]))

        collected: List[Dict[str, Any]] = []
        seen_href: set = set()

        # 2. 逐个镜像尝试，收集候选（不取磁力、不下载）
        for idx, mirror in enumerate(mirrors[: self._max_sites], 1):
            url = mirror["href"]
            note(f"[{idx}/{min(len(mirrors), self._max_sites)}] 尝试 {url}")
            try:
                self._goto(page, url)
            except Exception as err:
                note(f"  进站失败: {err}")
                continue

            self._wait_for_challenge(page)
            self._dismiss_overlays(page)
            body = self._body(page)
            if self._is_broken(body):
                note("  镜像异常（502/空页），换下一个")
                continue

            # 登录（分两种：正文提示未登录 / 页面标题栏有登录入口）
            if self._need_login(page, body):
                if not self._login(page):
                    note("  登录失败，换下一个镜像")
                    continue
                note("  登录完成")
                self._wait_for_challenge(page)
                self._dismiss_overlays(page)

            candidates = self._search_and_rank(page, url, query, note)
            if not candidates:
                note("  未解析到种子条目")
                continue

            for cand in candidates:
                href = cand.get("href")
                if not href or href in seen_href:
                    continue
                # min_seeds 过滤（seeds<0 视为未知，保留）
                if self._min_seeds > 0 and isinstance(cand.get("seeds"), int) \
                        and cand["seeds"] >= 0 and cand["seeds"] < self._min_seeds:
                    continue
                seen_href.add(href)
                collected.append({
                    "href": href,
                    "site": url,
                    "seeds": cand.get("seeds", -1),
                    "text": (cand.get("text") or "").strip(),
                })
            note(f"  本镜像累计候选 {len(collected)} 个")

        # 3. 按做种数降序，截取前 20（seeds<0 排末尾）
        collected.sort(
            key=lambda c: (c.get("seeds") if isinstance(c.get("seeds"), int) and c["seeds"] >= 0 else -1),
            reverse=True,
        )
        result["candidates"] = collected[:20]

        if not result["candidates"]:
            result["message"] = result["message"] or "所有镜像都没能拿到候选列表"
            note(result["message"])
        else:
            result["success"] = True
            note(f"✓ 共收集 {len(result['candidates'])} 个候选")
        return result

    # 公开包装：针对已确定的详情页链接取磁力（供下载步骤复用浏览器上下文）
    def fetch_magnet(self, page, mirror: str, href: str) -> Optional[str]:
        return self._fetch_magnet(page, mirror, href)

    # ------------------------------------------------------------ 基础动作
    def _goto(self, page, url: str):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self._page_timeout)
        except Exception:
            # domcontentloaded 超时也继续，后面靠 body 内容判断
            pass
        self._wait_idle(page)

    @staticmethod
    def _wait_idle(page):
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

    @staticmethod
    def _js(page, expr: str, default=None, tries: int = 3):
        """evaluate 包装：页面导航会让执行上下文失效，重试几次。"""
        for _ in range(max(1, tries)):
            try:
                return page.evaluate(expr)
            except Exception:
                time.sleep(0.8)
        return default

    def _body(self, page) -> str:
        text = self._js(page, "document.body ? document.body.innerText : ''", "")
        return text or ""

    def _dismiss_overlays(self, page):
        """移除弹窗遮罩，否则搜索框/按钮点不动。"""
        self._js(
            page,
            "(function(){var s='.popup-overlay,.popup-wrapper,.modal,.mask,"
            "[class*=overlay],[class*=popup],[class*=mask]';"
            "var els=document.querySelectorAll(s);"
            "for(var i=0;i<els.length;i++){try{els[i].style.display='none';els[i].remove();}catch(e){}}"
            "try{window.scrollTo(0,0);}catch(e){}return true;})()",
            None,
            tries=2,
        )
        time.sleep(0.6)

    def _wait_for_challenge(self, page, rounds: int = 10):
        """等 JS 人机校验跑完（CloakBrowser 一般能自动过）。"""
        for _ in range(rounds):
            body = self._body(page)
            if body and not any(h in body for h in CHALLENGE_HINTS):
                return
            time.sleep(2)

    @staticmethod
    def _is_broken(body: str) -> bool:
        if not body or not body.strip():
            return True
        return any(h in body for h in BROKEN_HINTS)

    def _wait_for_body(self, page, rounds: int = 25):
        """SPA 首屏常返回空壳，轮询等待正文渲染出来再解析。"""
        for _ in range(max(1, rounds)):
            if (self._body(page) or "").strip():
                return
            time.sleep(1)

    def _tag_element(self, page, selector_js: str, attr: str) -> bool:
        """用 JS 在页面上找元素并打标，再用稳定的属性选择器去点/填。"""
        expr = "(function(){%s})()" % selector_js.replace("%ATTR%", attr)
        return bool(self._js(page, expr, False, tries=2))

    # ------------------------------------------------------------ 镜像收集
    def _collect_mirrors(self, page) -> List[Dict[str, str]]:
        raw = self._js(
            page,
            "(function(){var out=[];var as=document.querySelectorAll('a[href]');"
            "for(var i=0;i<as.length;i++){var a=as[i];var h=a.href||'';"
            "if(h.indexOf('http')!==0)continue;"
            "out.push({t:(a.innerText||'').replace(/\\s+/g,' ').trim(),h:h});}return out;})()",
            [],
        ) or []
        nav_host = ""
        m = re.match(r"https?://([^/]+)", self._nav_url or "")
        if m:
            nav_host = m.group(1)
        mirrors: List[Dict[str, str]] = []
        seen = set()
        for item in raw:
            href = item.get("h") or ""
            text = item.get("t") or ""
            if nav_host and nav_host in href:
                continue
            if not href or href in seen:
                continue
            seen.add(href)
            mirrors.append({"name": text, "href": href})
        return mirrors

    # ------------------------------------------------------------ 登录
    def _need_login(self, page, body: str) -> bool:
        if any(h in body for h in LOGGED_OUT_HINTS):
            return True
        # 页面上存在可见的密码框，也认为需要登录
        return self._has_visible(page, 'input[type="password"]')

    def _has_visible(self, page, selector: str) -> bool:
        expr = (
            "(function(){var el=document.querySelector(%s);if(!el)return false;"
            "var r=el.getBoundingClientRect();return r.width>0&&r.height>0;})()"
            % json.dumps(selector, ensure_ascii=False)
        )
        return bool(self._js(page, expr, False, tries=2))

    def _login(self, page) -> bool:
        if not (self._username and self._password):
            logger.warn("[GYingBTMagnet] 未配置站点账号密码，跳过登录")
            return False

        # 打开登录弹窗
        if not self._has_visible(page, 'input[type="password"]'):
            for kw in LOGIN_BUTTON_HINTS:
                if self._click_by_text(page, kw):
                    time.sleep(2.5)
                    if self._has_visible(page, 'input[type="password"]'):
                        break

        if not self._has_visible(page, 'input[type="password"]'):
            logger.warn("[GYingBTMagnet] 没等到登录框")
            return False

        # 账号框：先用语义化选择器，再用 JS 兜底
        user_ok = False
        for sel in (
            'input#username',
            'input[name="username"]',
            'input[name="user"]',
            'input[name="account"]',
            'input[name*="user"]',
            'input[name*="account"]',
            'input[placeholder*="账号"]',
            'input[placeholder*="用户"]',
            'input[placeholder*="邮箱"]',
        ):
            if self._fill_visible(page, sel, self._username):
                user_ok = True
                break
        if not user_ok:
            tagged = self._tag_element(
                page,
                "var els=document.querySelectorAll('input');"
                "for(var i=0;i<els.length;i++){var el=els[i];"
                "var t=(el.getAttribute('type')||'text').toLowerCase();"
                "if(t!=='text'&&t!=='email'&&t!=='tel')continue;"
                "var r=el.getBoundingClientRect();if(r.width<=0||r.height<=0)continue;"
                "var hint=((el.getAttribute('name')||'')+' '+(el.id||'')+' '+(el.getAttribute('placeholder')||'')).toLowerCase();"
                "if(hint.indexOf('search')>=0||hint.indexOf('搜索')>=0)continue;"
                "el.setAttribute('%ATTR%','1');return true;}return false;",
                "data-mpbt-user",
            )
            user_ok = tagged and self._fill_visible(page, '[data-mpbt-user="1"]', self._username)
        if not user_ok:
            logger.warn("[GYingBTMagnet] 没找到账号输入框")
            return False

        # 密码框
        if not self._fill_visible(page, 'input[type="password"]', self._password):
            logger.warn("[GYingBTMagnet] 没找到密码输入框")
            return False

        # 提交
        submitted = False
        for kw in SUBMIT_BUTTON_HINTS:
            if self._click_by_text(page, kw):
                submitted = True
                break
        if not submitted:
            self._js(
                page,
                "(function(){var el=document.querySelector('input[type=password]');"
                "if(!el)return false;el.form&&el.form.submit();return true;})()",
                None,
                tries=1,
            )
        time.sleep(4)

        # 判定：密码框消失且不再提示登录
        if self._has_visible(page, 'input[type="password"]'):
            return False
        body = self._body(page)
        return not any(h in body for h in ("立即登录", "请先登录"))

    def _fill_visible(self, page, selector: str, value: str) -> bool:
        try:
            el = page.query_selector(selector)
            if not el or not el.is_visible():
                return False
            el.fill(value)
            return True
        except Exception:
            return False

    def _click_by_text(self, page, keyword: str) -> bool:
        """给文本匹配的可见元素打标，然后点击。"""
        tagged = self._tag_element(
            page,
            "var kw=%s;var els=document.querySelectorAll('a,button,input[type=submit],[role=button],span,div');"
            "for(var i=0;i<els.length;i++){var el=els[i];"
            "var t=(el.innerText||el.value||el.getAttribute('aria-label')||'').replace(/\\s+/g,'').trim();"
            "if(!t||t.length>12)continue;"
            "if(t.indexOf(kw)<0)continue;"
            "var r=el.getBoundingClientRect();if(r.width<=0||r.height<=0)continue;"
            "el.setAttribute('%%ATTR%%','1');return true;}return false;" % json.dumps(keyword, ensure_ascii=False),
            "data-mpbt-click",
        )
        if not tagged:
            return False
        try:
            page.click('[data-mpbt-click="1"]')
            return True
        except Exception:
            return False

    # ------------------------------------------------------------ 搜索 + 优选
    def _search_and_rank(self, page, mirror: str, query: str, note) -> List[Dict[str, Any]]:
        """返回按做种数降序的候选列表。"""
        from urllib.parse import quote

        # 方式一：直接走站点自己的搜索 URL（做种过滤 type=4）
        search_url = "%s/search?q=%s&type=4&mode=1" % (mirror.rstrip("/"), quote(query))
        note(f"  搜索URL {search_url}")
        try:
            self._goto(page, search_url)
        except Exception:
            pass

        links = self._collect_bt_links(page)
        if not links:
            # 方式二：在搜索框里输入再回车
            note("  URL 搜索没结果，改用搜索框")
            if self._fill_search_box(page, query):
                time.sleep(3)
                links = self._collect_bt_links(page)

        if not links:
            return []

        candidates = self._rank_rows(page)
        if not candidates:
            candidates = [{"href": h, "seeds": -1, "text": ""} for h in links]
        return candidates

    def _collect_bt_links(self, page) -> List[str]:
        for _ in range(10):
            links = self._js(
                page,
                "(function(){var out=[];var as=document.querySelectorAll('a[href^=\"/bt/\"],a[href*=\"/bt/\"]');"
                "for(var i=0;i<as.length&&out.length<12;i++){var h=as[i].getAttribute('href');"
                "if(h&&out.indexOf(h)<0)out.push(h);}return out;})()",
                [],
            ) or []
            if links:
                return links
            time.sleep(2)
        return []

    def _rank_rows(self, page) -> List[Dict[str, Any]]:
        """解析种子列表表格，按做种数降序。"""
        rows = self._js(
            page,
            "(function(){var rows=[];var trs=document.querySelectorAll('table.bit_list tr');"
            "for(var i=0;i<trs.length;i++){var tr=trs[i];"
            "var a=tr.querySelector('a.svg-tf[href^=\"/bt/\"]')||tr.querySelector('a[href^=\"/bt/\"]');"
            "if(!a)continue;"
            "var tds=tr.querySelectorAll('td');var seeds=-1;"
            "if(tds.length>=3){var s=parseInt((tds[2].innerText||'').trim(),10);if(!isNaN(s)&&s>=0&&s<1000000)seeds=s;}"
            "if(seeds<0){var txt=(tr.innerText||'').replace(/\\s+/g,' ');"
            "var m=txt.match(/(\\d+)\\s*(?:个)?\\s*(?:做种|种子|seeds)/i);if(m)seeds=parseInt(m[1],10);}"
            "rows.push({href:a.getAttribute('href'),seeds:seeds,"
            "text:(tr.innerText||'').replace(/\\s+/g,' ').trim().slice(0,200)});}"
            "rows.sort(function(x,y){return (y.seeds||0)-(x.seeds||0);});"
            "return rows.slice(0,20);})()",
            [],
        ) or []
        return [r for r in rows if r.get("href")]

    def _fill_search_box(self, page, query: str) -> bool:
        tagged = self._tag_element(
            page,
            "var els=document.querySelectorAll('input');"
            "for(var i=0;i<els.length;i++){var el=els[i];"
            "var t=(el.getAttribute('type')||'text').toLowerCase();"
            "if(t==='hidden'||t==='password'||t==='checkbox'||t==='radio'||t==='file')continue;"
            "var r=el.getBoundingClientRect();if(r.width<=0||r.height<=0)continue;"
            "el.setAttribute('%ATTR%','1');return true;}return false;",
            "data-mpbt-search",
        )
        if not tagged:
            return False
        try:
            page.fill('[data-mpbt-search="1"]', query)
        except Exception:
            return False
        # 表单提交（不依赖 keyboard.press）
        self._js(
            page,
            "(function(){var el=document.querySelector('[data-mpbt-search=\"1\"]');if(!el)return false;"
            "if(el.form){el.form.submit();return true;}"
            "var ev=new KeyboardEvent('keydown',{key:'Enter',keyCode:13,bubbles:true});el.dispatchEvent(ev);return true;})()",
            None,
            tries=1,
        )
        return True

    # ------------------------------------------------------------ 取磁力
    def _fetch_magnet(self, page, mirror: str, href: str) -> Optional[str]:
        target = href
        if target.startswith("/"):
            target = mirror.rstrip("/") + target
        try:
            self._goto(page, target)
        except Exception:
            return None
        time.sleep(2)

        for _ in range(8):
            mags = self._js(
                page,
                "(function(){var out=[];var as=document.querySelectorAll('a[href^=\"magnet:\"],input[value^=\"magnet:\"],textarea');"
                "for(var i=0;i<as.length;i++){var el=as[i];"
                "var v=el.getAttribute('href')||el.value||el.innerText||'';"
                "if(v&&v.indexOf('magnet:')===0&&out.indexOf(v)<0)out.push(v.trim());}return out;})()",
                [],
            ) or []
            if mags:
                return mags[0].split("\n")[0].strip()

            # 兜底：整页正则
            html = ""
            try:
                html = page.content() or ""
            except Exception:
                pass
            m = MAGNET_RE.search(html)
            if m:
                return m.group(0).strip()

            time.sleep(1.5)
        return None

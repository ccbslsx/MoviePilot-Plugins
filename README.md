# MoviePilot-Plugins

个人自用的 [MoviePilot](https://github.com/jxxghp/MoviePilot) **V2 插件市场仓库**。

把这个地址加进 MoviePilot 的插件市场配置，就能在市场里直接安装、升级下面的插件：

```
https://github.com/ccbslsx/MoviePilot-Plugins
```

## 插件列表

| 插件 ID | 名称 | 版本 | 说明 |
|---|---|---|---|
| [`GYingBTMagnet`](plugins.v2/gyingbtmagnet) | GYing 磁力导航搜索下载 | v1.3.0 | 从 BT 导航站聚合搜索磁力，两步式：先返回候选列表（不下载），再按编号下载 |

---

## 安装

MoviePilot → 设置 → 基础设置 → **插件市场**，在原有地址后面追加：

```
,https://github.com/ccbslsx/MoviePilot-Plugins
```

> ⚠️ 是**追加**不是覆盖。覆盖了官方和其他第三方市场的插件就都没了。

保存后进 插件 → 插件市场，搜索 `GYingBTMagnet` 或「磁力导航搜索下载」，点安装。

> 索引有 30 分钟缓存，没刷出来就重启一次 MoviePilot。
> 匿名调 GitHub API 每小时只有 60 次配额，装插件报 403 的话给 MP 配一个 `GITHUB_TOKEN`。

### 不走市场的直装方式（市场刷不出来时用）

把仓库 clone 到跑 Docker 的宿主机上，然后：

```bash
./tools/install.sh              # 自动找 moviepilot 容器
./tools/install.sh <容器名>
docker restart <容器名>
```

脚本做三件事：`docker cp` 插件到容器里的 `/app/app/plugins/gyingbtmagnet`、
把 `GYingBTMagnet` 写进 `UserInstalledPlugins`、提示重启。
容器重建（升级镜像）后需要重跑一次。

---

## GYingBTMagnet

把「BT 导航站 → 镜像站遍历 → 自动登录 → 搜索 → 取磁力 → 落下载器」
整条链路搬进 MoviePilot，并做成**两步式**交互：

- **第一步「搜索」**——`/bt 兰香如故` / 对 AI 说「磁力搜索 兰香如故」：只返回**按做种数排序的候选列表**，**绝不下载**；
- **第二步「下载」**——`/bt 下载第3个` / 对 AI 说「下载第3个」：只对列表中第 3 个的磁力落下载器。

列表会保留到下一次搜索前，下载时按编号取用，不需要重新搜索。

- **浏览器**用 MP 自带的 CloakBrowser（`app.helper.browser.PlaywrightHelper`），没有额外 pip 依赖
- **下载**走 MP 自己的 `DownloaderHelper`，复用你已配好的下载器和 `MOVIEPILOT` 标签
- 任务在 MP 的下载列表里可见可管，不是外挂的一套平行状态

### 三个入口

| 入口 | 用法 |
|---|---|
| 远程命令 | 在已绑定的通知渠道（Telegram / 微信 / QQ…）里发 `/bt 兰香如故` 搜、`/bt 下载第3个` 下 |
| 插件 API | `GET /api/v1/plugin/GYingBTMagnet/search?query=兰香如故&apikey=<APIKEY>`（只搜）；`GET /api/v1/plugin/GYingBTMagnet/download?index=3&apikey=<APIKEY>`（下载第3个） |
| 智能体工具 | 对 MP 的 AI 说「磁力搜索 兰香如故」「BT找一下 沙丘2」，要下载就说「下载第3个」 |

> **片名只是示例，换成你想找的就行。**
>
> ```
> 磁力搜索 盗梦空间          ← 只返回候选列表，不下载
> BT找一下 沙丘2
> BT搜索一下 甄嬛传
> 下载第3个                 ← 对上一次搜索返回的第 3 个候选落下载器
> ```
>
> 智能体工具拆成两个：`bt_magnet_search`（只搜不下）和 `bt_magnet_download`（按编号下），
> 所以**不需要在搜索时加「先别下载」「只搜」这类标记词**——默认搜索就不下载，下载是独立动作。

### 配置项

插件页 → 配置：

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | 关 | 启用插件 |
| `nav_url` | `https://www.xn--ykq321c.com/?count=1` | BT 导航站入口，镜像列表从这一页解析 |
| `username` / `password` | 空 | 镜像站的账号，用于自动登录（缺失时只做匿名搜索） |
| `downloader` | 空 | 下载器名称；留空 = 用 `DownloaderHelper` 里第一个可用实例 |
| `save_path` | 空 | 下载保存路径。留空 = 用下载器自己的默认目录 |
| `category` | 空 | 下载器分类（qB 用） |
| `tag` | `MOVIEPILOT` | 下载标签。**必须和 MP 的一致**，否则任务不会被 MP 接管整理 |
| `min_seeds` | `1` | 做种数低于该值的候选不进入列表 |
| `max_sites` | `6` | 最多尝试几个镜像站 |
| `timeout` | `30` | 单页等待超时（秒） |
| `add_directly` | 开 | 兼容老字段，新版本以「两步式」为准（搜索步骤永不自动下载） |

站点凭据只保存在 MP 的插件配置里，不进代码、不进日志。

---

## 仓库结构

```
.
├── package.v2.json                 # ← 插件市场索引，MP 从这里读插件列表
├── icons/
│   └── gyingbtmagnet.png
├── plugins.v2/
│   └── gyingbtmagnet/              # ← 目录名必须是插件 ID 的小写形式
│       ├── __init__.py             # 插件主体（_PluginBase 实现）
│       ├── rpa.py                  # 浏览器抓取流程（可脱离 MP 单测）
│       └── agenttool.py            # 智能体工具
├── tools/make_icon.py              # 生成图标（纯标准库，可删）
└── LICENSE                         # GPL-3.0
```

三处**硬性约定**，改错了插件就装不上：

1. 索引文件名必须是 `package.v2.json`（MP v2 的 `VERSION_FLAG` 恒为 `v2`，不可外部修改）。
2. 插件源码必须放在 `plugins.v2/<插件ID小写>/`。
3. 仓库**默认分支必须是 `main`** —— MP 拉索引的地址是硬编码的
   `https://raw.githubusercontent.com/{user}/{repo}/main/package.v2.json`。

MP 安装时走 GitHub Contents API（`/repos/{user}/{repo}/contents/plugins.v2/<id>`）递归拉文件，
所以**不需要 GitHub Actions、不需要打 Release**——直接推源码就能用。
（`package.v2.json` 条目里没写 `release` 字段即为文件列表安装模式。）

### 更新插件

1. 把 `package.v2.json` 里的 `version` **和** `plugins.v2/<id>/__init__.py` 里的 `plugin_version`
   **一起**改成新版本号（两处必须一致），并在 `history` 里补一条说明。
2. commit & push。
3. 回 MP 插件市场，等它刷新（30 分钟缓存）或重启 MP，然后点「升级」。

---

## 排错

**市场里搜不到这个插件**
- 确认 `https://raw.githubusercontent.com/ccbslsx/MoviePilot-Plugins/main/package.v2.json` 能打开。
- 索引有 30 分钟缓存，重启一次 MP 最省事。
- GitHub API 匿名配额耗尽（403）会导致整个市场加载失败 → 配 `GITHUB_TOKEN`。

**插件装上了但列表里看不到**
- 插件 ID 必须是 `GYingBTMagnet`，目录名必须是小写 `gyingbtmagnet`（MP 用 `plugin_id.lower()` 匹配目录）。
- 看看 MP 的 `UserInstalledPlugins` 里有没有 `GYingBTMagnet`。

**插件加载失败**
- MP 日志里搜 `加载插件 GYingBTMagnet 出错`。
- 常见原因是 `app.helper.browser` 或 `app.agent.tools.base` 导入失败 —— 说明 MP 大版本对不上。

**命令没反应**
- 命令处理器只在插件 `get_state()` 为真时启用 → 先确认配置页里 `enabled` 已开并保存。
- 参数通过事件里的 `arg_str` 传递，`/bt` 后面必须有内容。

**搜不到 / 取不到磁力**
- 看插件详情页的「最近记录」，里面记了每个镜像的尝试结果和失败原因。
- 站点账号过期会表现为「登录后仍受限」。
- 全部镜像 502 属于站点日常抽风，稍后重试。

**下载器没收到任务**
- 看详情页记录的下载器返回原文。
- 确认 `tag` 与 MP 的下载标签一致。

---

## 说明

- 插件只读取导航站的公开页面，不做任何越权操作。
- 代码为原创，按 GPL-3.0 授权（与 MoviePilot 生态一致）。

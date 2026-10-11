#!/usr/bin/env bash
# GYingBTMagnet 直装脚本（不走插件市场）
#
# 把插件拷进运行中的 MoviePilot 容器，并写进 UserInstalledPlugins。
# 适合插件市场刷不出来（GitHub API 限流）或者想跳过市场直接验证的场景。
#
# 用法:
#   ./tools/install.sh                  # 自动找 moviepilot 容器
#   ./tools/install.sh <容器名>
#
# 注意: 容器重建（升级镜像）后 /app/app/plugins 会回到镜像原样，需要重跑一次。

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"   # 仓库根目录
PLUGIN_ID="GYingBTMagnet"
DIR_NAME="gyingbtmagnet"
SRC="${HERE}/plugins.v2/${DIR_NAME}"
DEST="/app/app/plugins/${DIR_NAME}"

CONTAINER="${1:-}"
if [ -z "${CONTAINER}" ]; then
  CONTAINER="$(docker ps --format '{{.Names}} {{.Image}}' | grep -i moviepilot | head -n1 | awk '{print $1}')"
fi
if [ -z "${CONTAINER}" ]; then
  echo "✗ 没找到 moviepilot 容器。请手动指定: $0 <容器名>" >&2
  docker ps --format '  {{.Names}}\t{{.Image}}' >&2
  exit 1
fi
if [ ! -d "${SRC}" ]; then
  echo "✗ 找不到插件源码目录: ${SRC}" >&2
  exit 1
fi

echo "→ 目标容器: ${CONTAINER}"
echo "→ 拷贝插件到 ${DEST}"
docker cp "${SRC}" "${CONTAINER}:${DEST}"

echo "→ 标记为已安装（写入 UserInstalledPlugins）"
docker exec -i "${CONTAINER}" sh -c 'PY=$(command -v python3 || command -v python); "$PY" - <<'"'"'PYEOF'"'"'
import sys
sys.path.insert(0, "/app")
from app.db.systemconfig_oper import SystemConfigOper
from app.schemas.types import SystemConfigKey
op = SystemConfigOper()
ids = op.get(SystemConfigKey.UserInstalledPlugins) or []
if "GYingBTMagnet" not in ids:
    ids.append("GYingBTMagnet")
    op.set(SystemConfigKey.UserInstalledPlugins, ids)
print("  UserInstalledPlugins =", ids)
PYEOF'

echo
echo "✓ 文件就位。重启容器让插件被加载："
echo "    docker restart ${CONTAINER}"
echo
echo "重启后到 MoviePilot → 插件 → 已安装，找到「GYing 磁力导航搜索下载」填配置并保存。"

# -*- coding: utf-8 -*-
"""
生成插件图标 ../icons/gyingbtmagnet.png（256x256 RGBA，两色马鞍形磁铁 + 深色圆角底板）。

纯标准库实现（zlib + struct），不依赖 PIL/numpy —— 本机这两个 C 扩展都是坏的。
做法：先在 1024x1024 上按行区间做硬边光栅化（每行只用常数时间算边界），
再 4x4 盒式降采样到 256x256，等效 16 倍超采样抗锯齿。
"""

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # 仓库根目录

SIZE = 256          # 输出边长
SS = 4              # 超采样倍数
W = SIZE * SS       # 光栅化分辨率

# ----------------------------------------------------------------- 颜色
BG        = (22, 24, 38)
BORDER    = (52, 58, 88)
OUTLINE   = (30, 33, 50)
RED       = (232, 72, 82)
RED_HI    = (255, 138, 146)
BLUE      = (74, 150, 232)
BLUE_HI   = (140, 196, 255)
CLEAR     = None

# ----------------------------------------------------------------- 几何
HW = HH = SIZE / 2.0          # 底板半宽/半高
R_BOX = 52.0                  # 底板圆角
BORDER_W = 2.5                # 底板描边宽度

CX = 128.0                    # 磁铁中心 x
ARC_CY = 150.0                # 弧心 y
LEG_TOP = 62.0                # 立柱顶
R_IN, R_OUT = 19.0, 49.0      # 彩色环带内外半径
E = 2.5                       # 深色外轮廓厚度


def _box_halfwidth(y, hw, hh, r):
    """圆角矩形在高度 y 处的半宽；不在矩形内返回 -1。"""
    t = abs(y - HH) - (hh - r)
    if t <= 0:
        return hw
    if t >= r:
        return -1.0
    return (hw - r) + math.sqrt(r * r - t * t)


def _runs(y):
    """返回该 y 上一串 (x0, x1, color) 运行段。"""
    out = []
    # 底板
    ox = _box_halfwidth(y, HW, HH, R_BOX)
    if ox <= 0:
        return out
    ix = _box_halfwidth(y, HW - BORDER_W, HH - BORDER_W, R_BOX - BORDER_W)
    left_o, right_o = CX - ox, CX + ox
    if ix <= 0:
        out.append((left_o, right_o, BORDER))
    else:
        left_i, right_i = CX - ix, CX + ix
        out.append((left_o, left_i, BORDER))
        out.append((left_i, right_i, BG))
        out.append((right_i, right_o, BORDER))

    # 磁铁：深色外轮廓
    if LEG_TOP <= y <= ARC_CY:
        out.append((CX - R_OUT - E, CX - R_IN + E, OUTLINE))
        out.append((CX + R_IN - E, CX + R_OUT + E, OUTLINE))
    elif y > ARC_CY:
        dy = y - ARC_CY
        if dy < R_OUT + E:
            ro = math.sqrt(max(0.0, (R_OUT + E) ** 2 - dy * dy))
            ri = math.sqrt(max(0.0, (R_IN - E) ** 2 - dy * dy))
            out.append((CX - ro, CX - ri, OUTLINE))
            out.append((CX + ri, CX + ro, OUTLINE))

    # 磁铁：彩色本体
    if LEG_TOP + E <= y <= ARC_CY:
        hi = y <= LEG_TOP + E + 15
        out.append((CX - R_OUT, CX - R_IN, RED_HI if hi else RED))
        out.append((CX + R_IN, CX + R_OUT, BLUE_HI if hi else BLUE))
    elif y > ARC_CY:
        dy = y - ARC_CY
        if dy < R_OUT:
            ro = math.sqrt(max(0.0, R_OUT * R_OUT - dy * dy))
            ri = math.sqrt(max(0.0, R_IN * R_IN - dy * dy))
            out.append((CX - ro, CX - ri, RED))
            out.append((CX + ri, CX + ro, BLUE))
    return out


# ------------------------------------------------------ 光栅化到 W x W 的 RGBA
canvas = bytearray(W * W * 4)
rowbytes = W * 4

for py in range(W):
    y = (py + 0.5) / SS
    runs = _runs(y)
    if not runs:
        continue
    row = bytearray(rowbytes)
    for x0, x1, color in runs:
        a = max(0, int(round(x0 * SS)))
        b = min(W, int(round(x1 * SS)))
        if b <= a:
            continue
        row[a * 4:b * 4] = bytes((color[0], color[1], color[2], 255)) * (b - a)
    canvas[py * rowbytes:(py + 1) * rowbytes] = row

# ------------------------------------------------------------ 4x4 降采样
out = bytearray(SIZE * SIZE * 4)
for oy in range(SIZE):
    base = oy * SS * rowbytes
    for ox in range(SIZE):
        r = g = b = a = 0
        x0 = ox * SS * 4
        for sy in range(SS):
            off = base + sy * rowbytes + x0
            for sx in range(SS):
                i = off + sx * 4
                r += canvas[i]
                g += canvas[i + 1]
                b += canvas[i + 2]
                a += canvas[i + 3]
        n = SS * SS
        o = (oy * SIZE + ox) * 4
        if a == 0:
            out[o:o + 4] = b"\x00\x00\x00\x00"
        else:
            # 半透明边缘：颜色按已覆盖像素均值，避免出现暗边
            covered = a // 255
            div = covered if covered else n
            out[o] = r // div
            out[o + 1] = g // div
            out[o + 2] = b // div
            out[o + 3] = a // n


# --------------------------------------------------------------- PNG 编码
def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


raw = bytearray()
for y in range(SIZE):
    raw.append(0)  # filter type 0
    raw += out[y * SIZE * 4:(y + 1) * SIZE * 4]

png = (b"\x89PNG\r\n\x1a\n"
       + _chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
       + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
       + _chunk(b"IEND", b""))

with open(ROOT / "icons/gyingbtmagnet.png", "wb") as f:
    f.write(png)

# 顺便导出一张白底预览，方便肉眼检查
prev = bytearray()
for i in range(0, len(out), 4):
    a = out[i + 3]
    prev.append((out[i] * a + 245 * (255 - a)) // 255)
    prev.append((out[i + 1] * a + 245 * (255 - a)) // 255)
    prev.append((out[i + 2] * a + 245 * (255 - a)) // 255)

praw = bytearray()
for y in range(SIZE):
    praw.append(0)
    praw += prev[y * SIZE * 3:(y + 1) * SIZE * 3]

with open(ROOT / "icons/_preview.png", "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(praw), 9))
            + _chunk(b"IEND", b""))

print(f"icons/gyingbtmagnet.png 已生成：{SIZE}x{SIZE} RGBA，{len(png)} 字节")

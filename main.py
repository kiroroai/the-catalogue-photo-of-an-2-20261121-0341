#!/usr/bin/env python3
"""PERPETUA - a catalogue photo of an impossible gadget.

The power strip that plugs into itself. A white 4-socket strip on a clean
studio sweep; its own cord loops across the floor and plugs into its own
last socket. The one accessory: a small desk lamp plugged into another
socket, glowing warm, proving it works.

Pure Python 3.13 stdlib (zlib, struct, math, os). Deterministic: no random,
no clock. Writes /output/entry.png, 1024x1024, <= 1 MiB.
"""
import math
import os
import struct
import zlib

W = H = 1024
OUT = "/output/entry.png"

# --- framebuffer: three separate planes (fast slice fills + translate blends)
R = bytearray(W * H)
G = bytearray(W * H)
B = bytearray(W * H)
PLANES = (R, G, B)

_lut_cache = {}


def _lut(src, alpha):
    key = (src, alpha)
    t = _lut_cache.get(key)
    if t is None:
        inv = 255 - alpha
        t = bytes((src * alpha + d * inv) // 255 for d in range(256))
        _lut_cache[key] = t
    return t


def fill_row(y, x0, x1, color):
    x0 = max(0, int(x0))
    x1 = min(W, int(x1))
    if x1 <= x0 or y < 0 or y >= H:
        return
    n = x1 - x0
    base = y * W + x0
    for c in range(3):
        PLANES[c][base:base + n] = bytes([color[c]]) * n


def blend_row(y, x0, x1, color, alpha):
    x0 = max(0, int(x0))
    x1 = min(W, int(x1))
    if x1 <= x0 or y < 0 or y >= H or alpha <= 0:
        return
    if alpha >= 255:
        fill_row(y, x0, x1, color)
        return
    n = x1 - x0
    base = y * W + x0
    for c in range(3):
        p = PLANES[c]
        p[base:base + n] = bytes(p[base:base + n]).translate(_lut(color[c], alpha))


def fill_rect(x0, y0, x1, y1, color):
    for y in range(max(0, y0), min(H, y1)):
        fill_row(y, x0, x1, color)


def vgrad(y0, y1, c_top, c_bot):
    span = max(1, y1 - y0 - 1)
    for y in range(max(0, y0), min(H, y1)):
        t = (y - y0) / span
        col = tuple(int(c_top[i] + (c_bot[i] - c_top[i]) * t) for i in range(3))
        fill_row(y, 0, W, col)


def fill_ellipse(cx, cy, rx, ry, color, alpha=None):
    if rx <= 0 or ry <= 0:
        return
    for y in range(max(0, cy - ry), min(H, cy + ry + 1)):
        dy = (y - cy) / ry
        t = 1.0 - dy * dy
        if t <= 0:
            continue
        dx = int(rx * math.sqrt(t))
        if alpha is None:
            fill_row(y, cx - dx, cx + dx + 1, color)
        else:
            blend_row(y, cx - dx, cx + dx + 1, color, alpha)


def soft_ellipse(cx, cy, rx, ry, color, max_alpha, rings=6):
    """Concentric ellipses, largest/faintest first: linear alpha falloff."""
    step = max(1, max_alpha // rings)
    for i in range(rings, 0, -1):
        f = i / rings
        fill_ellipse(cx, cy, int(rx * f), int(ry * f), color, step)


def fill_rounded_rect(x0, y0, x1, y1, r, color):
    for y in range(max(0, y0), min(H, y1)):
        if y < y0 + r:
            dy = (y0 + r) - y
            dx = int(math.sqrt(max(0, r * r - dy * dy)))
            xa, xb = x0 + r - dx, x1 - r + dx
        elif y >= y1 - r:
            dy = y - (y1 - r)
            dx = int(math.sqrt(max(0, r * r - dy * dy)))
            xa, xb = x0 + r - dx, x1 - r + dx
        else:
            xa, xb = x0, x1
        fill_row(y, xa, xb, color)


def fill_rounded_rect_grad(x0, y0, x1, y1, r, c_top, c_bot):
    span = max(1, y1 - y0 - 1)
    for y in range(max(0, y0), min(H, y1)):
        t = (y - y0) / span
        col = tuple(int(c_top[i] + (c_bot[i] - c_top[i]) * t) for i in range(3))
        if y < y0 + r:
            dy = (y0 + r) - y
            dx = int(math.sqrt(max(0, r * r - dy * dy)))
            xa, xb = x0 + r - dx, x1 - r + dx
        elif y >= y1 - r:
            dy = y - (y1 - r)
            dx = int(math.sqrt(max(0, r * r - dy * dy)))
            xa, xb = x0 + r - dx, x1 - r + dx
        else:
            xa, xb = x0, x1
        fill_row(y, xa, xb, col)


def fill_poly(points, color):
    ys = [p[1] for p in points]
    y0, y1 = max(0, min(ys)), min(H, max(ys) + 1)
    n = len(points)
    for y in range(y0, y1):
        yc = y + 0.5
        xs = []
        for i in range(n):
            x1_, y1_ = points[i]
            x2_, y2_ = points[(i + 1) % n]
            if (y1_ <= yc < y2_) or (y2_ <= yc < y1_):
                t = (yc - y1_) / (y2_ - y1_)
                xs.append(x1_ + t * (x2_ - x1_))
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            fill_row(y, int(xs[i]), int(xs[i + 1]) + 1, color)


def _cubic(p0, p1, p2, p3, t):
    mt = 1.0 - t
    return (
        mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t ** 3 * p3[0],
        mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t ** 3 * p3[1],
    )


def _seg_len(p0, p1, p2, p3):
    pts = [_cubic(p0, p1, p2, p3, i / 24.0) for i in range(25)]
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(24))


def draw_cable(segs, width, color, alpha=None, dx=0, dy=0, step=1.4):
    r = width // 2
    for (p0, p1, p2, p3) in segs:
        n = max(8, int(_seg_len(p0, p1, p2, p3) / step))
        for i in range(n + 1):
            x, y = _cubic(p0, p1, p2, p3, i / n)
            fill_ellipse(int(x) + dx, int(y) + dy, r, r, color, alpha)


# --- 5x7 bitmap font (only the glyphs the product label needs) ---
FONT = {
    'A': ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    'D': ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    'E': ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    'F': ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    'I': ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    'L': ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    'M': ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    'N': ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    'O': ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    'P': ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    'Q': ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    'R': ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    'S': ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    'T': ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    'U': ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    'W': ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    '1': ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    '0': ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    '-': ("00000", "00000", "00000", "01110", "00000", "00000", "00000"),
    '.': ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    '\u2022': ("00000", "00100", "01110", "01110", "01110", "00100", "00000"),
    ' ': ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}


def text_width(s, scale):
    return len(s) * 6 * scale - scale


def draw_text(x, y, s, scale, color):
    cx = x
    for ch in s:
        g = FONT.get(ch)
        if g:
            for r in range(7):
                row = g[r]
                for c in range(5):
                    if row[c] == '1':
                        fill_rect(cx + c * scale, y + r * scale,
                                  cx + c * scale + scale, y + r * scale + scale,
                                  color)
        cx += 6 * scale


def draw_text_centered(cx, y, s, scale, color):
    draw_text(cx - text_width(s, scale) // 2, y, s, scale, color)


# --- palette ---
BG_TOP = (216, 220, 224)
BG_HOR = (240, 239, 237)
FL_TOP = (231, 228, 225)
FL_BOT = (208, 205, 202)
STRIP_TOP = (253, 252, 249)
STRIP_BOT = (236, 233, 228)
FACE_TOP = (244, 242, 238)
FACE_BOT = (230, 227, 222)
SOCK_OUT = (169, 166, 160)
SOCK_IN = (141, 138, 132)
SLOT = (48, 48, 50)
CABLE = (46, 46, 48)
CABLE_HI = (122, 122, 126)
PLUG = (51, 51, 54)
PRONG = (158, 158, 162)
LAMP_BLK = (35, 35, 38)
SHADE_IN = (247, 198, 92)
BULB = (255, 230, 168)
AMBER = (245, 178, 80)
INK = (74, 72, 68)
SHADOW = (60, 58, 55)
LED = (63, 174, 90)

HORIZON = 720


def draw_scene():
    # 1. studio sweep: wall gradient above horizon, floor gradient below
    vgrad(0, HORIZON, BG_TOP, BG_HOR)
    vgrad(HORIZON, H, FL_TOP, FL_BOT)
    for y in range(HORIZON - 1, HORIZON + 2):
        blend_row(y, 0, W, (150, 148, 145), 20)

    # 2. warm halo around the lamp (behind the product)
    soft_ellipse(110, 500, 190, 140, AMBER, 55, rings=12)
    # warm pool of light on the floor under the lamp
    soft_ellipse(110, 712, 130, 24, AMBER, 95, rings=12)

    # 3. floor shadows
    soft_ellipse(512, 690, 360, 22, SHADOW, 62, rings=10)   # under strip
    soft_ellipse(512, 666, 330, 10, (50, 48, 45), 72, rings=8)  # contact
    soft_ellipse(110, 706, 62, 10, SHADOW, 60, rings=8)    # under lamp base

    # 4. the self-powering cable: exits the strip's left end, loops across
    #    the floor, rises to plug into its own last socket.
    cable = [
        ((780, 440), (905, 455), (955, 560), (945, 680)),
        ((945, 680), (935, 800), (800, 855), (620, 850)),
        ((620, 850), (430, 845), (260, 800), (185, 690)),
        ((185, 690), (150, 640), (150, 615), (172, 600)),
    ]
    draw_cable(cable, 15, (70, 68, 65), alpha=50, dy=10)  # soft cast shadow
    draw_cable(cable, 15, CABLE)
    draw_cable(cable, 5, CABLE_HI, dx=-2, dy=-3)           # rounded highlight

    # 5. the strip: top face, then front face
    fill_poly([(190, 494), (834, 494), (854, 540), (170, 540)], FACE_TOP)
    fill_poly([(190, 494), (834, 494), (834, 499), (190, 499)], (255, 255, 255))
    # sockets on the top face
    for cx in (300, 460, 620, 780):
        fill_ellipse(cx, 517, 52, 17, SOCK_OUT)
        fill_ellipse(cx, 517, 44, 13, SOCK_IN)
        fill_ellipse(cx - 18, 517, 7, 4, SLOT)
        fill_ellipse(cx + 18, 517, 7, 4, SLOT)
    # front face
    fill_rounded_rect_grad(170, 540, 854, 660, 20, STRIP_TOP, STRIP_BOT)
    fill_row(541, 176, 848, (255, 255, 255))               # top edge light
    fill_rect(182, 656, 842, 659, (214, 210, 205))          # bottom edge shade

    # 6. product label (text belongs to the product only)
    draw_text_centered(512, 574, "PERPETUA", 2, INK)
    draw_text_centered(512, 600, "SELF-POWERED \u2022 NO OUTLET REQUIRED", 2, INK)
    draw_text_centered(512, 626, "MODEL PP-1", 1, INK)
    # power LED, on: it works
    fill_ellipse(806, 594, 7, 7, LED)
    fill_ellipse(806, 594, 3, 3, (140, 220, 160))

    # 7. lamp cord: lamp base up to socket 1, draped over the strip's edge
    lamp_cord = [((110, 700), (190, 730), (240, 640), (272, 512))]
    draw_cable(lamp_cord, 8, CABLE)
    # lamp plug into socket 1
    fill_poly([(268, 492), (298, 500), (292, 528), (262, 520)], PLUG)
    fill_poly([(282, 518), (290, 520), (288, 530), (280, 528)], PRONG)
    fill_poly([(290, 514), (298, 516), (296, 528), (288, 526)], PRONG)

    # 8. the self-plug: plug head seated in socket 4, prongs in
    fill_rounded_rect(748, 440, 812, 498, 10, PLUG)
    fill_row(442, 752, 808, (90, 90, 94))
    fill_rect(762, 498, 770, 517, PRONG)
    fill_rect(790, 498, 798, 517, PRONG)

    # 9. the lamp (the one accessory): matte-black stem + shade, warm glow
    fill_ellipse(110, 700, 36, 11, (28, 28, 31))           # base
    fill_ellipse(110, 694, 26, 8, (46, 46, 49))            # base dome
    fill_rounded_rect(104, 505, 116, 700, 5, LAMP_BLK)     # stem
    fill_poly([(80, 458), (140, 458), (164, 538), (56, 538)], LAMP_BLK)  # shade
    fill_poly([(88, 462), (132, 462), (152, 530), (68, 530)], SHADE_IN)  # lit inside
    fill_ellipse(110, 528, 22, 9, BULB)                    # bulb
    fill_ellipse(110, 540, 46, 12, (255, 217, 138), alpha=170)  # glow disc

    # 10. faint warm spill over the product from the lamp
    soft_ellipse(110, 500, 190, 140, AMBER, 16, rings=10)


def write_png(path):
    raw = bytearray()
    for y in range(H):
        raw.append(0)
        row = bytearray(W * 3)
        base = y * W
        row[0::3] = R[base:base + W]
        row[1::3] = G[base:base + W]
        row[2::3] = B[base:base + W]
        raw += row
    comp = zlib.compress(bytes(raw), 6)

    def chunk(typ, data):
        out = struct.pack(">I", len(data)) + typ + data
        return out + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", comp)
    png += chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


def main():
    draw_scene()
    n = write_png(OUT)
    print("wrote %s (%d bytes)" % (OUT, n))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Draws assets/booktrans_icon.png (the GUI's window / Dock icon): an open old book on a dark tile, grey type on the
left page, warm type on the right one, a turning arrow above - a scanned book becoming its translation.
Run again after changing it: python3 assets/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024                                   # drawn at 2x, saved at 512
OUT = Path(__file__).with_name('booktrans_icon.png')


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def main():
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    # tile: vertical gradient, rounded
    grad = Image.new('RGB', (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=lerp((44, 62, 92), (18, 24, 38), y / S))
    mask = Image.new('L', (S, S), 0)
    m = 80
    ImageDraw.Draw(mask).rounded_rectangle([m, m, S - m, S - m], radius=190, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # book shadow
    sh = Image.new('L', (S, S), 0)
    ImageDraw.Draw(sh).ellipse([210, 760, 814, 840], fill=150)
    sh = sh.filter(ImageFilter.GaussianBlur(28))
    img.paste((0, 0, 0, 255), (0, 0), Image.eval(sh, lambda v: v * 0.7))
    d = ImageDraw.Draw(img)

    cx, top, bot = S // 2, 430, 790
    paper, edge = (243, 232, 204), (196, 178, 140)
    # page stack (edges) then the two pages, slightly curved
    for k, col in ((22, edge), (11, (226, 211, 176)), (0, paper)):
        left = [(cx, top + 30 + k), (cx - 300 - k, top - 10 + k), (cx - 300 - k, bot - 30 + k), (cx, bot + k)]
        right = [(cx, top + 30 + k), (cx + 300 + k, top - 10 + k), (cx + 300 + k, bot - 30 + k), (cx, bot + k)]
        d.polygon(left, fill=col)
        d.polygon(right, fill=col)
    d.line([(cx, top + 30), (cx, bot)], fill=(170, 150, 112), width=6)      # the gutter

    def lines(x0, x1, colour, n=7, short=(3, 6)):
        """Type lines on a page from x0 (outer edge side) to x1 (gutter side); the page dips towards the gutter."""
        for i in range(n):
            y = top + 60 + i * (bot - top - 150) / (n - 1)
            xa, xb = min(x0, x1), max(x0, x1)
            if i in short:                       # a paragraph's last line: short, flush left
                xb = xa + (xb - xa) * 0.5
            dip = lambda x: 26 * (1 - abs(x - cx) / 300)
            d.line([(xa, y + dip(xa)), (xb, y + dip(xb))], fill=colour, width=20)

    # left page: the old print (grey-brown), right page: the translation (warm red)
    lines(cx - 255, cx - 45, (120, 108, 92))
    lines(cx + 255, cx + 45, (196, 64, 52))

    # turning arrow over the book, left -> right
    import math
    gold = (255, 196, 92)
    ccx, ccy, r = cx, 400, 215
    a0, a1 = 205, 330                            # degrees, PIL convention (clockwise from 3 o'clock)
    d.arc([ccx - r, ccy - r, ccx + r, ccy + r], start=a0, end=a1, fill=gold, width=36)
    t = math.radians(a1)
    ex, ey = ccx + r * math.cos(t), ccy + r * math.sin(t)
    tx, ty = -math.sin(t), math.cos(t)           # direction of travel at the end (clockwise)
    nx, ny = math.cos(t), math.sin(t)            # outward normal
    L, W = 70, 52
    d.polygon([(ex + tx * L, ey + ty * L), (ex + nx * W - tx * 8, ey + ny * W - ty * 8),
               (ex - nx * W - tx * 8, ey - ny * W - ty * 8)], fill=gold)

    img.resize((512, 512), Image.LANCZOS).save(OUT)
    print('wrote', OUT)


if __name__ == '__main__':
    main()

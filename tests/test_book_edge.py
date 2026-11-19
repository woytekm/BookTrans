"""Stacked page edges at the outer side of a page, photographs with light parts, hairlines that are not text."""
import numpy as np

import illustrations as I
import stage1


def _page(w=900, h=1300, edge=0, side='L'):
    g = np.full((h, w), 232, np.uint8)
    for k in range(0, edge, 9):                         # stacked page edges: long vertical lines at the border
        x = k + 4 if side == 'L' else w - 5 - k
        g[30:h - 30, x] = 120
    for y in range(200, 1100, 24):                      # a text column
        for x in range(240, 800, 12):
            g[y:y + 9, x:x + 7] = 40
    return g


def test_book_edge_band_widens_the_outer_margin_only_when_there_is_one():
    assert I.book_edge(_page(edge=0), 'L') == {'left': 0, 'right': 0}
    e = I.book_edge(_page(edge=80), 'L')
    assert 70 <= e['left'] <= 90 and e['right'] == 0
    assert I.page_ignore_margins(_page(edge=80), 'L')[0] >= e['left'] + 8
    assert I.page_ignore_margins(_page(edge=0), 'L') == I.edge_margins('L')
    r = I.book_edge(_page(edge=80, side='R'), 'R')
    assert 70 <= r['right'] <= 90 and r['left'] == 0
    g = _page()
    g[30:1270, 300] = 100                               # a vertical rule inside the page is not the book's edge
    assert I.book_edge(g, 'L')['left'] == 0


def test_a_photograph_grows_over_its_light_part_but_not_over_paper_or_text():
    g = np.full((900, 700), 230, np.uint8)
    g[100:400, 100:600] = 90                            # the dark part the threshold finds
    g[400:450, 100:600] = 200                           # a light pavement, still picture
    g[462:470, 120:500:6] = 40                          # the caption below, after a paper gap
    box = I.grow_halftone(g, [100, 100, 600, 400], 230, (10, 10, 10, 10))
    assert box[3] in (450, 451, 452) and box[0] == 100 and box[2] == 600
    shaded = np.full((900, 700), 230, np.uint8)
    shaded[:, 600:] = 205                               # a darker band of paper (page curvature) beside the photo
    shaded[100:400, 100:600] = 90
    assert I.grow_halftone(shaded, [100, 100, 600, 400], 230, (10, 10, 10, 10))[2] == 600


def test_hairlines_are_not_text_but_small_capitals_are():
    crease = np.zeros((14, 140), np.uint8)
    crease[6, ::2] = 255; crease[7, 10:30] = 255
    assert stage1.thin_line(crease)
    caps = np.zeros((22, 250), np.uint8)                 # capitals with their tops cut off by a figure's mask
    for x in range(0, 240, 14):
        caps[9:15, x:x + 2] = 255; caps[9:11, x:x + 9] = 255; caps[13:15, x:x + 9] = 255
    assert not stage1.thin_line(caps)

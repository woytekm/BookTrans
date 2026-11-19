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


def test_faint_broken_edge_lines_count_and_a_pale_stripe_inside_a_photo_is_crossed():
    g = _page()
    for x in (6, 9, 12, 44, 46, 70):                     # dark edge lines, then faint broken ones further in
        for y0 in range(40, 1250, 160):
            g[y0:y0 + 140, x] = 150
    assert 60 <= I.book_edge(g, 'L')['left'] <= 80
    p = np.full((900, 800), 210, np.uint8)
    p[100:400, 100:400] = 90                            # the dark left part the threshold finds
    p[100:400, 400:404] = 205                           # a pale column, 4 px
    p[100:400, 404:520] = 140                           # the rest of the photo, mid grey
    assert I.grow_halftone(p, [100, 100, 400, 400], 210, (10, 10, 10, 10))[2] >= 518


def test_a_fixed_edge_margin_overrides_the_detection():
    g = _page(edge=80)
    assert I.page_ignore_margins(g, 'L', fixed=120) == (120, 65, 10, 65)
    assert I.page_ignore_margins(g, 'L', fixed=0)[0] == 0                 # nothing ignored at the outer side
    assert I.page_ignore_margins(g, 'R', fixed='40') == (10, 65, 40, 65)
    assert I.page_ignore_margins(g, 'L', fixed='auto') == I.page_ignore_margins(g, 'L')
    assert I.page_ignore_margins(g, 'C', fixed=120) == I.edge_margins('C')   # a crop has no book edge


def test_edge_margin_setting_is_checked_and_passed_on(tmp_path):
    import pytest
    from project import DEFAULT_PARAMS, Project, ProjectError, check_params
    assert DEFAULT_PARAMS['edge_margin'] == 'auto'
    assert check_params(dict(DEFAULT_PARAMS, edge_margin=' 90 '))['edge_margin'] == '90'
    assert check_params(dict(DEFAULT_PARAMS, edge_margin=''))['edge_margin'] == 'auto'
    for bad in ('wide', '-5', '900'):
        with pytest.raises(ProjectError):
            check_params(dict(DEFAULT_PARAMS, edge_margin=bad))
    (tmp_path / 'in').mkdir()
    p = Project.create(tmp_path / 'p', 'book', tmp_path / 'in', {'edge_margin': '90'})
    c = p.command()
    assert c[c.index('--edge-margin') + 1] == '90'
    legacy = Project.create(tmp_path / 'q', 'book', tmp_path / 'in')
    del legacy.data['params']['edge_margin']                             # a project made before the setting
    c = legacy.command()
    assert c[c.index('--edge-margin') + 1] == 'auto'

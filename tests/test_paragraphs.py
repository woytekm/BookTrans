from stage2 import mark_paragraph_breaks, build_regions


def col(rows, x1=100, R=600, pitch=24):
    """rows: (text, shortfall_px[, indent_px]) -> line dicts."""
    out = []
    for i, r in enumerate(rows):
        text, short = r[0], r[1]; ind = r[2] if len(r) > 2 else 0
        y = 100 + i * pitch
        out.append({'text': text, 'type': 'body_line', 'relative_size': 1.0, 'bbox': [x1 + ind, y, R - short, y + 10]})
    return out


def breaks(c):
    return [l['paragraph_break'] for l in mark_paragraph_breaks(c, 10)]


def test_short_last_line_ends_paragraph():
    c = col([('a', 0), ('b', 1), ('c', 60), ('d', 0), ('e', 2), ('f', 0)])
    assert breaks(c) == [True, False, False, True, False, False]
    assert c[3]['paragraph_break_reason'] == 'short_last_line'


def test_hyphen_never_ends_paragraph():
    c = col([('a', 0), ('b-', 90), ('c', 0), ('d', 1)])
    assert breaks(c) == [True, False, False, False]


def test_first_line_indent():
    c = col([('a', 0), ('b', 0), ('c', 0, 25), ('d', 0)])
    assert breaks(c) == [True, False, True, False]
    assert c[2]['paragraph_break_reason'] == 'first_line_indent'


def test_extra_spacing():
    c = col([('a', 0), ('b', 0), ('c', 0), ('d', 0)])
    for l in c[2:]:
        l['bbox'][1] += 20; l['bbox'][3] += 20
    assert breaks(c) == [True, False, True, False]


def test_regions_split_per_paragraph():
    c = col([('a', 0), ('b', 70), ('c', 0), ('d', 50)])
    regs = build_regions(c, 10)
    assert [len(r['lines']) for r in regs] == [2, 2]

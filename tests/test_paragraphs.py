from stage2 import mark_paragraph_breaks, build_regions


def col(rows, x1=100, R=600, pitch=24):
    """rows: (text, shortfall_px[, indent_px]) -> line dicts."""
    out = []
    for i, r in enumerate(rows):
        text, short = r[0], r[1]; ind = r[2] if len(r) > 2 else 0
        y = 100 + i * pitch
        out.append({'text': text[:1].upper() + text[1:], 'type': 'body_line', 'relative_size': 1.0, 'bbox': [x1 + ind, y, R - short, y + 10]})
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


def test_lower_case_line_never_starts_a_paragraph():
    # OCR lost a word in line 3, which therefore looks short; "geklärt." cannot start a paragraph
    c = col([('Da abgesehen von dem Holzbau', 0), ('vorhanden war, ist ein Anschluß', 1),
             ('Voraussetzung. Für Niederschlesien ist diese Frage im', 70), ('geklärt.', 560)])
    c[3]['text'] = 'geklärt.'
    assert breaks(c) == [True, False, False, False]
    c2 = col([('Eine Zeile endet hier.', 80), ('Neuer Absatz beginnt', 0)])
    assert breaks(c2) == [True, True]                 # a sentence end followed by a capital is still a break


def _line(text, x1, x2, y, h=10):
    return {'text': text, 'type': 'body_line', 'relative_size': 1.0, 'bbox': [x1, y, x2, y + h]}


def test_a_margin_mark_between_lines_does_not_cut_the_paragraph():
    # page 62 of the test book: a ')' read at the right margin between "... im Jahre 1900" and "erfolgten ..."
    c = [_line('Nur das nördliche der beiden Portale ist alt. Bei der im Jahre 1900', 100, 600, 100),
         _line(')', 597, 601, 106),
         _line('erfolgten Restauration der Kirche sind die Stücke erneuert', 100, 600, 124),
         _line('worden. Das südliche Portal ist neu eingebaut.', 100, 400, 148),
         _line('Bei den sich nach außen erweiternden Portalgewänden wechseln', 100, 600, 172),
         _line('die Säulen mit tiefen Hohlkehlen ab.', 100, 350, 196)]
    ls = mark_paragraph_breaks(c, 10)
    by_text = {l['text'][:6]: l for l in ls}
    assert by_text[')']['stray_mark'] and not by_text[')']['paragraph_break']
    assert not by_text['erfolg']['paragraph_break']                         # was 'first_line_indent' because of ')'
    assert by_text['Bei de']['paragraph_break_reason'] == 'short_last_line'
    regs = build_regions(c, 10)
    assert [len(r['lines']) for r in regs if r['type'] == 'paragraph'] == [4, 2]


def test_a_page_number_below_the_text_stays_its_own_region():
    c = [_line('Erste Zeile des Absatzes mit vollem Satz und weiter', 100, 600, 100),
         _line('Ende des Absatzes.', 100, 300, 124),
         _line('62', 100, 118, 190)]                                         # far below: not part of the paragraph
    regs = build_regions(c, 10)
    texts = [[l['text'] for l in r['lines']] for r in regs if r['type'] == 'paragraph']
    assert texts == [['Erste Zeile des Absatzes mit vollem Satz und weiter', 'Ende des Absatzes.'], ['62']]

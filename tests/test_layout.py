"""Layout plan: line slots, page-synchronised flow, orphans/widows, drawing one page from the plan."""
import json

import numpy as np
import pytest
from PIL import Image

import layout as L
import render as R
from typeset import Fonts, typeset, text_of, line_words

DE = ('Den politischen und sozialen Verhältnissen ist es wohl zumeist zuzuschreiben, daß Oberschlesien nicht '
      'reich an monumentalen Werken des frühen Mittelalters ist. Leider ist nun auch das Wenige, was wir aus '
      'jener frühen Zeit besitzen, vielfach durch nachträgliche Um- und Erweiterungsbauten besonders in neuerer '
      'Zeit so entstellt, daß der ursprüngliche Zustand schwer festgestellt werden kann. ')
PL = ('Stosunkom politycznym i społecznym należy zapewne przypisać przede wszystkim to, że Górny Śląsk nie jest '
      'bogaty w monumentalne dzieła wczesnego średniowiecza. Niestety także to nieliczne, co z owych wczesnych '
      'czasów posiadamy, zostało w wielu wypadkach tak oszpecone późniejszymi przebudowami, że pierwotny stan '
      'trudno obecnie ustalić. ')
PITCH, GH = 22.0, 14.0
PAPER = (240, 230, 190)


def _fonts():
    return Fonts()


def _lines(n, x1, x2, top, last_short=True):
    """n original lines of German body text, justified between x1 and x2, the last one shorter."""
    words = (DE * 4).split()
    out, k = [], 0
    for i in range(n):
        t = ' '.join(words[k:k + 9]); k += 9
        right = x2 if (i < n - 1 or not last_short) else x1 + 0.5 * (x2 - x1)
        if i == n - 1 and last_short:
            t = ' '.join(t.split()[:4])
        out.append({'bbox': [x1, top + i * PITCH, right, top + i * PITCH + GH], 'text': t, 'glyph_height_px': GH})
    return out


def _para(bid, unit, n, x1, x2, top, **kw):
    lines = _lines(n, x1, x2, top, **kw)
    return {'block_id': bid, 'kind': 'text', 'role': 'paragraph', 'unit_id': unit, 'line_count': n,
            'bbox': [x1, top, x2, top + (n - 1) * PITCH + GH], 'lines': lines,
            'typography': {'glyph_height_px': GH, 'line_pitch_px': PITCH, 'alignment': 'justified',
                           'uppercase_ratio': 0.0, 'first_line_indent_px': 0.0}}


def _fig(bid, box, tmp_path):
    (tmp_path / 'figures').mkdir(exist_ok=True)
    x1, y1, x2, y2 = box
    Image.new('RGB', (x2 - x1, y2 - y1), (60, 60, 60)).save(tmp_path / 'figures' / (bid + '.png'))
    return {'block_id': bid, 'kind': 'figure', 'role': 'illustration', 'bbox': list(box), 'crop_bbox': list(box),
            'file': 'figures/%s.png' % bid}


def _doc(tmp_path, pages, cols=((40, 560),), size=(600, 800)):
    out = []
    for k, blocks in enumerate(pages, 1):
        name = 'page%d.png' % k
        Image.new('RGB', size, PAPER).save(tmp_path / name)
        out.append({'page': k, 'side': 'LR'[(k - 1) % 2], 'image': name, 'image_full': None, 'scale': 1.0,
                    'width': size[0], 'height': size[1], 'blocks': blocks,
                    'layout': {'paper_color_rgb': list(PAPER),
                               'columns': [{'left': a, 'right': b} for a, b in cols] if len(cols) == 1 else []}})
    return {'_dir': str(tmp_path), 'pages': out, 'units': []}


def _pl(chars):
    t = ''
    while len(t) < chars:
        t += PL
    return t[:chars].rsplit(' ', 1)[0] + '.'


def _words_of(plan, unit):
    return [w for pg in plan['pages'] for ln in pg['lines'] if ln['unit'] == unit for w, _ in ln['words']]


# ---------------------------------------------------------------- typeset helpers
def test_text_of_and_line_words_round_trip():
    f = _fonts().get('regular', 8 * 16)
    lines, _ = typeset(PL * 2, f, 8 * 400, 0.0, True)
    assert text_of(lines).split() == (PL * 2).split() or len(text_of(lines)) == len((PL * 2).strip())
    rest = text_of(lines[3:])
    assert rest.split()[0] in (PL * 2) and len(rest) < len(PL * 2)
    for ln in lines:
        words = line_words(ln)
        assert abs(sum(w for _, w in words) + ln['gap'] * (len(words) - 1) - (ln['width'] - (f.getlength('-') if ln['hyphen'] else 0))) < 1.0


# ---------------------------------------------------------------- slots
def test_body_never_runs_over_a_figure_in_the_column(tmp_path):
    p1 = _para('p1', 'u1', 6, 40, 560, 100)
    fig = _fig('f1', (60, 240, 540, 460), tmp_path)
    p2 = _para('p2', 'u2', 8, 40, 560, 480)
    doc = _doc(tmp_path, [[p1, fig, p2]])
    trans = {'units': {'u1': {'pl': _pl(380)}, 'u2': {'pl': _pl(520)}}}
    plan = L.make_plan(doc, trans, _fonts())
    lines = plan['pages'][0]['lines']
    assert lines
    for ln in lines:                                    # no baseline inside the figure
        assert not (240 - 4 <= ln['baseline'] <= 460 + 14), ln
    # every line sits on an original line slot: baseline - ascender = an original line top
    tops = {round(l['bbox'][1]) for b in (p1, p2) for l in b['lines']}
    asc = plan['ratios']['regular']['asc']
    for ln in lines:
        assert min(abs(ln['baseline'] - asc * ln['size'] - t) for t in tops) < 1.5


def test_two_columns_fill_left_then_right(tmp_path):
    a = _para('a', 'u1', 10, 40, 290, 100)
    b = _para('b', 'u2', 10, 310, 560, 100)
    doc = _doc(tmp_path, [[a, b]], cols=((40, 290), (310, 560)))
    trans = {'units': {'u1': {'pl': _pl(330)}, 'u2': {'pl': _pl(330)}}}
    plan = L.make_plan(doc, trans, _fonts())
    lines = plan['pages'][0]['lines']
    left = [l for l in lines if l['x'] < 300]
    right = [l for l in lines if l['x'] >= 300]
    assert left and right
    assert all(l['x'] + l['width'] <= 290.5 for l in left)
    assert all(l['x'] + l['width'] <= 560.5 for l in right)
    assert lines[0]['unit'] == 'u1' and lines[0]['x'] < 300     # reading order: left column first


def test_text_wraps_beside_a_figure(tmp_path):
    p = _para('p', 'u1', 12, 40, 560, 100)
    for ln in p['lines'][2:8]:                          # lines 3..8 stop short of a picture on the right
        ln['bbox'][2] = 380
    fig = _fig('f', (400, 140, 560, 270), tmp_path)
    doc = _doc(tmp_path, [[p, fig]])
    plan = L.make_plan(doc, {'units': {'u1': {'pl': _pl(700)}}}, _fonts())
    for ln in plan['pages'][0]['lines']:
        top = ln['baseline'] - plan['ratios']['regular']['asc'] * ln['size']
        if 140 - 10 < top < 270:
            assert ln['x'] + ln['width'] <= 400 - 6 + 0.5


# ---------------------------------------------------------------- page sync
def test_short_translation_does_not_pull_the_next_page_back(tmp_path):
    p1 = _para('p1', 'u1', 20, 40, 560, 100)
    p2 = _para('p2', 'u2', 20, 40, 560, 100)
    doc = _doc(tmp_path, [[p1], [p2]])
    trans = {'units': {'u1': {'pl': _pl(600)}, 'u2': {'pl': _pl(1200)}}}  # u1 needs far fewer lines
    plan = L.make_plan(doc, trans, _fonts(), {'page_fit': False})
    assert {l['unit'] for l in plan['pages'][0]['lines']} == {'u1'}
    assert plan['pages'][1]['lines'][0]['unit'] == 'u2'
    rep = {u['unit']: u for u in plan['report']['units']}
    assert rep['u2']['starts_on_page'] == rep['u2']['original_page'] == 2
    assert any('empty body line' in w for w in plan['report']['warnings'])


def test_paragraph_crossing_a_page_keeps_every_word_once(tmp_path):
    p1a = _para('p1a', 'u1', 10, 40, 560, 100)
    p1b = _para('p1b', 'u2', 10, 40, 560, 100 + 11 * PITCH, last_short=False)
    p2a = _para('p2a', 'u2', 6, 40, 560, 100)
    p2b = _para('p2b', 'u3', 8, 40, 560, 100 + 7 * PITCH)
    doc = _doc(tmp_path, [[p1a, p1b], [p2a, p2b]])
    texts = {'u1': _pl(700), 'u2': _pl(1100), 'u3': _pl(560)}
    plan = L.make_plan(doc, {'units': {u: {'pl': t} for u, t in texts.items()}}, _fonts())
    for u, t in texts.items():
        got = ''.join(_words_of(plan, u))
        assert got.replace('-', '') == t.replace(' ', '').replace('-', ''), u
    pages_u2 = {pg['page'] for pg in plan['pages'] for l in pg['lines'] if l['unit'] == 'u2'}
    assert pages_u2 == {1, 2}
    assert plan['pages'][1]['lines'][0]['unit'] == 'u2'            # page 2 starts with the rest of u2


def test_long_translation_is_fitted_per_page_then_pushed(tmp_path):
    p1 = _para('p1', 'u1', 10, 40, 560, 100)
    p2 = _para('p2', 'u2', 10, 40, 560, 100)
    doc = _doc(tmp_path, [[p1], [p2]])
    plan = L.make_plan(doc, {'units': {'u1': {'pl': _pl(900)}, 'u2': {'pl': _pl(600)}}}, _fonts())
    sizes = [pg['body']['size_px'] for pg in plan['pages']]
    assert sizes[0] <= sizes[1]                         # page 1 had to shrink (or tighten) to keep u1 on it
    rep = {u['unit']: u for u in plan['report']['units']}
    assert rep['u2']['starts_on_page'] == 2
    # far too long: pushed, with a warning, and nothing lost
    plan = L.make_plan(doc, {'units': {'u1': {'pl': _pl(2600)}, 'u2': {'pl': _pl(600)}}}, _fonts())
    assert any('later page' in w or 'overflow' in w for w in plan['report']['warnings'])
    assert ''.join(_words_of(plan, 'u2')).replace('-', '') == _pl(600).replace(' ', '').replace('-', '')


def test_no_orphan_at_the_foot_no_widow_at_the_head(tmp_path):
    p1a = _para('p1a', 'u1', 9, 40, 560, 100)
    p1b = _para('p1b', 'u2', 1, 40, 560, 100 + 10 * PITCH, last_short=False)
    p2a = _para('p2a', 'u2', 9, 40, 560, 100)
    doc = _doc(tmp_path, [[p1a, p1b], [p2a]])
    f = _fonts()
    flow_plan = L.make_plan(doc, {'units': {'u1': {'pl': _pl(560)}, 'u2': {'pl': _pl(700)}}}, f, {'page_fit': False})
    on1 = [l for l in flow_plan['pages'][0]['lines'] if l['unit'] == 'u2']
    on2 = [l for l in flow_plan['pages'][1]['lines'] if l['unit'] == 'u2']
    assert len(on1) != 1                                # never one first line alone at the foot
    assert len(on2) != 1 or not on1                     # never one last line alone at the head


def test_untranslated_body_paragraph_keeps_its_slots(tmp_path):
    p1 = _para('p1', 'u1', 5, 40, 560, 100)
    p2 = _para('p2', 'u2', 5, 40, 560, 100 + 6 * PITCH)
    p3 = _para('p3', 'u3', 5, 40, 560, 100 + 12 * PITCH)
    doc = _doc(tmp_path, [[p1, p2, p3]])
    plan = L.make_plan(doc, {'units': {'u1': {'pl': _pl(400)}, 'u3': {'pl': _pl(400)}}}, _fonts())
    assert plan['pages'][0]['keep'] == ['p2']
    y1, y2 = p2['bbox'][1], p2['bbox'][3]
    assert all(not (y1 - 2 <= l['baseline'] <= y2 + 6) for l in plan['pages'][0]['lines'])


def test_text_without_a_unit_outside_figures_keeps_pixels(tmp_path):
    p1 = _para('p1', 'u1', 5, 40, 560, 100)
    label = {'block_id': 'lab', 'kind': 'text', 'role': 'paragraph', 'bbox': [100, 400, 160, 414], 'line_count': 1,
             'lines': [{'bbox': [100, 400, 160, 414], 'text': 'A-B'}], 'typography': {'glyph_height_px': GH}}
    inside = dict(label, block_id='lab2', bbox=[300, 600, 340, 614])
    fig = _fig('f', (280, 560, 500, 700), tmp_path)
    doc = _doc(tmp_path, [[p1, label, inside, fig]])
    plan = L.make_plan(doc, {'units': {'u1': {'pl': _pl(400)}}}, _fonts())
    assert plan['pages'][0]['keep'] == ['lab']


# ---------------------------------------------------------------- drawing from the plan
def test_draw_one_page_from_a_saved_plan(tmp_path):
    p1 = _para('p1', 'u1', 20, 40, 560, 100)
    p2 = _para('p2', 'u2', 20, 40, 560, 100)
    doc = _doc(tmp_path, [[p1], [p2]])
    trans = {'units': {'u1': {'pl': _pl(1200)}, 'u2': {'pl': _pl(1200)}}}
    names, rep = R.render(doc, trans, tmp_path / 'out', _fonts())
    plan = json.loads((tmp_path / 'out' / 'layout.json').read_text(encoding='utf-8'))
    assert plan['schema'] == L.SCHEMA
    img, _ = R.draw_page(doc, plan, 1)                 # fonts from the plan
    ref = np.asarray(Image.open(tmp_path / 'out' / names[1]).convert('RGB'))
    assert np.array_equal(np.asarray(img), ref)
    only, _ = R.render(doc, trans, tmp_path / 'one', _fonts(), pages=R.parse_pages('2', doc))
    assert only == [names[1]]
    assert np.array_equal(np.asarray(Image.open(tmp_path / 'one' / only[0]).convert('RGB')), ref)


def test_parse_pages():
    doc = {'pages': [{'page': n} for n in (50, 51, 52, 53)]}
    assert R.parse_pages(None, doc) == [0, 1, 2, 3]
    assert R.parse_pages('51,53', doc) == [1, 3]
    assert R.parse_pages('50-52', doc) == [0, 1, 2]


# ---------------------------------------------------------------- checks
def _line(block, x, baseline, width, size=16.0, words=3, stretch=1.0, **kw):
    d = {'unit': 'u_' + block, 'block': block, 'role': 'paragraph', 'style': 'regular', 'size': size, 'track': 0.0,
         'x': x, 'baseline': baseline, 'width': width, 'gap': 4.0, 'hyphen': False, 'stretch': stretch,
         'words': [['w', width / words]] * words}
    d.update(kw)
    return d


def _check_doc(tmp_path, lines, keep=()):
    fig = _fig('f', (60, 300, 540, 500), tmp_path)
    doc = _doc(tmp_path, [[fig, {'block_id': 'k', 'kind': 'text', 'role': 'caption', 'bbox': [60, 520, 300, 534]}]])
    plan = {'ratios': {'regular': _fonts().ratios('regular')}, 'report': {'units': []},
            'pages': [{'page': 1, 'lines': lines, 'keep': list(keep), 'body': None}]}
    return L.check_plan(doc, plan)


def test_checks_find_collisions_overflow_and_loose_lines(tmp_path):
    checks, summary = _check_doc(tmp_path, [
        _line('a', 40, 120, 500),                                  # clean
        _line('b', 40, 320, 500),                                  # over the figure
        _line('c', 40, 528, 200),                                  # over kept text
        _line('d', 40, 600, 300), _line('e', 200, 603, 300),       # two blocks on top of each other
        _line('g', 40, 700, 500, stretch=2.3),                     # loose
        _line('h', 40, 760, 500, overflow=True),                   # outside its frame
    ], keep=['k'])
    kinds = {(c['kind'], c['block'], c['with']) for c in checks}
    assert ('collision', 'b', 'f') in kinds and ('collision', 'c', 'k') in kinds
    assert any(c['kind'] == 'collision' and {c['block'], c['with']} == {'d', 'e'} for c in checks)
    assert ('loose', 'g', None) in kinds and ('overflow', 'h', None) in kinds
    assert not any(c['block'] == 'a' for c in checks)
    assert summary['1']['collision'] == 3


def test_a_real_plan_around_a_figure_has_no_collisions(tmp_path):
    p1 = _para('p1', 'u1', 6, 40, 560, 100)
    fig = _fig('f1', (60, 240, 540, 460), tmp_path)
    p2 = _para('p2', 'u2', 8, 40, 560, 480)
    doc = _doc(tmp_path, [[p1, fig, p2]])
    plan = L.make_plan(doc, {'units': {'u1': {'pl': _pl(380)}, 'u2': {'pl': _pl(520)}}}, _fonts())
    assert not [c for c in plan['report']['checks'] if c['kind'] == 'collision']


def test_drift_is_reported(tmp_path):
    p1 = _para('p1', 'u1', 10, 40, 560, 100)
    p2 = _para('p2', 'u2', 10, 40, 560, 100)
    p3 = _para('p3', 'u3', 10, 40, 560, 100)
    doc = _doc(tmp_path, [[p1], [p2], [p3]])
    plan = L.make_plan(doc, {'units': {'u1': {'pl': _pl(1500)}, 'u2': {'pl': _pl(300)}, 'u3': {'pl': _pl(300)}}}, _fonts())
    drift = [c for c in plan['report']['checks'] if c['kind'] == 'drift']
    assert drift and drift[0]['unit'] == 'u2'


# ---------------------------------------------------------------- emphasis inside a paragraph, PDF
STYLED = ('Portal kościoła w <sp>Byczynie</sp> jest jednym z najstarszych, a jego <i>tympanon</i> i <i>archiwolta</i> '
          'pochodzą z <b>XIII</b> wieku; zachowały się też <i>Steinmetzzeichen</i>u oraz <sp>bardzo charakterystyczne</sp> '
          'detale portalu bocznego, które opisał już Lutsch w swoim spisie zabytków.')


def test_styled_runs_round_trip_and_carry_their_style():
    f = _fonts(); size = 18
    font = f.get('regular', size * 8)
    lines, _ = typeset(STYLED, font, 8 * 400, 0.0, True, fonts_for=lambda st: f.get(st, size * 8))
    assert text_of(lines) == STYLED
    rest = text_of(lines[2:])
    assert rest.count('<sp>') == rest.count('</sp>') and rest.count('<i>') == rest.count('</i>')
    entries = [e for ln in lines for e in line_words(ln)]
    by_text = {e[0]: (e[2] if len(e) > 2 else {}) for e in entries}
    assert by_text['tympanon'] == {'style': 'italic'} and by_text['XIII'] == {'style': 'bold'}
    assert by_text['Byczynie']['track'] > 0.1 * size * 8                       # letter-spaced
    assert by_text['Steinmetzzeichen'] == {'style': 'italic', 'join': True} and 'u' in by_text   # style change inside a word
    for ln in lines[:-1]:                                                      # still justified
        assert abs(ln['width'] - 8 * 400) < 1.0
    plain, _ = typeset(STYLED, font, 8 * 400, 0.0, True)                       # without fonts_for: tags dropped
    assert '<' not in ''.join(e[0] for ln in plain for e in line_words(ln))


def test_styled_text_through_the_plan_and_drawing(tmp_path):
    p1 = _para('p1', 'u1', 6, 40, 560, 100)
    doc = _doc(tmp_path, [[p1]])
    trans = {'units': {'u1': {'pl': STYLED}}}
    names, _ = R.render(doc, trans, tmp_path / 'out', _fonts())
    plan = json.loads((tmp_path / 'out' / 'layout.json').read_text(encoding='utf-8'))
    metas = [w[2] for ln in plan['pages'][0]['lines'] for w in ln['words'] if len(w) > 2]
    assert any(m.get('style') == 'italic' for m in metas) and any('track' in m for m in metas)
    assert not any('<' in w[0] for ln in plan['pages'][0]['lines'] for w in ln['words'])
    img, _ = R.draw_page(doc, plan, 0)
    assert np.array_equal(np.asarray(img), np.asarray(Image.open(tmp_path / 'out' / names[0]).convert('RGB')))


def test_heading_uppercase_keeps_tags(tmp_path):
    from typeset import upper_keep_tags
    assert upper_keep_tags('portal w <i>byczynie</i>') == 'PORTAL W <i>BYCZYNIE</i>'


def test_pdf_has_one_page_per_drawn_page_at_the_physical_size(tmp_path):
    doc = _doc(tmp_path, [[_para('p1', 'u1', 6, 40, 560, 100)], [_para('p2', 'u2', 6, 40, 560, 100)]])
    trans = {'units': {'u1': {'pl': _pl(380)}, 'u2': {'pl': _pl(380)}}}
    _, rep = R.render(doc, trans, tmp_path / 'out', _fonts(), pdf='book.pdf')
    data = (tmp_path / 'out' / 'book.pdf').read_bytes()
    assert data.startswith(b'%PDF-1.4') and data.rstrip().endswith(b'%%EOF') and rep['pdf'] == 'book.pdf'
    assert data.count(b'/Type /Page ') == 2
    assert b'/MediaBox [0 0 360.000 480.000]' in data                    # 600 x 800 px at 120 DPI
    try:
        import pypdf
    except ImportError:
        return
    r = pypdf.PdfReader(str(tmp_path / 'out' / 'book.pdf'))
    assert len(r.pages) == 2 and float(r.pages[0].mediabox.width) == 360.0
    R.render(doc, trans, tmp_path / 'o2', _fonts(), pages=[1], pdf='p.pdf', pdf_dpi=300)
    assert len(pypdf.PdfReader(str(tmp_path / 'o2' / 'p.pdf')).pages) == 1


# ---------------------------------------------------------------- robustness (found on the first real book, 2026-11-18)
def _noise(bid, unit, box, text='|'):
    return {'block_id': bid, 'kind': 'text', 'role': 'paragraph', 'unit_id': unit, 'line_count': 1, 'bbox': list(box),
            'text': text, 'lines': [{'bbox': list(box), 'text': text}],
            'typography': {'glyph_height_px': GH, 'line_pitch_px': None, 'alignment': 'left'}}


def test_scan_noise_is_not_typeset_and_does_not_steal_a_paragraph(tmp_path):
    p1 = _para('p1', 'u1', 8, 40, 560, 100)
    edge = _noise('n1', 'u2', (585, 300, 588, 360))                    # '|' at the page edge, outside the column
    num = _noise('n2', 'u3', (300, 700, 318, 714), '62')               # a page number read as a paragraph
    junk_head = _noise('n3', 'u4', (586, 500, 589, 540), '| x |')     # noise that stage 4 joined to the next page's paragraph
    p2 = _para('p2', 'u4', 8, 40, 560, 100)
    doc = _doc(tmp_path, [[p1, edge, num, junk_head], [p2]])
    trans = {'units': {'u1': {'pl': _pl(500)}, 'u2': {'pl': '|'}, 'u3': {'pl': '62'}, 'u4': {'pl': _pl(500)}}}
    plan = L.make_plan(doc, trans, _fonts())
    pg = plan['pages'][0]
    assert set(pg['dropped']) == {'n1', 'n3'} and 'n2' in pg['keep']
    assert {l['unit'] for l in pg['lines']} == {'u1'}
    assert all(l['x'] + l['width'] <= 560.5 for p in plan['pages'] for l in p['lines'])
    assert {u['unit']: u for u in plan['report']['units']}['u4']['original_page'] == 2   # starts where its real text is


def test_stray_marks_read_as_lines_get_no_slot_and_keep_the_pitch(tmp_path):
    a = _para('a', 'u1', 4, 40, 560, 100)
    b = _para('b', 'u2', 4, 40, 560, 100 + 4 * PITCH + 2)
    b['lines'].insert(1, {'bbox': [556, 100 + 4 * PITCH + 12, 559, 100 + 4 * PITCH + 20], 'text': '5'})   # a margin mark
    b['line_count'] = 5
    b['typography']['line_pitch_px'] = 11.0                             # what such a mark does to the measured pitch
    doc = _doc(tmp_path, [[a, b]])
    plan = L.make_plan(doc, {'units': {'u1': {'pl': _pl(330)}, 'u2': {'pl': _pl(330)}}}, _fonts())
    assert plan['pages'][0]['body']['slots'] == 8
    base = sorted(l['baseline'] for l in plan['pages'][0]['lines'])
    gaps = np.diff(base)
    assert gaps.min() > 0.85 * PITCH and gaps.max() < 1.25 * PITCH


def test_even_line_grid_across_paragraph_blocks(tmp_path):
    a = _para('a', 'u1', 5, 40, 560, 100)
    b = _para('b', 'u2', 5, 40, 560, 100 + 5 * PITCH + 4)               # the original has 4 px more between blocks
    doc = _doc(tmp_path, [[a, b]])
    plan = L.make_plan(doc, {'units': {'u1': {'pl': _pl(420)}, 'u2': {'pl': _pl(260)}}}, _fonts())
    base = sorted(l['baseline'] for l in plan['pages'][0]['lines'])
    gaps = np.diff(base)
    assert gaps.max() - gaps.min() < 1.0                                # one grid: no bump inside the new paragraphs


def test_a_long_body_size_caption_is_set_as_body_text(tmp_path):
    fig = _fig('f', (100, 40, 500, 200), tmp_path)
    cap = _para('c', 'u1', 12, 40, 560, 240)
    cap['role'] = 'caption'
    cap['typography'].update(relative_size=1.0, alignment='left')
    doc = _doc(tmp_path, [[fig, cap]], cols=())
    doc['pages'][0]['layout']['columns'] = []
    plan = L.make_plan(doc, {'units': {'u1': {'pl': _pl(800)}}}, _fonts())
    assert plan['report']['reclassified'] == ['c']
    lines = plan['pages'][0]['lines']
    assert lines[0]['role'] == 'paragraph' and all(abs(l['width'] - 520) < 1.0 for l in lines[:-1])   # justified


# ---------------------------------------------------------------- captions (2026-11-18)
def _cap(bid, unit, box, text='40 BOSDORF OS PORTALSOCKEL', gh=8.0, fig=None):
    x1, y1, x2, y2 = box
    return {'block_id': bid, 'kind': 'text', 'role': 'caption', 'unit_id': unit, 'line_count': 1, 'bbox': list(box),
            'text': text, 'figure_ids': [fig] if fig else [], 'lines': [{'bbox': list(box), 'text': text, 'glyph_height_px': gh}],
            'typography': {'glyph_height_px': gh, 'line_pitch_px': None, 'alignment': 'left', 'uppercase_ratio': 1.0}}


def test_caption_geometry_follows_its_figure():
    page = {'layout': {'columns': [{'left': 40, 'right': 560}]}, 'blocks': []}
    fig = {'block_id': 'f', 'kind': 'figure', 'bbox': [300, 100, 560, 400]}
    beside = _cap('c', 'u1', (60, 388, 290, 398), fig='f')
    under = _cap('d', 'u2', (380, 410, 480, 418), fig='f')
    page['blocks'] = [fig, beside]
    anchor, lo, hi, extra, grow, _ = L.caption_geometry(page, beside)
    assert anchor == 'right' and grow == 'up' and hi < 300 and lo == 40            # flush to the picture, room above
    page['blocks'] = [fig, under]
    anchor, lo, hi, extra, grow, _ = L.caption_geometry(page, under)
    assert anchor == 'center' and (lo, hi) == (300, 560) and grow == 'down' and extra >= 1
    assert L.caption_geometry(page, under, tight=False)[1:3] == (40, 560)


def test_captions_share_one_size_and_are_not_hyphenated(tmp_path):
    figs = [_fig('f%d' % k, (40 + 180 * k, 100, 200 + 180 * k, 300), tmp_path) for k in range(3)]
    caps = [_cap('c%d' % k, 'u%d' % k, (40 + 180 * k, 310, 190 + 180 * k, 318), gh=8.0 + k, fig='f%d' % k) for k in range(3)]
    doc = _doc(tmp_path, [figs + caps])
    trans = {'units': {'u0': {'pl': '40 PAKOSŁAWICE COKÓŁ'}, 'u1': {'pl': '41 BARTULTOVICE COKÓŁ PORTALU'},
                       'u2': {'pl': '57 KORFANTÓW IMPOST PORTALU ZACHODNIEGO KATOLICKIEGO KOŚCIOŁA'}}}
    plan = L.make_plan(doc, trans, _fonts(), {})
    sizes = {ln['size'] for ln in plan['pages'][0]['lines']}
    assert len(sizes) == 1                                                       # one size for the page's captions
    words = [w for ln in plan['pages'][0]['lines'] for w, _ in ln['words']]
    assert not any(w.endswith('-') for w in words)                               # captions are never hyphenated


def test_typeset_without_hyphenation_keeps_words_whole():
    from typeset import _merge_soft
    f = _fonts().get('regular', 40)
    lines, _ = typeset('KATOLICKIEGO KOŚCIOŁA PARAFIALNEGO', f, f.getlength('KATOLICKIEGO KOŚC') + 2, 0.0, False,
                       hyphenate=False)
    assert [text_of([l]) for l in lines] == ['KATOLICKIEGO', 'KOŚCIOŁA', 'PARAFIALNEGO']
    assert _merge_soft([{'t': 'ko', 'w': 1, 'brk': 'soft'}, {'t': 'ściół', 'w': 2, 'brk': 'space'}]) == \
        [{'t': 'kościół', 'w': 3, 'brk': 'space'}]


def test_an_emptied_caption_is_neither_drawn_nor_kept(tmp_path):
    fig = _fig('f0', (40, 100, 400, 300), tmp_path)
    a = _cap('a', 'u1', (40, 310, 120, 318), fig='f0')
    b = _cap('b', 'u2', (160, 310, 300, 318), fig='f0')
    doc = _doc(tmp_path, [[fig, a, b]])
    trans = {'units': {'u1': {'pl': '43 RUDY, PORTAL ZACHODNI', 'source': '43 RAUDEN OS, WESTPORTAL'},
                       'u2': {'pl': '', 'source': ''}}}
    plan = L.make_plan(doc, trans, _fonts(), {})
    assert {ln['unit'] for ln in plan['pages'][0]['lines']} == {'u1'}
    assert 'b' not in plan['pages'][0]['keep'] and plan['report']['emptied'] == ['u2']


def test_a_caption_taking_extra_lines_closes_its_leading_and_stays_clear_of_the_text_below(tmp_path):
    f0, f1 = _fig('f0', (40, 100, 120, 300), tmp_path), _fig('f1', (130, 100, 560, 300), tmp_path)
    c0 = _cap('c0', 'u0', (40, 310, 120, 318), '59 POLITZ', fig='f0')
    c1 = _cap('c1', 'u1', (130, 310, 560, 318), '58 BOSDORF PORTALKÄMPFER', fig='f1')
    c0['typography']['line_pitch_px'] = c1['typography']['line_pitch_px'] = 14.0
    body = _para('p', 'u2', 3, 40, 560, 362)
    doc = _doc(tmp_path, [[f0, f1, c0, c1, body]])
    trans = {'units': {'u0': {'pl': '59 POLICE. PIERŚCIEŃ TRZONU KOLUMNY'}, 'u1': {'pl': '58 PAKOSŁAWICE, IMPOST PORTALU'},
                       'u2': {'pl': _pl(300)}}}
    plan = L.make_plan(doc, trans, _fonts(), {})
    mine = [ln for ln in plan['pages'][0]['lines'] if ln['unit'] == 'u0']
    assert len(mine) >= 2 and not any(ln.get('overflow') for ln in mine)
    assert max(ln['baseline'] for ln in mine) < 362 - 4                        # clear of the paragraph below
    gaps = {round(b['baseline'] - a['baseline'], 1) for a, b in zip(mine, mine[1:])}
    assert len(gaps) == 1 and gaps.pop() <= 14.0                               # one even leading, not wider than the original


def test_centered_page_position_puts_every_page_on_one_size_centred_and_level(tmp_path):
    # a left page with a wide outer margin and a right page with a narrow one, of slightly different sizes
    p1 = [_para('a', 'u1', 6, 230, 810, 150)]
    p2 = [_para('b', 'u2', 6, 60, 640, 165)]
    doc = _doc(tmp_path, [p1, p2], cols=((0, 0), (0, 0)), size=(900, 1300))
    doc['pages'][0]['layout']['columns'] = [{'left': 230, 'right': 810}]
    doc['pages'][1]['layout']['columns'] = [{'left': 60, 'right': 640}]
    doc['pages'][1]['width'] = 880
    assert R.page_frame(doc, 0, 'original') == (0.0, 0.0, 900.0, 1300.0)
    f0, f1 = R.page_frame(doc, 0, 'centered'), R.page_frame(doc, 1, 'centered')
    assert f0[2:] == f1[2:] == (890.0, 1300.0)                                 # one size: the medians
    assert 230 + f0[0] == pytest.approx(60 + f1[0]) == pytest.approx((890 - 580) / 2)   # column centred on both
    assert 150 + f0[1] == pytest.approx(165 + f1[1])                           # tops level
    trans = {'units': {'u1': {'pl': _pl(400)}, 'u2': {'pl': _pl(400)}}}
    plan = L.make_plan(doc, trans, _fonts(), {})
    img, _ = R.draw_page(doc, plan, 1, _fonts(), resolution='work', position='centered')
    assert img.size == (890, 1300)
    ink = np.where((np.asarray(img.convert('L')) < 150).any(axis=0))[0]
    assert abs((ink.min() + ink.max()) / 2 - 445) < 12                          # the text sits in the middle

"""Layout plan: line slots, page-synchronised flow, orphans/widows, drawing one page from the plan."""
import json

import numpy as np
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

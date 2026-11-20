import json
from pathlib import Path

import pytest

import numpy as np
from PIL import Image

import render as R


def _fonts():
    return R.Fonts()


TEXT = ('Najstarsze dzieła, jakie do nas dotarły, należą do drugiej połowy XIII wieku, co dla obszaru '
        'górnośląskiego oznacza epokę wczesnego gotyku. Czystych romańskich zabytków budownictwa, '
        'jakie na Dolnym Śląsku spotykamy jeszcze często, w szczątkach, nie znam.')


def test_justified_lines_fill_the_width():
    f = _fonts().get('regular', 160)
    lines, _ = R.typeset(TEXT, f, 3200, 0.0, True)
    assert len(lines) > 3
    for ln in lines[:-1]:
        assert abs(ln['width'] - 3200) < 1.0
    assert lines[-1]['width'] <= 3200


def test_no_line_ends_with_a_one_letter_word():
    f = _fonts().get('regular', 160)
    text = 'Zakres badań obejmuje w istocie kościoły z cegły i z kamienia, a także o ' * 6
    lines, _ = R.typeset(text, f, 2600, 0.0, True)
    for ln in lines[:-1]:
        last = ln['pieces'][-1]['t'].strip('.,;:')
        assert last.lower() not in R.SINGLE_LETTER


def test_hyphenation_only_with_pyphen():
    f = _fonts().get('regular', 160)
    pieces = R.pieces_of('przebudowami', f)
    if R._HYPH is not None:
        assert len(pieces) > 1 and 'soft' in {p['brk'] for p in pieces}
    else:
        assert len(pieces) == 1
    # kerning-preserving widths: the pieces add up to the whole word
    assert abs(sum(p['w'] for p in pieces) - f.getlength('przebudowami')) < 0.5


def _doc(tmp_path, with_translation=True):
    page = np.full((400, 600, 3), (240, 230, 190), np.uint8)
    page[20:80, 30:200] = (60, 60, 60)                                  # the "photo"
    Image.fromarray(page).save(tmp_path / 'page.png')
    Image.fromarray(page[20:80, 30:200]).save(tmp_path / 'fig.png')
    (tmp_path / 'figures').mkdir(exist_ok=True)
    Image.fromarray(page[20:80, 30:200]).save(tmp_path / 'figures' / 'fig.png')
    lines = [{'bbox': [40, 120 + 22 * i, 560, 134 + 22 * i], 'text': 'x' * 60, 'glyph_height_px': 14}
             for i in range(4)]
    para = {'block_id': 'p1-reg-1', 'kind': 'text', 'role': 'paragraph', 'bbox': [40, 120, 560, 206],
            'line_count': 4, 'unit_id': 'u1', 'lines': lines, 'reading_order': 2,
            'typography': {'glyph_height_px': 14.0, 'line_pitch_px': 22.0, 'alignment': 'justified',
                           'uppercase_ratio': 0.0, 'first_line_indent_px': 0.0}}
    fig = {'block_id': 'p1-ill-1', 'kind': 'figure', 'role': 'illustration', 'bbox': [30, 20, 200, 80],
           'crop_bbox': [30, 20, 200, 80], 'file': 'figures/fig.png', 'reading_order': 1}
    doc = {'_dir': str(tmp_path), 'units': [{'unit_id': 'u1', 'text': 'x'}],
           'pages': [{'page': 1, 'side': 'L', 'image': 'page.png', 'width': 600, 'height': 400, 'scale': 1.0,
                      'image_full': None,
                      'layout': {'paper_color_rgb': [240, 230, 190], 'columns': [{'left': 40, 'right': 560}]},
                      'blocks': [fig, para]}]}
    trans = {'units': {'u1': {'pl': TEXT}} if with_translation else {}}
    return doc, trans


def test_render_end_to_end(tmp_path):
    doc, trans = _doc(tmp_path)
    names, rep = R.render(doc, trans, tmp_path / 'out', _fonts(), resolution='work')
    out = np.asarray(Image.open(tmp_path / 'out' / names[0]).convert('RGB'))
    assert (out[30:70, 40:190] == 60).all()                              # figure pasted
    assert (out[400 - 5, 5] == (240, 230, 190)).all()                    # clean paper
    text_zone = out[115:215, 40:560]
    assert (text_zone.sum(axis=2) < 300).sum() > 200                     # ink was drawn
    assert not rep['warnings']
    assert (tmp_path / 'out' / 'compare.png').exists() and (tmp_path / 'out' / 'report.json').exists()


def test_missing_translation_keeps_original_pixels(tmp_path):
    doc, trans = _doc(tmp_path, with_translation=False)
    page = np.array(Image.open(tmp_path / 'page.png').convert('RGB'))
    page[130:140, 100:300] = (10, 10, 10)                                # "original text" pixels
    Image.fromarray(page).save(tmp_path / 'page.png')
    names, rep = R.render(doc, trans, tmp_path / 'out', _fonts(), resolution='work')
    out = np.asarray(Image.open(tmp_path / 'out' / names[0]).convert('RGB'))
    assert (out[130:140, 100:300] == 10).all()
    assert any('no translation' in w for w in rep['warnings'])


def _blk(widths_texts, justified=False):
    lines = [{'text': t, 'bbox': [0, i * 20, w, i * 20 + 14]} for i, (t, w) in enumerate(widths_texts)]
    return {'lines': lines, 'typography': {'alignment': 'justified' if justified else 'left'}}


def test_fit_track_fills_original_widths():
    f = _fonts().get('regular', 8 * 16)
    txt = 'Mittelalterliche Kirchenportale'
    nat = f.getlength(txt) / 8
    b = _blk([(txt, nat + 31 * 1.0)] * 3)                 # original is 1 px/char wider than natural
    t = R.fit_track([b], f, 8, 16)
    assert abs(t - 1.0) < 0.05
    wide = _blk([(txt, nat + 31 * 9.0)] * 3)              # absurdly wide original: capped at 0.12 em
    assert abs(R.fit_track([wide], f, 8, 16) - 0.12 * 16) < 1e-6
    narrow = _blk([(txt, nat - 20)] * 3)                  # original narrower: never negative
    assert R.fit_track([narrow], f, 8, 16) == 0.0


def test_tracking_option_and_report(tmp_path):
    doc, trans = _doc(tmp_path)
    _, rep0 = R.render(doc, trans, tmp_path / 'o0', _fonts(), tracking=0.0)
    _, rep1 = R.render(doc, trans, tmp_path / 'o1', _fonts(), tracking=0.06)
    u0, u1 = rep0['units'][0], rep1['units'][0]
    assert u0['tracking_em'] == 0.0 and abs(u1['tracking_em'] - 0.06) < 0.002
    assert 'lines_original' in u1
    _, rep2 = R.render(doc, trans, tmp_path / 'o2', _fonts(), size_scale=0.9)
    assert rep2['units'][0]['size_px'] < rep0['units'][0]['size_px'] * 0.95


# ---------------------------------------------------------------- output resolution, line drawings (captions work)
def test_final_pages_are_drawn_at_the_output_dpi(tmp_path):
    doc, trans = _doc(tmp_path)
    page = doc['pages'][0]
    assert R.output_scale(page) == R.DEFAULT_OUTPUT_DPI / R.WORK_DPI
    assert R.output_scale(page, 'work') == 1.0 and R.output_scale(page, 'auto', 240) == 2.0
    names, _ = R.render(doc, trans, tmp_path / 'out', _fonts(), output_dpi=240)
    out = Image.open(tmp_path / 'out' / names[0])
    assert out.size == (1200, 800)                                        # 600 x 400 working px at 2x
    assert (np.asarray(out.convert('RGB'))[60:140, 80:380] == 60).all()   # the figure scaled with the page


def test_line_drawings_are_printed_ink_only():
    paper = np.full((60, 80, 3), (235, 225, 190), np.uint8)
    drawing = paper.copy()
    drawing[10:50, 20:22] = 40; drawing[30:32, 10:70] = 40                # strokes on paper
    photo = np.linspace(40, 230, 60 * 80).reshape(60, 80, 1).repeat(3, axis=2).astype(np.uint8)
    assert R.is_line_drawing(drawing) and not R.is_line_drawing(photo)
    canvas = Image.new('RGB', (80, 60), (250, 245, 230))
    R._ink_only(canvas, Image.fromarray(drawing), (0, 0))
    a = np.asarray(canvas)
    assert tuple(a[5, 5]) == (250, 245, 230)                              # the drawing's paper tone is not pasted
    assert a[20, 20].max() < 80                                           # its strokes are


# ---------------------------------------------------------------- font B and superscripts (2026-11-20)
def test_font_b_and_superscript_markup(tmp_path):
    from typeset import Fonts, styled_pieces, strip_tags, upper_keep_tags, SUP_SCALE
    root = Path(__file__).resolve().parent.parent / 'fonts'
    f = Fonts(str(root / 'CrimsonText-Regular.ttf'), f2=str(root / 'CrimsonPro-Light.ttf'))
    assert f.paths['f2'].endswith('CrimsonPro-Light.ttf') and Fonts(str(root / 'CrimsonText-Regular.ttf')).paths['f2'].endswith('CrimsonText-Regular.ttf')
    assert f.get('sup_regular', 100).size == pytest.approx(100 * SUP_SCALE, abs=1)
    ps = styled_pieces('ut <f2>ipse advocatus</f2> valeat<sup>2</sup> <i><sup>3</sup></i>', lambda st: f.get(st, 40), 'regular')
    styles = [(p['t'], p.get('style')) for p in ps]
    assert ('ipse', 'f2') in styles and ('tus', 'f2') in styles and ('2', 'sup_regular') in styles
    assert ('3', 'sup_italic') in styles and ('ut', None) in styles
    assert strip_tags('a<f2>b</f2><sup>1</sup>') == 'ab1' and upper_keep_tags('a<f2>b</f2>') == 'A<f2>B</f2>'


def test_superscripts_are_raised_when_drawn():
    from PIL import ImageDraw
    from typeset import Fonts
    root = Path(__file__).resolve().parent.parent / 'fonts'
    f = Fonts(str(root / 'CrimsonText-Regular.ttf'))
    img = Image.new('RGB', (200, 80), 'white')
    line = {'x': 10, 'baseline': 60, 'size': 40, 'gap': 10, 'words': [['1', 20, {'style': 'sup_regular'}]]}
    R.draw_line(ImageDraw.Draw(img), line, f.get('regular', 40), 1.0, (0, 0, 0), fonts=f)
    rows = np.where((np.asarray(img.convert('L')) < 128).any(axis=1))[0]
    assert rows.max() < 60 - 0.25 * 40                                  # well above the baseline

import json

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

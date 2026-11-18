"""fontcheck: the font a synthetic scan was set in comes out best, and the measurements are sane."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import fontcheck as FC
from typeset import Fonts, family_of, font_spec

FONTS = Path(__file__).resolve().parent.parent / 'fonts'
WORDS = ('Den politischen und sozialen Verhältnissen ist es wohl zumeist zuzuschreiben, daß Oberschlesien nicht reich '
         'an monumentalen Werken des frühen Mittelalters ist. Leider ist nun auch das Wenige, was wir aus jener frühen '
         'Zeit besitzen, vielfach durch nachträgliche Um- und Erweiterungsbauten besonders in neuerer Zeit entstellt. ' * 3).split()


def _scan(tmp_path, font_file, size=20):
    f = ImageFont.truetype(str(font_file), size)
    im = Image.new('RGB', (700, 400), (235, 225, 195)); d = ImageDraw.Draw(im)
    asc = -f.getbbox('dbl', anchor='ls')[1]; box = f.getbbox('Hg', anchor='ls'); gh = box[3] - box[1]
    lines, k, top, pitch, W, X = [], 0, 60, 28, 560, 60
    for i in range(9):
        ws = []
        while k < len(WORDS) and f.getlength(' '.join(ws + [WORDS[k]])) < W * 0.97:
            ws.append(WORDS[k]); k += 1
        last = i == 8
        if last:
            ws = ws[:4]
        gap = (W - sum(f.getlength(w) for w in ws)) / (len(ws) - 1) if not last else f.getlength(' ')
        cx = X
        for w in ws:
            d.text((cx, top + i * pitch + asc), w, font=f, fill=(40, 32, 20), anchor='ls'); cx += f.getlength(w) + gap
        lines.append({'bbox': [X, top + i * pitch, X + W if not last else cx - gap, top + i * pitch + gh],
                      'text': ' '.join(ws), 'glyph_height_px': gh})
    im.save(tmp_path / 'p.png')
    blk = {'block_id': 'b1', 'kind': 'text', 'role': 'paragraph', 'unit_id': 'u1', 'line_count': 9,
           'bbox': [X, top, X + W, top + 8 * pitch + gh], 'lines': lines,
           'typography': {'glyph_height_px': gh, 'line_pitch_px': pitch, 'alignment': 'justified'}}
    return {'_dir': str(tmp_path), 'pages': [{'page': 1, 'image': 'p.png', 'blocks': [blk], 'layout': {}}]}


def test_the_font_of_the_scan_wins(tmp_path):
    doc = _scan(tmp_path, FONTS / 'CrimsonText-SemiBold.ttf')
    cands = [('regular', Fonts()), ('semibold', Fonts(FONTS / 'CrimsonText-SemiBold.ttf')),
             ('italic', Fonts(FONTS / 'CrimsonText-Italic.ttf')), ('pro light', Fonts(FONTS / 'CrimsonPro-Light.ttf'))]
    res = FC.check(doc, cands, out_dir=tmp_path / 'fc')
    assert res['ranking'][0] == 'semibold'
    m = res['fonts']['semibold']
    assert abs(m['width'] - 1) < 0.06 and abs(m['x_height'] - 1) < 0.05 and abs(m['darkness'] - 1) < 0.1
    assert res['fonts']['pro light']['darkness'] < res['fonts']['regular']['darkness'] < m['darkness']
    assert (tmp_path / 'fc' / 'specimen_b1.png').exists() and (tmp_path / 'fc' / 'fontcheck.json').exists()
    assert res['ink'] and max(res['ink']) < 120


def test_font_families_and_variable_specs():
    assert family_of(str(FONTS / 'CrimsonPro-Light.ttf'), 'italic').endswith('CrimsonPro-LightItalic.ttf')
    assert family_of(str(FONTS / 'CrimsonPro-Light.ttf'), 'bold').endswith('CrimsonPro-SemiBold.ttf')
    assert family_of(str(FONTS / 'CrimsonText-Regular.ttf'), 'italic').endswith('CrimsonText-Italic.ttf')
    f = Fonts(FONTS / 'CrimsonPro-Light.ttf')
    assert 'CrimsonText' not in f.paths['bold'] + f.paths['italic']          # never mixed with another family
    assert font_spec('a/B[wght].ttf@300') == ('a/B[wght].ttf', 300.0) and font_spec('a/B.ttf') == ('a/B.ttf', None)
    assert family_of('x/Font[wght].ttf@300', 'bold') == 'x/Font[wght].ttf@600'

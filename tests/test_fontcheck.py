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
    assert abs(m['width'] - 1) < 0.06 and abs(m['x_height'] - 1) < 0.05
    assert 1.0 <= m['darkness'] < 1.35                                     # set in black; the scan's ink is dark brown
    assert res['fonts']['pro light']['darkness'] < res['fonts']['regular']['darkness'] < m['darkness']
    assert (tmp_path / 'fc' / 'specimen_b1.png').exists() and (tmp_path / 'fc' / 'fontcheck.json').exists()
    assert res['ink'] == [0, 0, 0]


def test_font_families_and_variable_specs():
    assert family_of(str(FONTS / 'CrimsonPro-Light.ttf'), 'italic').endswith('CrimsonPro-LightItalic.ttf')
    assert family_of(str(FONTS / 'CrimsonPro-Light.ttf'), 'bold').endswith('CrimsonPro-SemiBold.ttf')
    assert family_of(str(FONTS / 'CrimsonText-Regular.ttf'), 'italic').endswith('CrimsonText-Italic.ttf')
    f = Fonts(FONTS / 'CrimsonPro-Light.ttf')
    assert 'CrimsonText' not in f.paths['bold'] + f.paths['italic']          # never mixed with another family
    assert font_spec('a/B[wght].ttf@300') == ('a/B[wght].ttf', 300.0) and font_spec('a/B.ttf') == ('a/B.ttf', None)
    assert family_of('x/Font[wght].ttf@300', 'bold') == 'x/Font[wght].ttf@600'


def test_default_font_is_configurable(tmp_path, monkeypatch):
    import json
    import typeset as T
    monkeypatch.delenv('BOOKTRANS_FONTS', raising=False)
    f = Fonts()                                                   # config/fonts.json: Crimson Text
    assert f.paths['regular'].endswith('CrimsonText-Regular.ttf') and f.paths['bold'].endswith('CrimsonText-SemiBold.ttf')
    assert f.name == 'CrimsonText-Regular'
    cfg = tmp_path / 'fonts.json'
    cfg.write_text(json.dumps({'regular': str(FONTS / 'CrimsonPro-Light.ttf')}), encoding='utf-8')
    monkeypatch.setenv('BOOKTRANS_FONTS', str(cfg))
    g = Fonts()
    assert g.paths['regular'].endswith('CrimsonPro-Light.ttf')
    assert g.paths['italic'].endswith('CrimsonPro-LightItalic.ttf') and g.paths['bold'].endswith('CrimsonPro-SemiBold.ttf')
    assert Fonts(FONTS / 'CrimsonText-Italic.ttf').paths['regular'].endswith('CrimsonText-Italic.ttf')   # --font wins
    cfg.write_text(json.dumps({'regular': 'CrimsonPro-Regular.ttf'}), encoding='utf-8')          # relative to the file
    (tmp_path / 'CrimsonPro-Regular.ttf').write_bytes((FONTS / 'CrimsonPro-Regular.ttf').read_bytes())
    assert Fonts().paths['regular'] == str(tmp_path / 'CrimsonPro-Regular.ttf')
    cfg.write_text('{"bold": "x.ttf"}', encoding='utf-8')
    import pytest
    with pytest.raises(ValueError):
        T.default_fonts()
    with pytest.raises(SystemExit):
        Fonts()


def test_match_ink_makes_the_set_text_as_dark_as_the_scan(tmp_path):
    from typecolour import match_ink
    doc = _scan(tmp_path, FONTS / 'CrimsonText-Regular.ttf')
    ink, info = match_ink(doc, Fonts())
    assert abs(info['darkness_set'] - info['darkness_scan']) < 0.01 * max(info['darkness_scan'], 0.01) + 0.002
    assert 0 < info['k'] < 1 and max(ink) < 100                           # the scan's ink (40, 32, 20) is dark
    heavy = _scan(tmp_path, FONTS / 'CrimsonText-SemiBold.ttf')             # a heavier original: darker ink needed
    ink2, info2 = match_ink(heavy, Fonts())
    assert sum(ink2) < sum(ink)

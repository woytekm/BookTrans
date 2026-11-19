"""Caption OCR helpers: the figure mask stops at a caption, caption pieces on one row are joined, the strip re-read."""
import numpy as np

import captions as C
import stage2


def _line(text, box, conf=80.0, gh=8.0, typ='caption_line'):
    return {'text': text, 'bbox': list(box), 'confidence': conf, 'glyph_height': gh, 'type': typ,
            'word_count': len(text.split()), 'words': [{'text': w, 'bbox': list(box), 'conf': conf} for w in text.split()]}


def test_mask_stops_at_a_caption_touching_the_picture():
    fig = [173, 157, 648, 499]                       # figure 177..494 plus the mask pad
    assert C.clip_mask(fig, [[168, 489, 419, 511]]) == [173, 157, 648, 488]
    assert C.clip_mask(fig, [[300, 300, 400, 320]]) == fig          # a label inside a drawing: left alone
    assert C.clip_mask([300, 100, 600, 400], [[200, 380, 296, 395]]) == [300, 100, 600, 400]   # not touching
    assert C.clip_mask([300, 100, 600, 400], [[590, 300, 700, 312]])[2] == 589                  # caption beside


def test_pieces_under_one_figure_are_joined_side_by_side_captions_are_not():
    fig = [[170, 160, 650, 494], [700, 160, 900, 494]]
    page = np.full((600, 1000, 3), 230, np.uint8)
    page[497:505, 185:240] = 40                       # letters the OCR dropped between the pieces
    page[497:505, 300:325] = 40
    lines = [_line('Br', (170, 495, 180, 503), 17), _line('U, MAR', (243, 497, 298, 503), 21),
             _line('AL DER MKCHE', (327, 497, 415, 505), 37), _line('46 OTHER', (710, 497, 800, 505)),
             _line('body text', (100, 540, 900, 552), 90, 14, 'body_line')]
    out = C.join_caption_rows(lines, fig, page)
    caps = [l for l in out if l['type'] == 'caption_line']
    assert [l['text'] for l in caps] == ['Br U, MAR AL DER MKCHE', '46 OTHER']
    assert caps[0]['bbox'] == [170, 495, 415, 505] and caps[0]['joined_from'] == ['Br', 'U, MAR', 'AL DER MKCHE']
    # two captions under one wide picture with blank paper between them stay apart
    two = [_line('38 FRIEDLAND OS', (233, 577, 459, 586)), _line('39 RITTERSWALDE', (575, 576, 802, 585))]
    assert len(C.join_caption_rows(two, [[220, 100, 820, 560]], np.full((700, 900, 3), 230, np.uint8))) == 2


def test_reocr_keeps_the_better_reading():
    page = np.full((300, 600, 3), 230, np.uint8)
    ln = _line('U, MAR AL DER MKCHE', (170, 100, 415, 110), 30)
    good = {'text': ['43', 'RAUDEN', 'OS,', 'WESTPORTAL'], 'conf': [90, 92, 91, 93],
            'left': [40, 120, 300, 400], 'top': [30] * 4, 'width': [60, 160, 80, 300], 'height': [30] * 4}
    new = C.reocr_line(ln, page, 'deu', ocr=lambda im: good)
    assert new['text'] == '43 RAUDEN OS, WESTPORTAL' and new['ocr_first']['text'] == 'U, MAR AL DER MKCHE'
    assert len(new['words']) == 4 and all(len(w['bbox']) == 4 for w in new['words'])
    bad = {'text': ['~~', 'x'], 'conf': [20, 10], 'left': [0, 10], 'top': [0, 0], 'width': [5, 5], 'height': [5, 5]}
    assert C.reocr_line(_line('43 RAUDEN OS', (170, 100, 300, 110), 88), page, 'deu', ocr=lambda im: bad)['text'] == '43 RAUDEN OS'


def test_junk_pieces_and_figure_junk():
    assert C.is_junk_caption(_line('"Bere', (0, 0, 10, 5), 17)) and not C.is_junk_caption(_line('40', (0, 0, 10, 5), 90))
    inside = _line('~#iI', (300, 300, 340, 310), 20, typ='body_line')
    assert C.drop_junk_in_figures([inside, _line('ok', (300, 300, 340, 310))], [[200, 200, 500, 500]])[0]['text'] == 'ok'


def test_mixed_case_body_size_text_beside_a_picture_is_not_a_caption():
    body = 14.0
    assert not stage2.caption_sized({'glyph_height': 14.0, 'uppercase_ratio': 0.05}, body)
    assert stage2.caption_sized({'glyph_height': 15.0, 'uppercase_ratio': 0.9}, body)       # capitals with an Ä
    assert stage2.caption_sized({'glyph_height': 10.0, 'uppercase_ratio': 0.05}, body)


def test_a_photo_edge_read_as_letters_under_the_mask_line_is_dropped():
    fig = [[255, 688, 628, 962]]
    edge = _line('IE Er EEE SE', (254, 950, 627, 967), 30, 17, 'heading_line')     # 70 % over the photo
    cap = _line('36 GROTTKAU, KATHOLISCHE PFARRKIRCHE', (153, 963, 626, 978), 90)
    body = _line('Text unter dem Bild', (150, 1000, 620, 1012), 35, 9, 'body_line')
    assert [l['text'] for l in C.drop_junk_in_figures([edge, cap, body], fig)] == [cap['text'], body['text']]

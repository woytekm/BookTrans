"""Caption help of the OCR correction window: caption units of a page, the enlarged row, join, apply to the JSON."""
import json

import pytest
from PIL import Image

import caption_tools as T


def _review():
    return {'page': 3, 'units': [
        {'unit_id': 'u1', 'role': 'paragraph', 'blocks': ['p'], 'text': 'Body', 'text_raw': 'Body'},
        {'unit_id': 'u2', 'role': 'caption', 'blocks': ['c2'], 'text': 'U, MAR', 'text_raw': 'U, MAR'},
        {'unit_id': 'u3', 'role': 'caption', 'blocks': ['c1'], 'text': 'Br', 'text_raw': 'Br'},
        {'unit_id': 'u4', 'role': 'caption', 'blocks': ['c3'], 'text': 'AL DER MKCHE', 'text_raw': 'AL DER MKCHE'}]}


def _page():
    return {'blocks': [{'block_id': 'p', 'bbox': [100, 600, 800, 700]}, {'block_id': 'c1', 'bbox': [170, 495, 180, 500]},
                       {'block_id': 'c2', 'bbox': [243, 497, 298, 502]}, {'block_id': 'c3', 'bbox': [327, 497, 415, 505]}]}


def test_caption_units_are_in_reading_order_with_their_boxes():
    caps = T.caption_units(_review(), _page())
    assert [c['unit_id'] for c in caps] == ['u3', 'u2', 'u4']
    assert caps[0]['bbox'] == [170, 495, 180, 500]
    box = T.row_box(caps[1], caps, page_w=900)                 # the whole row of pieces, padded
    assert box[0] == 170 - T.ZOOM_PAD and box[2] == 415 + T.ZOOM_PAD


def test_zoomed_view_and_read_again():
    img = Image.new('RGB', (900, 1200), (230, 220, 190))
    z, f = T.zoomed(img, [156, 481, 429, 519], width=760)
    assert f == pytest.approx(760 / 273, rel=0.01) and z.width == pytest.approx(760, abs=2)
    data = {'text': ['43', 'RAUDEN'], 'conf': [90, 93], 'left': [10, 80], 'top': [10, 10], 'width': [40, 150], 'height': [30, 30]}
    assert T.read_again(img, [170, 495, 415, 505], 'deu', ocr=lambda im: data) == ('43 RAUDEN', 91.5)


def test_join_and_apply_to_review():
    assert T.join_texts(['Br', 'U, MAR', 'AL'], 0) == ['Br U, MAR', '', 'AL']
    with pytest.raises(ValueError):
        T.join_texts(['a'], 0)
    txt = json.dumps(_review())
    out = json.loads(T.apply_to_review(txt, {'u3': '43 RAUDEN OS, WESTPORTAL DER KIRCHE', 'u2': '', 'u4': ''}))
    assert [u['text'] for u in out['units']] == ['Body', '', '43 RAUDEN OS, WESTPORTAL DER KIRCHE', '']
    with pytest.raises(ValueError):
        T.apply_to_review(txt, {'u9': 'x'})
    with pytest.raises(ValueError):
        T.apply_to_review('{broken', {'u3': 'x'})

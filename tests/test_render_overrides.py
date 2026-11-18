"""Human adjustments of rebuilt pages: the store (hash-tied) and their effect on the layout."""
import pytest

import layout as L
import render_overrides as RO
from typeset import Fonts
from test_layout import _para, _doc, _pl, _fig, GH


def _caption(bid, unit, box, text='57 FRIEDLAND OS PORTALKAMPFER'):
    x1, y1, x2, y2 = box
    return {'block_id': bid, 'kind': 'text', 'role': 'caption', 'unit_id': unit, 'line_count': 1, 'bbox': list(box),
            'lines': [{'bbox': list(box), 'text': text, 'glyph_height_px': GH * 0.7}],
            'typography': {'glyph_height_px': GH * 0.7, 'line_pitch_px': 16.0, 'alignment': 'left',
                           'uppercase_ratio': 0.9}}


def test_store_round_trip_validation_and_staleness(tmp_path):
    p1 = _para('p1', 'u1', 6, 40, 560, 100)
    doc = _doc(tmp_path, [[p1]])
    data = RO.empty()
    RO.set_block(data, p1, size_scale=0.9, anchor='center')
    RO.set_page(data, doc['pages'][0], size_factor=0.97)
    f = tmp_path / 'review' / 'render_overrides.json'
    RO.save(f, data)
    back = RO.load(f)
    blocks, pages, stale = RO.resolve(doc, back)
    assert blocks == {'p1': {'size_scale': 0.9, 'anchor': 'center'}} and pages == {0: {'size_factor': 0.97}} and not stale
    RO.set_block(back, p1, anchor=None)                       # None removes one key ...
    assert back['blocks']['p1'] == {'size_scale': 0.9, 'base': RO.block_hash(p1)}
    RO.set_block(back, p1, size_scale=None)                   # ... and the last key the entry
    assert 'p1' not in back['blocks']
    with pytest.raises(ValueError):
        RO.set_block(back, p1, size_scale=9)
    with pytest.raises(ValueError):
        RO.set_block(back, p1, anchor='middle')
    with pytest.raises(ValueError):
        RO.set_page(back, doc['pages'][0], size_factor=2)
    # the OCR text under the block changed: the adjustment is stale and not applied
    p1['lines'][0]['text'] = 'anders gelesen'
    blocks, pages, stale = RO.resolve(doc, data)
    assert 'p1' not in blocks and 0 not in pages and len(stale) == 2
    assert RO.load(tmp_path / 'none.json') == RO.empty()
    (tmp_path / 'bad.json').write_text('[1]', encoding='utf-8')
    with pytest.raises(ValueError):
        RO.load(tmp_path / 'bad.json')


def test_page_key_uses_tiff_and_position():
    assert RO.page_key({'page': 7, 'source_tiff': '0003.tif', 'page_in_spread': 2}) == '0003.tif#2'
    assert RO.page_key({'page': 7}) == 'page 7'


def test_block_adjustments_change_the_layout(tmp_path):
    cap = _caption('c1', 'u2', (100, 400, 330, 410))
    p1 = _para('p1', 'u1', 6, 40, 560, 100)
    doc = _doc(tmp_path, [[p1, cap]])
    trans = {'units': {'u1': {'pl': _pl(380)}, 'u2': {'pl': '57 FRYDLĄD, GŁOWICA PORTALU'}}}
    f = Fonts()
    base = L.make_plan(doc, trans, f)
    line0 = [l for l in base['pages'][0]['lines'] if l['unit'] == 'u2'][0]
    adj = L.make_plan(doc, trans, f, {'blocks': {'c1': {'size_scale': 0.8, 'tracking_em': 0.0, 'anchor': 'right'}}})
    line1 = [l for l in adj['pages'][0]['lines'] if l['unit'] == 'u2'][0]
    assert line1['size'] < line0['size'] * 0.85 and line1['track'] == 0.0
    assert abs(line1['x'] + line1['width'] - 330) < 0.6           # right-anchored in its frame
    rep = [u for u in adj['report']['units'] if u['unit'] == 'u2'][0]
    assert rep['adjusted'] == ['anchor', 'size_scale', 'tracking_em']
    kept = L.make_plan(doc, trans, f, {'blocks': {'c1': {'keep_original': True}}})
    assert 'c1' in kept['pages'][0]['keep'] and not [l for l in kept['pages'][0]['lines'] if l['unit'] == 'u2']
    assert kept['report']['kept_by_hand'] == ['u2']


def test_page_adjustment_fixes_the_body_fit(tmp_path):
    doc = _doc(tmp_path, [[_para('p1', 'u1', 10, 40, 560, 100)], [_para('p2', 'u2', 10, 40, 560, 100)]])
    trans = {'units': {'u1': {'pl': _pl(560)}, 'u2': {'pl': _pl(560)}}}
    plan = L.make_plan(doc, trans, Fonts(), {'pages': {1: {'size_factor': 0.9, 'track_delta_em': 0.03}}})
    b0, b1 = plan['pages'][0]['body'], plan['pages'][1]['body']
    assert b1['size_factor'] == 0.9 and b1['track_delta_em'] == 0.03
    assert abs(b1['size_px'] - 0.9 * b0['size_px'] / b0['size_factor']) < 0.05

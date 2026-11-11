import numpy as np
from PIL import Image

from docmodel import build_page, _nid


def _line(x1, x2, y, t='text'):
    return {'bbox': [x1, y, x2, y + 12], 'text': t, 'glyph_height': 12.0, 'confidence': 90,
            'uppercase_ratio': 0.0, 'paragraph_break': False}


def _para(oid, y, short_last=60):
    ls = [_line(100, 600, y + 20 * i) for i in range(3)] + [_line(100, 600 - short_last, y + 60)]
    return {'type': 'paragraph', 'object_id': oid, 'bbox': [100, y, 600, y + 72], 'text': 'x',
            'line_count': 4, 'lines': ls, 'median_relative_size': 1.0, 'translation_status': 'translate'}


def test_page_model(tmp_path):
    img = np.full((400, 700, 3), 240, np.uint8)
    cap = {'type': 'caption', 'object_id': 's1-p1-reg-2', 'bbox': [100, 150, 300, 170], 'text': 'Abb. 1',
           'line_count': 1, 'median_relative_size': .7, 'lines': [_line(100, 300, 150)],
           'associated_illustration_ids': ['p1-ill-1'], 'translation_status': 'translate'}
    page = {'page': 1, 'side': 'L', 'x_offset': 0, 'width': 700, 'height': 400, 'x': 0,
            'regions': [cap, _para('s1-p1-reg-3', 200), _para('s1-p1-reg-4', 290)],
            'illustrations': [{'object_id': 'p1-ill-1', 'bbox': [100, 20, 400, 140],
                               'classification_reason': 'stage1_line_art'}]}
    m = build_page(page, 'pages/p.png', img, tmp_path / 'figures', {'s1-p1-reg-3': 'u0001'})
    kinds = [b['kind'] for b in m['blocks']]
    assert kinds == ['figure', 'text', 'text', 'text']
    fig = m['blocks'][0]
    assert fig['captions'] == ['p1-reg-2']
    assert Image.open(tmp_path / 'figures' / fig['file'].split('/')[-1]).size[0] >= 300
    p1 = m['blocks'][2]
    assert p1['unit_id'] == 'u0001' and p1['typography']['alignment'] == 'justified'
    assert p1['typography']['last_line_shortfall_px'] == 60
    assert m['blocks'][3]['typography']['gap_above_px'] == 290 - (200 + 72)
    assert m['layout']['columns'][0]['right'] == 600.0
    assert m['layout']['text_area'] == [100, 200, 600, 362]


def test_nid():
    assert _nid('s1-p1-reg-2') == 'p1-reg-2' and _nid('p1-ill-1') == 'p1-ill-1'


def test_figures_cut_from_full_resolution(tmp_path):
    img = np.full((400, 700, 3), 240, np.uint8)
    full = np.full((2000, 3500, 3), 200, np.uint8)          # 5x the working page
    page = {'page': 1, 'side': 'L', 'x_offset': 0, 'width': 700, 'height': 400, 'scale': 5.0,
            'regions': [], 'illustrations': [{'object_id': 'p1-ill-1', 'bbox': [100, 20, 400, 140],
                                              'classification_reason': 'stage1_tonal'}]}
    m = build_page(page, 'pages/p.png', img, tmp_path / 'figures', {}, full, 'pages/p_full.png')
    fig = m['blocks'][0]
    w, h = Image.open(tmp_path / 'figures' / fig['file'].split('/')[-1]).size
    assert (w, h) == (1520, 620) and fig['bbox'] == [100, 20, 400, 140]   # (300+4)*5, (120+4)*5
    assert m['scale'] == 5.0 and m['image_full'] == 'pages/p_full.png'


def test_book_mode_keeps_ids_unique(tmp_path):
    from docmodel import build_document
    pages = []
    for k in (1, 2):                                   # two spreads, both with a "page 1" and the same object ids
        d = tmp_path / ('s%d' % k); d.mkdir()
        Image.fromarray(np.full((400, 700, 3), 240, np.uint8)).save(d / 'page_01_L.png')
        cap = {'type': 'caption', 'object_id': 's%d-p1-reg-2' % k, 'bbox': [100, 150, 300, 170], 'text': 'Abb.',
               'line_count': 1, 'median_relative_size': .7, 'lines': [_line(100, 300, 150)],
               'associated_illustration_ids': ['p1-ill-1'], 'translation_status': 'translate'}
        pages.append({'page': 1, 'side': 'L', 'x_offset': 0, 'width': 700, 'height': 400, 'input_index': k,
                      'page_image': 'page_01_L.png', 'regions': [cap, _para('s%d-p1-reg-3' % k, 200)],
                      'illustrations': [{'object_id': 'p1-ill-1', 'bbox': [100, 20, 400, 140],
                                         'classification_reason': 'stage1_line_art'}],
                      'source_tiff': 'scans/000%d.tif' % k, 'stage1_dir': str(d)})
    units = {'units': [{'unit_id': 'u0001', 'type': 'paragraph', 'text': 'x', 'dehyphenated': False, 'joins': [],
                        'source_object_ids': ['s1-p1-reg-3', 's2-p1-reg-3']}], 'pages': pages}
    doc = build_document(units, None, tmp_path / 'out', 'book', book=True)
    assert [p['page'] for p in doc['pages']] == [1, 2]
    assert [p['spread'] for p in doc['pages']] == [1, 2]
    ids = [b['block_id'] for p in doc['pages'] for b in p['blocks']]
    assert len(ids) == len(set(ids)) and 's2-p1-ill-1' in ids
    assert doc['units'][0]['source_block_ids'] == ['s1-p1-reg-3', 's2-p1-reg-3']
    figs = {b['file'] for p in doc['pages'] for b in p['blocks'] if b['kind'] == 'figure'}
    assert len(figs) == 2 and all((tmp_path / 'out' / f).exists() for f in figs)
    cap2 = [b for b in doc['pages'][1]['blocks'] if b['role'] == 'caption'][0]
    assert cap2['figure_ids'] == ['s2-p1-ill-1']
    assert (tmp_path / 'out' / 'pages' / '0001_page_01_L.png').exists()


def test_expand_inputs(tmp_path):
    from booktrans import expand_inputs
    for n in ('0002.tif', '0001.TIF', 'x.txt', '.hidden.tif'):
        (tmp_path / n).write_bytes(b'')
    tiffs, folder = expand_inputs([tmp_path])
    assert [t.name for t in tiffs] == ['0001.TIF', '0002.tif'] and folder == tmp_path

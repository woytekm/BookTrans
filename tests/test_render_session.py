"""The render preview session (no Tk): loading, layout, drawing, block lookup, adjustments, settings."""
import json

import pytest

import gui_demo
import render_session as RS
from project import ProjectError


@pytest.fixture()
def demo(tmp_path):
    return gui_demo.make_demo(tmp_path / 'p', translate=True)


def test_needs_a_document_and_a_translation(tmp_path):
    p = gui_demo.make_demo(tmp_path / 'p')                       # no translation
    with pytest.raises(ProjectError, match='run the translation first'):
        RS.RenderSession(p).load()


def test_layout_rows_drawing_and_blocks(demo):
    s = RS.RenderSession(demo).load()
    plan = s.relayout()
    assert plan['ink'] and s.timings['layout'] >= 0
    rows = s.page_rows()
    assert [r['page'] for r in rows] == [1, 2, 3] and rows[0]['lines'] == '8/8'
    assert set(rows[1]['checks']) == set(RS.CHECK_KINDS)
    rebuilt, orig = s.draw(0, 0.5)
    assert rebuilt.size == orig.size and abs(rebuilt.width - 0.5 * demo_page_width(s)) <= 1
    bid = s.block_at(0, 300, 250)
    info = s.block_info(0, bid)
    assert info['body'] and info['state'] == 'body text' and info['text']
    assert s.block_at(0, 5, 5) is None


def demo_page_width(s):
    from PIL import Image
    p = s.doc['pages'][0]
    return p.get('width') or Image.open('%s/%s' % (s.doc['_dir'], p['image'])).width


def test_adjustments_are_saved_tied_and_applied(demo):
    s = RS.RenderSession(demo).load()
    s.relayout()
    cap = next(b for p in s.doc['pages'] for b in p['blocks'] if b.get('role') == 'caption')
    pi = next(i for i, p in enumerate(s.doc['pages']) if cap in p['blocks'])
    before = s.block_info(pi, cap['block_id'])['report']['size_px']
    s.set_block(pi, cap['block_id'], size_scale=0.8, anchor='center')
    saved = json.loads(demo.overrides_file.read_text(encoding='utf-8'))
    assert saved['blocks'][cap['block_id']]['size_scale'] == 0.8 and saved['blocks'][cap['block_id']]['base']
    s.relayout()
    info = s.block_info(pi, cap['block_id'])
    assert info['report']['size_px'] < before * 0.85 and info['report']['adjusted'] == ['anchor', 'size_scale']
    with pytest.raises(ValueError):
        s.set_block(pi, cap['block_id'], size_scale=7)
    assert s.block_adjustment(cap['block_id'])['size_scale'] == 0.8           # a refused value changes nothing
    s.set_page(0, size_factor=0.95, track_delta_em=0.0)
    s.relayout()
    assert s.plan['pages'][0]['body']['size_factor'] == 0.95 and s.page_rows()[0]['adjusted']
    s.clear_page(0)
    s.clear_block(pi, cap['block_id'])
    assert s.page_adjustment(0) == {} and s.block_adjustment(cap['block_id']) == {}
    # a stale adjustment (its OCR text changed) is listed, not applied
    s.set_block(pi, cap['block_id'], size_scale=0.8)
    cap['lines'][0]['text'] = 'anders'
    s.relayout()
    assert s.stale and cap['block_id'] in s.stale[0]


def test_book_settings_are_validated_and_saved(demo):
    s = RS.RenderSession(demo).load()
    s.relayout()
    with pytest.raises(ProjectError):
        s.save_settings({'size_scale': 9})
    assert demo.render_settings['size_scale'] == 1.0
    s.save_settings({'tracking': '0.05', 'ink': '40,30,20'})
    assert json.loads(demo.file.read_text(encoding='utf-8'))['render']['tracking'] == '0.05'
    s.relayout()
    assert s.plan['ink'] == [40, 30, 20] and abs(s.plan['report']['units'][0]['tracking_em'] - 0.05) < 0.002
    assert RS.settings_for_layout({'tracking': 'auto', 'paragraph_indent': '', 'size_scale': 1, 'word_space': 1})['tracking'] is None


def test_render_command_with_pdf(demo):
    c = demo.render_command(pages='2', pdf=True)
    assert c[c.index('--pdf') + 1] == 'book.pdf' and c[c.index('--pages') + 1] == '2'

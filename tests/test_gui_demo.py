"""The demo project end to end: real stage 4 units, real ocrfix output, positions from pageindex."""
import pytest

pytest.importorskip('symspellpy')
pytest.importorskip('PIL')

import gui_demo      # noqa: E402
import pageindex as P   # noqa: E402


@pytest.fixture(scope='module')
def demo(tmp_path_factory):
    proj = gui_demo.make_demo(tmp_path_factory.mktemp('demo') / 'p')
    doc, which = proj.load_document()
    assert which == 'corrected'
    return proj, doc, P.DocIndex(doc, proj.load_review(), proj.book_dir())


def test_every_unit_is_traced_back_to_its_lines(demo):
    _, doc, ix = demo
    assert doc['units'] and all(ix.unit_map(u['unit_id']).aligned for u in doc['units'])


def test_all_four_mark_classes_occur(demo):
    _, doc, ix = demo
    seen = {m['cls'] for p in ix.pages for m in ix.page_marks(p['page'])}
    assert seen == {'auto', 'uncertain', 'watch', 'human'}


def test_every_mark_spells_the_token_ocrfix_reported(demo):
    """The strongest check of the offset -> line mapping: the characters of the line fragments, joined,
    are the raw OCR token.  (A word split by a hyphen at a line end is joined from its two fragments.)"""
    _, doc, ix = demo
    n = 0
    for p in ix.pages:
        for m in ix.page_marks(p['page']):
            if m['kind'] == 'flag' or m['kind'] in ('auto', 'human'):
                lines = {b['block_id']: b['lines'] for b in p['blocks'] if b['kind'] == 'text'}
                got = ''.join(lines[w['block']][w['line']]['text'][w['c0']:w['c1']] for w in m['where'])
                # an unrelated page may hold the other half of a straddling word: only whole marks are compared
                u = ix.units[m['unit']]
                whole = all(ix.block[b][0] == p['page'] for b in u['source_block_ids'])
                if whole:
                    assert got == m['token'], (m['token'], got)
                    n += 1
    assert n >= 8


def test_straddling_word_is_found_on_both_pages(demo):
    """'gegliederten Spitz-' (page 1) + 'bogen ...' (page 2) is one unit; a mark on 'Spitzbogen' must reach both."""
    _, doc, ix = demo
    u = next(u for u in doc['units'] if 'Spitzbogen' in u['text_raw'])
    assert ix.unit_pages(u['unit_id']) == [1, 2]
    um = ix.unit_map(u['unit_id'])
    i = u['text_raw'].index('Spitzbogen')
    frags = um.locate(i, i + len('Spitzbogen'))
    assert [f.block for f in frags] == ['s1-p2', 's1-p3']
    pages = {ix.block[f.block][0] for f in frags}
    assert pages == {1, 2}


def test_page_images_and_review_json(demo):
    proj, doc, ix = demo
    for p in ix.pages:
        assert ix.image_path(p['page']).is_file()
    r = ix.page_review(2)
    assert [u['unit_id'] for u in r['units']][0] == next(u['unit_id'] for u in doc['units'] if 'Spitzbogen' in u['text_raw'])
    assert r['units'][0]['continues_from_previous_page'] is True


def test_demo_refuses_to_overwrite(tmp_path):
    gui_demo.make_demo(tmp_path / 'd')
    with pytest.raises(SystemExit):
        gui_demo.make_demo(tmp_path / 'd')
    gui_demo.make_demo(tmp_path / 'd', force=True)

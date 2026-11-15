"""pageindex.py: unit text -> page/line fragments, checked against the real stage 4 builder."""
import pytest

import pageindex as P
import stage4

LINES = {
    'plain':     ['Die Kirche steht', 'am Markt und', 'ist alt.'],
    'removed':   ['Das Portal zeigt einen Spitz-', 'bogen mit Profil.'],
    'kept':      ['Der Gra-', 'Nit ist hart.'],
    'suspended': ['die Werk-', 'und Formsteine sind', 'selten.'],
    'lead':      ['  eingerückt und', '   weiter hier.'],
    'empty':     ['Anfang', '', 'Ende.'],
    'multi':     ['a-', 'b-', 'c d'],
}


def region(oid, lines, **kw):
    r = {'object_id': oid, 'type': 'paragraph', 'translation_status': 'translate', 'bbox': [0, 0, 1, 1],
         'lines': [{'text': t} for t in lines], 'text': ' '.join(lines)}
    r.update(kw)
    return r


@pytest.mark.parametrize('name', sorted(LINES))
def test_replay_equals_stage4_for_one_block(name):
    pages = [{'page': 1, 'input_index': 1, 'regions': [region('s1-r1', LINES[name])]}]
    unit = stage4.build_units(pages)[0]
    text, spans = P.replay([('s1-r1', LINES[name])])
    assert text == unit['text']
    for s in spans:                       # every span really is that line's text inside the unit text
        assert text[s.start:s.end] == LINES[name][s.line].strip()[:s.end - s.start]


@pytest.mark.parametrize('first,second', [
    (['Der Bau zeigt einen Spitz-'], ['bogen und mehr.']),           # hyphen removed across the page break
    (['Es handelt sich um Gra-'], ['Nit und anderes.']),             # upper case: hyphen kept
    (['Die Werk-'], ['und Formsteine liegen dort.']),                # suspended compound
    (['Der Satz endet nicht'], ['und geht weiter hier.']),           # plain continuation, no hyphen
])
def test_replay_equals_stage4_across_blocks(first, second):
    pages = [{'page': 1, 'input_index': 1, 'regions': [region('s1-a', first)]},
             {'page': 2, 'input_index': 1, 'regions': [region('s1-b', second)]}]
    stage4.link_pages(pages)
    units = stage4.build_units(pages)
    assert len(units) == 1 and units[0]['source_object_ids'] == ['s1-a', 's1-b']
    text, spans = P.replay([('s1-a', first), ('s1-b', second)])
    assert text == units[0]['text']
    assert {s.block for s in spans} == {'s1-a', 's1-b'}


# ---------------------------------------------------------------- a small document
def make_doc():
    a = ['Die Binfassung ist im vesten', 'ist stark entstellt. Der Spitz-']          # page 1, ends in a hyphen
    b = ['bogen zeigt z. B.in der Mitte', 'Profile.']                                 # page 2, continues
    c = ['Ein anderer Absatz.']
    def block(bid, lines, uid, order):
        return {'block_id': bid, 'kind': 'text', 'role': 'paragraph', 'bbox': [0, 0, 1, 1], 'reading_order': order,
                'unit_id': uid, 'text': ' '.join(lines), 'lines': [{'bbox': [0, i * 10, 100, i * 10 + 8], 'text': t} for i, t in enumerate(lines)]}
    pages = [
        {'page': 1, 'side': 'L', 'image': 'pages/p1.png', 'source_tiff': 't1.tif', 'blocks': [
            block('s1-a', a, 'u1', 1), {'block_id': 's1-fig', 'kind': 'figure', 'reading_order': 2, 'bbox': [0, 0, 1, 1]}]},
        {'page': 2, 'side': 'R', 'image': 'pages/p2.png', 'source_tiff': 't1.tif', 'blocks': [
            block('s1-b', b, 'u1', 1), block('s1-c', c, 'u2', 2)]},
    ]
    text1, _ = P.replay([('s1-a', a), ('s1-b', b)])
    units = [{'unit_id': 'u1', 'type': 'paragraph', 'text': text1, 'text_raw': text1, 'source_block_ids': ['s1-a', 's1-b'],
              'ocr_corrections': []},
             {'unit_id': 'u2', 'type': 'paragraph', 'text': c[0], 'text_raw': c[0], 'source_block_ids': ['s1-c'], 'ocr_corrections': []}]
    return {'pages': pages, 'units': units, 'source_file': 'x/t1.tif'}, text1


def test_straddling_word_gives_two_fragments_on_two_pages():
    doc, text = make_doc()
    i = text.index('Spitzbogen')
    um = P.DocIndex(doc).unit_map('u1')
    assert um.aligned
    fr = um.locate(i, i + len('Spitzbogen'))
    c0 = doc['pages'][0]['blocks'][0]['lines'][1]['text'].index('Spitz')
    assert [(f.block, f.line, f.c0, f.c1) for f in fr] == [('s1-a', 1, c0, c0 + 5), ('s1-b', 0, 0, 5)]
    assert doc['pages'][0]['blocks'][0]['lines'][1]['text'][c0:c0 + 5] == 'Spitz'    # the stripped hyphen is not part of it
    assert doc['pages'][1]['blocks'][0]['lines'][0]['text'][0:5] == 'bogen'


def test_leading_blanks_shift_the_fragment_into_the_raw_line():
    pages = [{'page': 1, 'input_index': 1, 'regions': [region('r', ['  eingerückt und', '   weiter hier.'])]}]
    text, spans = P.replay([('r', ['  eingerückt und', '   weiter hier.'])])
    i = text.index('weiter')
    (f,) = P.UnitMap('u', text, spans, True).locate(i, i + 6)
    assert (f.line, '   weiter hier.'[f.c0:f.c1]) == (1, 'weiter')


def test_marks_counts_and_fragments():
    doc, text = make_doc()
    i_bin, i_vest = text.index('Binfassung'), text.index('vesten')
    i_abbr = text.index('B.in') + 2                       # abbrev_space: offset is the END of the token "B."
    doc['units'][0]['ocr_corrections'] = [
        {'unit': 'u1', 'offset': i_bin, 'before': 'Binfassung', 'after': 'Einfassung', 'rule': 'ocr_confusion', 'cost': 0.7},
        {'unit': 'u1', 'offset': i_abbr, 'before': 'B.', 'after': 'B. ', 'rule': 'abbrev_space', 'cost': 0.2},
        {'unit': 'u1', 'offset': i_vest, 'before': 'vesten', 'after': 'besten', 'rule': 'human', 'cost': 0.0}]
    review = {'flags': [
        {'key': 'u1|Spitzbogen|1', 'unit': 'u1', 'offset': text.index('Spitzbogen'), 'token': 'Spitzbogen', 'reason': 'no_candidate',
         'severity': 'high', 'candidates': [{'text': 'Spitzbogen'}]},
        {'key': 'u2|Absatz|1', 'unit': 'u2', 'offset': 13, 'token': 'Absatz', 'reason': 'suspect', 'severity': 'low', 'candidates': []}]}
    ix = P.DocIndex(doc, review)
    rows = {r['page']: r for r in ix.page_rows()}
    assert (rows[1]['auto'], rows[1]['human'], rows[1]['uncertain'], rows[1]['watch']) == (1, 1, 1, 0)
    assert (rows[2]['auto'], rows[2]['human'], rows[2]['uncertain'], rows[2]['watch']) == (1, 0, 1, 1)
    assert rows[1]['text_blocks'] == 1 and rows[1]['figures'] == 1 and rows[1]['units'] == 1
    # the straddling flag is on both pages, each time with the fragment that is on that page
    m1 = [m for m in ix.page_marks(1) if m['cls'] == 'uncertain'][0]
    m2 = [m for m in ix.page_marks(2) if m['cls'] == 'uncertain'][0]
    c0 = doc['pages'][0]['blocks'][0]['lines'][1]['text'].index('Spitz')
    assert [(w['block'], w['c0'], w['c1']) for w in m1['where']] == [('s1-a', c0, c0 + 5)]
    assert [(w['block'], w['c0'], w['c1']) for w in m2['where']] == [('s1-b', 0, 5)]
    # abbrev_space: the mark covers the abbreviation itself ("B."), not what follows it
    ab = [m for m in ix.page_marks(2) if m['rule'] == 'abbrev_space'][0]
    w = ab['where'][0]
    assert doc['pages'][1]['blocks'][0]['lines'][0]['text'][w['c0']:w['c1']] == 'B.'


def test_unaligned_unit_falls_back_to_its_first_page():
    doc, text = make_doc()
    doc['units'][0]['text_raw'] = text + ' (changed since)'          # replay no longer matches
    doc['units'][0]['ocr_corrections'] = [{'unit': 'u1', 'offset': 4, 'before': 'Binf', 'after': 'Einf', 'rule': 'ocr_confusion', 'cost': 0.7}]
    ix = P.DocIndex(doc)
    assert not ix.unit_map('u1').aligned
    assert [m['where'] for m in ix.page_marks(1)] == [[]] and ix.page_marks(2) == []
    assert {r['page']: r['unaligned'] for r in ix.page_rows()} == {1: 1, 2: 1}


def test_unit_with_a_missing_block_is_unaligned_not_an_error():
    doc, _ = make_doc()
    doc['units'][1]['source_block_ids'] = ['gone']
    assert not P.DocIndex(doc).unit_map('u2').aligned


def test_page_review_lists_units_once_with_their_blocks_on_this_page():
    doc, text = make_doc()
    ix = P.DocIndex(doc)
    r1, r2 = ix.page_review(1), ix.page_review(2)
    assert [u['unit_id'] for u in r1['units']] == ['u1'] and [u['unit_id'] for u in r2['units']] == ['u1', 'u2']
    u = r1['units'][0]
    assert u['blocks'] == ['s1-a'] and u['pages'] == [1, 2] and u['continues_on_next_page'] and not u['continues_from_previous_page']
    assert r2['units'][0]['blocks'] == ['s1-b'] and r2['units'][0]['continues_from_previous_page']
    assert u['text'] == text and u['text_raw'] == text

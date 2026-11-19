import pytest

import ocr_decisions as od


def test_resolve_accept_word_and_status():
    d = od.empty()
    od.resolve(d, 'u0001|vesten|1')
    od.resolve(d, 'u0002|Hamann|1', 'Hamann')
    od.accept_word(d, 'Pitschen')
    od.accept_word(d, 'pitschen')                            # case-insensitive: not added twice
    assert d['accepted_words'] == ['Pitschen']
    assert od.status(d, 'u0001|vesten|1', 'vesten') == 'accepted'
    assert od.status(d, 'u0002|Hamann|1', 'Hamann') == 'replaced:Hamann'
    assert od.status(d, 'u0003|PITSCHEN|1', 'PITSCHEN') == 'word accepted'
    assert od.status(d, 'u0004|other|1', 'other') is None
    od.unresolve(d, 'u0001|vesten|1')
    od.forget_word(d, 'PITSCHEN')
    assert od.status(d, 'u0001|vesten|1', 'vesten') is None and od.status(d, 'x|Pitschen|1', 'Pitschen') is None


def test_round_trip_and_bad_files(tmp_path):
    p = tmp_path / 'review' / 'ocr_decisions.json'
    assert od.load_decisions(p) == od.empty()
    d = od.empty()
    od.resolve(d, 'u1|Größe|1', 'Größe')
    od.save_decisions(p, d)
    assert od.load_decisions(p) == d and not list(p.parent.glob('*.tmp')) and 'Größe' in p.read_text(encoding='utf-8')
    p.write_text('[]')
    with pytest.raises(ValueError):
        od.load_decisions(p)
    p.write_text('{"resolutions": 5}')
    with pytest.raises(ValueError):
        od.load_decisions(p)
    p.write_text('{nope')
    with pytest.raises(ValueError):
        od.load_decisions(p)


def test_merge_lets_the_gui_win_over_ocr_review():
    res, accepted = od.merge({'a|x|1': 'old', 'b|y|1': '='}, ['Foo'], {'resolutions': {'a|x|1': '='}, 'accepted_words': ['Foo', 'Bar']})
    assert res == {'a|x|1': '=', 'b|y|1': '='} and accepted == ['Foo', 'Bar']
    assert od.merge({}, [], {}) == ({}, [])


def test_ocrfix_run_takes_the_decisions(tmp_path):
    """End to end with the real Fixer is in test_ocrfix.py (needs the dictionaries); here: the flag reaches ``review_res``."""
    import ocrfix
    p = tmp_path / 'ocr_decisions.json'
    d = od.empty()
    od.resolve(d, 'u0001|vesten|1', 'besten')
    od.accept_word(d, 'Hamann')
    od.save_decisions(p, d)
    res, accepted = ocrfix.load_review(tmp_path / 'none.json')
    res, accepted = od.merge(res, accepted, od.load_decisions(p))
    assert res == {'u0001|vesten|1': 'besten'} and accepted == ['Hamann']


# ---------------------------------------------------------------- renumbered units (2026-11-19)
def _doc_units(*pairs):
    return {'units': [{'unit_id': uid, 'text': t} for uid, t in pairs]}


def test_decisions_follow_their_text_when_units_are_renumbered():
    import ocr_decisions as D
    old = _doc_units(('u0001', 'Das Portal in Pitschen'), ('u0002', 'Der Turm in vesten Mauern'))
    d = D.empty()
    D.resolve(d, 'u0002|vesten|1', 'festen')
    D.resolve(d, 'u0001|Pitschen|1', D.ACCEPT)
    D.stamp(d, {u['unit_id']: u for u in old['units']})
    assert set(d['bases']) == {'u0002|vesten|1', 'u0001|Pitschen|1'}
    # a new scan was added in front: the same texts now have other numbers, and u0002 is another text
    new = _doc_units(('u0001', 'Vorwort'), ('u0002', 'Das Portal in Pitschen'), ('u0003', 'Der Turm in vesten Mauern'))
    out, moved, stale = D.remap(d, new)
    assert moved == {'u0002|vesten|1': 'u0003|vesten|1', 'u0001|Pitschen|1': 'u0002|Pitschen|1'} and not stale
    assert out['resolutions'] == {'u0003|vesten|1': 'festen', 'u0002|Pitschen|1': D.ACCEPT}
    assert D.remap(out, new)[1] == {}                                   # already fitted: nothing moves
    # the text was corrected: the decision is set aside, not put on whatever unit has its number now
    changed = _doc_units(('u0001', 'Das Portal in Pitschen'), ('u0002', 'Der Turm in festen Mauern'))
    out, moved, stale = D.remap(d, changed)
    assert stale == ['u0002|vesten|1'] and 'u0002|vesten|1' not in out['resolutions']
    assert out['stale']['u0002|vesten|1']['resolution'] == 'festen'
    # an old file without fingerprints keeps its keys
    legacy = {'schema': '1.0', 'resolutions': {'u0002|vesten|1': '='}, 'accepted_words': []}
    assert D.remap(legacy, new)[0]['resolutions'] == {'u0002|vesten|1': '='}
    D.unresolve(d, 'u0002|vesten|1')
    assert 'u0002|vesten|1' not in d['bases']

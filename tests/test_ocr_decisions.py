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

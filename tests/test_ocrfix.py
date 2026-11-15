"""ocrfix.py with a tiny synthetic lexicon (the real dictionaries are downloaded, not in the repository)."""
import json

import pytest

pytest.importorskip('symspellpy')
import ocrfix as O   # noqa: E402

WORDS = {
    'einfassung': 37000, 'entstellt': 52000, 'der': 2000000000, 'die': 1900000000, 'und': 1400000000,
    'nicht': 380000000, 'mehr': 70000000, 'dass': 83000000, 'zustand': 900000, 'portal': 800000,
    'gebäude': 2000000, 'wesentlichen': 900000, 'besten': 900000, 'westen': 800000, 'festen': 700000,
    'kirche': 3000000, 'kirchen': 3000000, 'ursprüngliche': 300000, 'bruchsteinen': 20000,
    'fenster': 1500000, 'bei': 500000000, 'im': 400000000, 'türen': 400000, 'sockel': 50000, 'ecken': 200000, 'geklärt': 90000, 'ist': 900000000,
}


@pytest.fixture(scope='module')
def lex():
    lx = O.Lexicon(dict_dir='/nonexistent', vocab=(), use_hunspell=False,
                   freq_lines=['%s %d' % kv for kv in WORDS.items()])
    lx.add_name('OS')
    lx.add_name('Niederschlesien')
    return lx


@pytest.fixture()
def fx(lex):
    return O.Fixer(lex, suggest=False)


def fix(fx, text, **kw):
    new, cors, flags = fx.process(text, 'u1', **kw)
    return new, cors, flags


def test_known_words_old_spelling_and_compounds_are_left_alone(fx):
    new, cors, flags = fix(fx, 'Der Zustand, daß die Kirchen und Fenstertüren nicht mehr')
    assert new == 'Der Zustand, daß die Kirchen und Fenstertüren nicht mehr'
    assert not cors and not flags


def test_confusion_is_corrected_and_recorded(fx):
    new, cors, flags = fix(fx, 'Die Binfassung ist so enistellt.')
    assert new == 'Die Einfassung ist so entstellt.'
    assert [(c['before'], c['after'], c['rule']) for c in cors] == [
        ('Binfassung', 'Einfassung', 'ocr_confusion'), ('enistellt', 'entstellt', 'ocr_confusion')]
    assert cors[0]['offset'] == 4 and not flags


def test_glossary_name_beats_edit_distance(fx):
    new, cors, _ = fix(fx, 'In Niederschlesten sind sie häufig.')
    assert 'Niederschlesien' in new and cors[0]['cost'] < 0.5


def test_lost_space_between_common_words(fx):
    new, cors, _ = fix(fx, 'Es ist nichtmehr zu ermitteln.')
    assert 'nicht mehr' in new and cors[0]['rule'] == 'lost_space'


def test_inner_capital_splits_words(fx):
    new, cors, _ = fix(fx, 'an die Kirchen und dieKirche stehen')
    assert 'die Kirche' in new


def test_abbreviation_glued_to_next_word(fx):
    new, cors, _ = fix(fx, 'wie z. B.in der Provinz')
    assert new == 'wie z. B. in der Provinz' and cors[0]['rule'] == 'abbrev_space'
    assert cors[0]['before'] == 'B.' and cors[0]['after'] == 'B. '


def test_digit_for_letter_uses_project_names(fx):
    new, cors, _ = fix(fx, 'PITSCHEN O8, Friedland OS')
    assert new == 'PITSCHEN OS, Friedland OS'


def test_ambiguous_candidates_are_flagged_not_changed(fx):
    new, cors, flags = fix(fx, 'im vesten der Kirche')
    assert new == 'im vesten der Kirche' and not cors
    f = flags[0]
    assert f['token'] == 'vesten' and f['reason'] == 'ambiguous' and f['severity'] == 'medium'
    assert {c['text'] for c in f['candidates']} >= {'besten', 'westen', 'festen'}
    assert f['resolution'] is None and 'vesten' in f['context']


def test_short_unknown_word_only_flagged(fx):
    new, cors, flags = fix(fx, 'Die Kirche lie nicht')
    assert 'lie' in new and not cors and flags[0]['token'] == 'lie'


def test_capitalised_unknown_word_with_weak_evidence_is_not_changed(lex):
    # 'Piasten' could be a name: i->l (0.7) gives a rare word, so a human decides
    lx = O.Lexicon(dict_dir='/nonexistent', vocab=(), use_hunspell=False,
                   freq_lines=['plasten 300', 'die 1900000000', 'kirche 3000000'])
    new, cors, flags = O.Fixer(lx, suggest=False).process('Die Piasten Kirche', 'u1')
    assert new == 'Die Piasten Kirche' and flags[0]['reason'] == 'name_risk'


def test_unknown_without_candidates_is_flagged_high_unless_capitalised_name(fx):
    _, _, flags = fix(fx, 'Die Kirche xqzvwk und Hamann')
    by = {f['token']: f for f in flags}
    assert by['xqzvwk']['severity'] == 'high' and by['Hamann']['severity'] == 'low'


def test_suspect_known_word_is_flagged_never_changed(lex):
    lx = O.Lexicon(dict_dir='/nonexistent', vocab=(), use_hunspell=False,
                   freq_lines=['fin 900', 'ein 900000000', 'die 1900000000'])
    new, cors, flags = O.Fixer(lx, suggest=False).process('Fin Portal', 'u1')
    assert new == 'Fin Portal' and flags[0]['reason'] == 'suspect' and flags[0]['candidates'][0]['text'] == 'Ein'


def test_human_resolution_and_accepted_words(lex):
    fx = O.Fixer(lex, suggest=False, accepted=['Hamann'])
    text = 'im vesten und im vesten bei Hamann xqzvwk'
    _, _, flags = fx.process(text, 'u1')
    assert [f['token'] for f in flags] == ['vesten', 'vesten', 'xqzvwk']     # Hamann is accepted
    res = {flags[0]['key']: 'westen', flags[1]['key']: '=', flags[2]['key']: 'Mörtel'}
    new, cors, left = fx.process(text, 'u1', res)
    assert new == 'im westen und im vesten bei Hamann Mörtel' and not left
    assert [c['rule'] for c in cors] == ['human', 'human']


def test_run_over_document_keeps_raw_text_and_skips_excluded(fx):
    doc = {'pages': [{'blocks': [
        {'kind': 'text', 'unit_id': 'u1', 'translation_status': 'translate'},
        {'kind': 'text', 'unit_id': 'u2', 'translation_status': 'exclude'}]}],
        'units': [{'unit_id': 'u1', 'text': 'Die Binfassung ist nichtmehr gut'},
                  {'unit_id': 'u2', 'text': 'Binfassung in der Zeichnung'}]}
    cors, flags = O.run(doc, fx)
    u1, u2 = doc['units']
    assert u1['text'] == 'Die Einfassung ist nicht mehr gut' and u1['text_raw'] == 'Die Binfassung ist nichtmehr gut'
    assert len(u1['ocr_corrections']) == 2 and {c['unit'] for c in cors} == {'u1'}
    assert 'text_raw' not in u2 and u2['text'] == 'Binfassung in der Zeichnung'


def test_review_file_roundtrip(tmp_path):
    p = tmp_path / 'ocr_review.json'
    p.write_text(json.dumps({'accepted_words': ['Hamann'],
        'resolved': [{'key': 'u1|Pitschen|1', 'token': 'Pitschen', 'resolution': '='}],
        'flags': [{'key': 'u1|vesten|1', 'resolution': 'westen'}, {'key': 'u1|xx|1', 'resolution': None}]}))
    res, acc = O.load_review(p)
    assert res == {'u1|vesten|1': 'westen', 'u1|Pitschen|1': '='} and acc == ['Hamann']
    assert O.load_review(tmp_path / 'missing.json') == ({}, [])


def test_helpers():
    assert O.dl_distance('Binfassung', 'Einfassung') == 1 and O.dl_distance('ab', 'ba') == 1
    assert O.recase('BOSDORF', 'bösdorf') == 'BÖSDORF' and O.recase('Haus', 'hans') == 'Hans'


def test_aggressive_policy_changes_more_but_still_flags_ties_and_hopeless_words():
    lx = O.Lexicon(dict_dir='/nonexistent', vocab=(), use_hunspell=False,
                   freq_lines=['plasten 300', 'die 1900000000', 'kirche 3000000', 'besten 900000', 'westen 800000',
                               'rang 500000', 'lie 1'])
    text = 'Die Piasten Kirche im vesten xqzvwk'
    strict = O.Fixer(lx, suggest=False).process(text, 'u1')
    aggr = O.Fixer(lx, suggest=False, aggressive=True).process(text, 'u1')
    assert 'Piasten' in strict[0] and strict[2][0]['reason'] == 'name_risk'          # a possible name: human decides
    assert aggr[0].startswith('Die Plasten Kirche')                                   # aggressive: changed
    for new, cors, flags in (strict, aggr):
        assert {f['token'] for f in flags} >= {'xqzvwk'}                              # hopeless: flagged in both modes
    assert 'vesten' in aggr[0] and 'vesten' in [f['token'] for f in aggr[2]]          # dead heat: still flagged


def test_policy_names():
    assert set(O.POLICIES) == {'strict', 'aggressive'}
    assert O.POLICIES['aggressive']['auto_max'] > O.POLICIES['strict']['auto_max']

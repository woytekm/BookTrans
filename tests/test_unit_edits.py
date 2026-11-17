import json

import pytest

import unit_edits as ue


def _unit(uid, raw, text=None):
    return {'unit_id': uid, 'text_raw': raw, 'text': text if text is not None else raw}


def _doc():
    return {'units': [_unit('u0001', 'Das Portal ist alt', 'Das Portal ist alt'),
                      _unit('u0002', 'Der Spitzbogen fin', 'Der Spitzbogen find')]}


def test_edit_is_tied_to_the_hash_of_the_ocr_text_and_applied():
    doc, edits = _doc(), ue.empty()
    assert ue.set_edit(edits, doc['units'][1], 'Der Spitzbogen fin.')
    e = edits['units']['u0002']
    assert e['base'] == ue.text_hash(doc['units'][1]) and e['text'] == 'Der Spitzbogen fin.'
    r = ue.apply_edits(doc, edits)
    u = doc['units'][1]
    assert r == {'applied': ['u0002'], 'stale': []}
    assert u['text'] == 'Der Spitzbogen fin.' and u['ocr_edited'] and u['text_ocrfix'] == 'Der Spitzbogen find'
    assert u['text_raw'] == 'Der Spitzbogen fin'                # what the OCR read is never touched
    ue.apply_edits(doc, edits)                                   # idempotent
    assert u['text_ocrfix'] == 'Der Spitzbogen find'


def test_an_edit_equal_to_the_pipeline_text_is_removed_and_revert_restores_it():
    doc, edits = _doc(), ue.empty()
    ue.set_edit(edits, doc['units'][1], 'xx')
    ue.apply_edits(doc, edits)
    u = doc['units'][1]
    assert not ue.set_edit(edits, u, 'Der Spitzbogen find') and edits['units'] == {}      # back to the pipeline text
    ue.revert(u)
    assert u['text'] == 'Der Spitzbogen find' and 'ocr_edited' not in u and 'text_ocrfix' not in u


def test_renumbered_units_keep_their_edit_and_changed_ocr_makes_it_stale():
    doc, edits = _doc(), ue.empty()
    ue.set_edit(edits, doc['units'][1], 'Der Spitzbogen fin.')
    renum = {'units': [_unit('u0001', 'Der Spitzbogen fin', 'Der Spitzbogen find'), _unit('u0002', 'Etwas Neues')]}
    assert ue.apply_edits(renum, edits) == {'applied': ['u0001'], 'stale': []}      # same hash, new number
    assert renum['units'][0]['text'] == 'Der Spitzbogen fin.' and renum['units'][1]['text'] == 'Etwas Neues'
    changed = {'units': [_unit('u0001', 'Das Portal'), _unit('u0002', 'Der Spitzbogen fim')]}   # the OCR read it differently
    assert ue.apply_edits(changed, edits) == {'applied': [], 'stale': ['u0002']}
    assert changed['units'][1]['text'] == 'Der Spitzbogen fim'
    twin = {'units': [_unit('u0007', 'same'), _unit('u0008', 'same')]}                # ambiguous hash: not guessed
    e2 = {'units': {'u0001': {'base': ue.text_hash(twin['units'][0]), 'text': 'x'}}}
    assert ue.apply_edits(twin, e2)['stale'] == ['u0001']


def test_save_and_load_round_trip_and_bad_files(tmp_path):
    p = tmp_path / 'review' / 'unit_edits.json'
    assert ue.load_edits(p) == ue.empty()
    e = ue.empty()
    e['units']['u1'] = {'base': 'h', 'text': 'Größe – „Zitat“', 'edited': 'now'}
    ue.save_edits(p, e)
    assert ue.load_edits(p) == e and not list(p.parent.glob('*.tmp'))
    assert 'Größe' in p.read_text(encoding='utf-8')               # not escaped to ö
    p.write_text('[1]')
    with pytest.raises(ValueError):
        ue.load_edits(p)
    p.write_text('{oops')
    with pytest.raises(ValueError):
        ue.load_edits(p)


def _review():
    return {'schema': '1.0', 'page': 1, 'units': [
        {'unit_id': 'u0001', 'role': 'paragraph', 'blocks': ['b1'], 'text_raw': 'a b', 'text': 'a b', 'edited': False},
        {'unit_id': 'u0002', 'role': 'heading', 'blocks': ['b2'], 'text_raw': 'c d', 'text': 'c d', 'edited': False}]}


def test_validate_accepts_text_changes_only():
    orig = _review()
    new = json.loads(json.dumps(orig))
    new['units'][1]['text'] = 'c D'
    assert ue.validate_review(orig, json.dumps(new)) == ({'u0002': 'c D'}, [])
    assert ue.validate_review(orig, json.dumps(orig)) == ({}, [])


@pytest.mark.parametrize('mutate, needle', [
    (lambda r: r['units'][0].__setitem__('text_raw', 'x'), 'text_raw'),
    (lambda r: r['units'][0].__setitem__('role', 'heading'), 'role'),
    (lambda r: r['units'][0].__setitem__('text', 5), 'string'),
    (lambda r: r['units'].pop(), 'added, removed'),
    (lambda r: r['units'].reverse(), 'added, removed'),
    (lambda r: r.__setitem__('page', 9), 'page'),
])
def test_validate_refuses_structural_changes(mutate, needle):
    orig = _review()
    new = json.loads(json.dumps(orig))
    mutate(new)
    changes, errors = ue.validate_review(orig, json.dumps(new))
    assert changes == {} and any(needle in e for e in errors), errors


def test_validate_reports_broken_json_with_position():
    changes, errors = ue.validate_review(_review(), '{"units": [')
    assert changes == {} and 'not valid JSON' in errors[0] and 'line' in errors[0]
    assert ue.validate_review(_review(), '[]')[1]


def test_ocrfix_applies_human_edits_after_the_correction(tmp_path):
    import ocrfix
    doc = {'units': [_unit('u0001', 'Das Portal ist alt', 'Das Portal ist alt'),
                     _unit('u0002', 'Der Spitzbogen fin', 'Der Spitzbogen find')], 'ocr_correction': {'version': 1}}
    edits = ue.empty()
    ue.set_edit(edits, doc['units'][1], 'Der Spitzbogen fin.')
    p = tmp_path / 'review' / 'unit_edits.json'
    ue.save_edits(p, edits)
    assert ocrfix.apply_human_edits(doc, None) is None
    r = ocrfix.apply_human_edits(doc, p)
    assert r['applied'] == ['u0002'] and doc['units'][1]['text'] == 'Der Spitzbogen fin.' and doc['units'][0]['text'] == 'Das Portal ist alt'
    assert doc['ocr_correction']['edits_applied'] == 1
    assert ocrfix.apply_human_edits({'units': [_unit('u0002', 'Der Spitzbogen fin')]}, tmp_path / 'missing.json')['applied'] == []


def test_booktrans_passes_edits_and_the_aggressive_flag_to_ocrfix(monkeypatch, tmp_path):
    import importlib.util
    import booktrans
    calls = []
    monkeypatch.setattr(booktrans, 'run', lambda cmd: calls.append([str(c) for c in cmd]))
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: object())
    monkeypatch.setattr(booktrans.Path, 'exists', lambda self: True)
    assert booktrans.ocr_correct(tmp_path, suggest=False, aggressive=True, edits=tmp_path / 'e.json')
    c = calls[0]
    assert '--aggressive' in c and '--no-suggest' in c and c[c.index('--edits') + 1].endswith('e.json')
    calls.clear()
    booktrans.ocr_correct(tmp_path, suggest=True)
    assert '--aggressive' not in calls[0] and '--edits' not in calls[0] and '--no-suggest' not in calls[0]

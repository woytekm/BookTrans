"""ocrreview.py: prompts, answer checking and acceptance, with a fake model (no server)."""
import argparse
import json

import pytest

import ocrreview as R


def doc():
    return {'pages': [{'blocks': [{'kind': 'text', 'unit_id': 'u1', 'role': 'paragraph'}]}],
            'units': [{'unit_id': 'u1', 'type': 'paragraph',
                       'text_raw': 'Die Binfassung ist nichtmehr gut, im vesten und Hamann.',
                       'text': 'Die Einfassung ist nicht mehr gut, im vesten und Hamann.',
                       'ocr_corrections': [
                           {'unit': 'u1', 'offset': 4, 'before': 'Binfassung', 'after': 'Einfassung', 'rule': 'x', 'cost': .7},
                           {'unit': 'u1', 'offset': 19, 'before': 'nichtmehr', 'after': 'nicht mehr', 'rule': 'x', 'cost': .5}]}]}


def review():
    return {'flags': [
        {'key': 'u1|vesten|1', 'unit': 'u1', 'offset': 37, 'token': 'vesten', 'reason': 'ambiguous', 'severity': 'medium',
         'candidates': [{'text': 'besten', 'cost': 1.0, 'rule': 'edit_distance'}, {'text': 'westen', 'cost': 1.0, 'rule': 'edit_distance'}],
         'context': '', 'resolution': None},
        {'key': 'u1|Hamann|1', 'unit': 'u1', 'offset': 48, 'token': 'Hamann', 'reason': 'unknown_name_or_word', 'severity': 'low',
         'candidates': [], 'context': '', 'resolution': None}]}


def args(**kw):
    d = dict(prompt=R.ROOT / 'prompts' / 'ocr_review_de.txt', glossary=[], redo=False, min_severity='low', only=None,
             dry_run=False, model='fake')
    d.update(kw)
    return argparse.Namespace(**d)


def test_offsets_are_mapped_into_the_corrected_text():
    d = doc()['units'][0]
    assert d['text'][R.corrected_offset(d, 37):][:6] == 'vesten'
    assert d['text'][R.corrected_offset(d, 48):][:6] == 'Hamann'


def test_marked_text_shows_flag_and_other_flags():
    d, r = doc(), review()
    t = R.mark_text(d['units'][0], r['flags'][0], [r['flags'][1]])
    assert t == 'Die Einfassung ist nicht mehr gut, im ⟦vesten⟧ und ‹Hamann›.'


def test_prompt_contains_whole_text_reason_and_candidates():
    d, r = doc(), review()
    d['units'][0]['_role'] = 'paragraph'
    p = R.build_prompt('BASE', d['units'][0], r['flags'][0], [r['flags'][1]], ['Pitschen'])
    assert p.startswith('BASE') and 'Die Einfassung ist nicht mehr gut' in p and '⟦vesten⟧' in p
    assert 'several corrections are about equally likely' in p and 'besten, westen' in p and 'Pitschen' in p


def test_parse_and_check_answers():
    assert R.parse_answer('<think>x</think>```json\n{"decision": "replace", "replacement": "westen", "confidence": "high"}\n```')['replacement'] == 'westen'
    assert R.parse_answer('no json') is None
    ok, why = R.check({'decision': 'Replace', 'replacement': ' westen ', 'confidence': 'HIGH', 'reason': 'r'}, 'vesten')
    assert ok['replacement'] == 'westen' and ok['confidence'] == 'high' and why is None
    assert R.check({'decision': 'replace', 'replacement': 'vesten'}, 'vesten')[0]['decision'] == 'keep'
    assert R.check({'decision': 'replace', 'replacement': ''}, 'x')[1]
    assert R.check({'decision': 'maybe'}, 'x')[1]
    assert R.check({'decision': 'replace', 'replacement': 'a\nb'}, 'x')[1]
    assert R.check({'decision': 'unsure'}, 'x')[0]['confidence'] == 'low'


def test_malformed_answer_is_retried_with_a_remark_then_unsure():
    seen = []

    def call(prompt):
        seen.append(prompt)
        return 'sorry' if len(seen) == 1 else '{"decision": "replace", "replacement": "westen", "confidence": "high", "reason": "ok"}'
    rec = R.propose_one('P', call, 'vesten')
    assert rec['decision'] == 'replace' and len(seen) == 2 and 'IMPORTANT' in seen[1]
    rec = R.propose_one('P', lambda p: 'never json', 'vesten')
    assert rec['decision'] == 'unsure' and 'unusable' in rec['reason']


def test_unknown_replacement_cannot_be_high():
    class Lex:
        def known(self, w):
            return w == 'westen'
    ans = '{"decision": "replace", "replacement": "%s", "confidence": "high", "reason": "r"}'
    assert R.propose_one('P', lambda p: ans % 'westen', 'vesten', Lex())['confidence'] == 'high'
    r = R.propose_one('P', lambda p: ans % 'wxsten', 'vesten', Lex())
    assert r['confidence'] == 'medium' and r['in_dictionary'] is False


def test_propose_fills_llm_uses_cache_and_skips_resolved(tmp_path):
    d, r = doc(), review()
    calls = []

    def call(prompt):
        calls.append(prompt)
        return ('{"decision": "keep", "replacement": "", "confidence": "high", "reason": "a name"}' if '⟦Hamann⟧' in prompt
                else '{"decision": "replace", "replacement": "westen", "confidence": "medium", "reason": "r"}')
    cache = {}
    assert R.propose(d, r, args(), call, None, cache) == 2
    assert r['flags'][0]['llm']['replacement'] == 'westen' and r['flags'][1]['llm']['decision'] == 'keep'
    r2 = review(); r2['flags'][1]['resolution'] = '='
    assert R.propose(doc(), r2, args(), call, None, cache) == 1 and len(calls) == 3      # resolved flag skipped; its neighbour's prompt changed, so no cache hit
    assert R.propose(doc(), review(), args(min_severity='medium'), call, None, cache) == 1


def test_accept_by_confidence():
    r = review()
    r['flags'][0]['llm'] = {'decision': 'replace', 'replacement': 'westen', 'confidence': 'medium'}
    r['flags'][1]['llm'] = {'decision': 'keep', 'replacement': '', 'confidence': 'high'}
    assert R.accept(r, 'high') == 1 and r['flags'][0]['resolution'] is None and r['flags'][1]['resolution'] == '='
    assert R.accept(r, 'medium') == 1 and r['flags'][0]['resolution'] == 'westen' and r['flags'][0]['resolved_by'] == 'llm'


def test_ocrfix_keeps_proposals_between_runs(tmp_path):
    import ocrfix
    p = tmp_path / 'ocr_review.json'
    p.write_text(json.dumps({'flags': [{'key': 'u1|vesten|1', 'llm': {'decision': 'keep'}}, {'key': 'u1|x|1'}]}))
    assert ocrfix.load_proposals(p) == {'u1|vesten|1': {'decision': 'keep'}}
    assert ocrfix.load_proposals(tmp_path / 'none.json') == {}

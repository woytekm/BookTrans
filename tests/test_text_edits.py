"""Human corrections of the translation: keyed by the German text, applied over the machine text, orphaned not lost."""
import json

import pytest

import gui_demo
import render_session as RS
import text_edits as TE


def _trans():
    return {'units': {'u1': {'source': 'Das Portal ist schlicht.', 'pl': 'Portal jest prosty,.'},
                      'u2': {'source': 'Der Turm  ist hoch.', 'pl': 'Wieża jest wysoka.'}}}


def test_edits_follow_the_german_not_the_unit_number(tmp_path):
    d = TE.empty()
    TE.set_edit(d, 'Das Portal ist schlicht.', 'u1', 'Portal jest prosty.')
    tr, applied, orphans = TE.apply(_trans(), d)
    assert applied == ['u1'] and not orphans
    assert tr['units']['u1'] == {'source': 'Das Portal ist schlicht.', 'pl': 'Portal jest prosty.',
                                 'pl_machine': 'Portal jest prosty,.', 'edited': True}
    assert _trans()['units']['u1']['pl'] == 'Portal jest prosty,.'                 # the input is not changed
    renumbered = {'units': {'u7': dict(_trans()['units']['u1'])}}                 # pipeline ran again
    assert TE.apply(renumbered, d)[1] == ['u7']
    assert TE.fingerprint('Der Turm  ist\nhoch.') == TE.fingerprint('Der Turm ist hoch.')   # whitespace folded
    changed = {'units': {'u1': dict(_trans()['units']['u1'], source='Das Portal ist reich.')}}
    tr, applied, orphans = TE.apply(changed, d)                                   # the OCR text was corrected
    assert not applied and orphans[0]['pl'] == 'Portal jest prosty.' and tr['units']['u1']['pl'] == 'Portal jest prosty,.'


def test_saving_the_machine_text_removes_the_edit_and_a_backup_is_kept(tmp_path):
    f = tmp_path / 'review' / 'text_edits.json'
    d = TE.empty()
    fp = TE.set_edit(d, 'Das Portal ist schlicht.', 'u1', 'Portal jest prosty.')
    TE.save(f, d)
    TE.set_edit(d, 'Das Portal ist schlicht.', 'u1', 'Portal jest prosty,.', machine='Portal jest prosty,.')
    TE.save(f, d)
    assert TE.load(f)['edits'] == {} and fp in json.loads(f.with_name('text_edits.json.bak').read_text())['edits']
    f.write_text('{bad', encoding='utf-8')
    with pytest.raises(ValueError):
        TE.load(f)


def test_warnings_use_the_translation_checks():
    assert TE.warnings('z owych czasów, – chodzi tu o kościoły') == ['comma and dash together']
    assert TE.warnings('Zob. <i>Deutsche Kunst</i>, 1923.') == []


def test_session_saves_corrections_and_render_uses_them(tmp_path):
    p = gui_demo.make_demo(tmp_path / 'p', translate=True)
    s = RS.RenderSession(p).load()
    s.relayout()
    cap = next(b for pg in s.doc['pages'] for b in pg['blocks'] if b.get('role') == 'caption' and b.get('unit_id'))
    uid = cap['unit_id']
    german, pl, machine, edited = s.text_of(uid)
    assert german and pl == machine and not edited
    warn = s.set_text(uid, 'Pierwszy, – poprawiony podpis')
    assert warn == ['comma and dash together']                                    # a warning, still saved
    assert s.text_of(uid)[1:] == ('Pierwszy, – poprawiony podpis', machine, True) and s.edited == [uid]
    plan = s.relayout()
    words = ' '.join(w for pg in plan['pages'] for ln in pg['lines'] if ln['unit'] == uid for w, _ in ln['words'])
    assert 'poprawiony' in words.lower()
    assert any('--text-edits' == c for c in p.render_command())                   # render.py gets them too
    s2 = RS.RenderSession(p).load()                                                # a new session reads the file
    assert s2.text_of(uid)[3]
    s.revert_text(uid)
    assert not s.text_of(uid)[3] and s.text_of(uid)[1] == machine and TE.load(p.text_edits_file)['edits'] == {}


def test_render_py_sets_the_corrections(tmp_path):
    import subprocess
    import sys
    p = gui_demo.make_demo(tmp_path / 'p', translate=True)
    s = RS.RenderSession(p).load()
    uid = next(b['unit_id'] for pg in s.doc['pages'] for b in pg['blocks'] if b.get('role') == 'caption' and b.get('unit_id'))
    s.set_text(uid, 'Podpis poprawiony ręcznie')
    cmd = p.render_command(pages='1', resolution='work')
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'text edits: 1 unit(s) with a human correction' in r.stdout + r.stderr
    plan = json.loads((p.render_dir / 'layout.json').read_text(encoding='utf-8'))
    assert any('ręcznie' in w for pg in plan['pages'] for ln in pg['lines'] if ln['unit'] == uid for w, _ in ln['words'])

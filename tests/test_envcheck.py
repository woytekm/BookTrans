"""envcheck.py: dictionary integrity, Tesseract parsing, severity logic (no real Tesseract needed)."""
from pathlib import Path

import pytest

import envcheck as E


def by_name(checks):
    return {c.name: c for c in checks}


def make_root(tmp_path, freq=60000, dic_ok=True, aff_ok=True):
    d = tmp_path / 'dictionaries'
    d.mkdir()
    (d / 'de-100k.txt').write_text(''.join('w%d %d\n' % (i, 100 - i % 90) for i in range(freq)), encoding='utf-8')
    if dic_ok:
        (d / 'de_DE_frami.dic').write_text('3\nHaus/N\nKirche/N\nPortal/NS\n', encoding='utf-8')
    if aff_ok:
        (d / 'de_DE_frami.aff').write_text('SET UTF-8\nSFX N Y 1\nSFX N 0 n .\n', encoding='utf-8')
    return tmp_path


def test_dictionary_names_match_what_ocrfix_downloads():
    pytest.importorskip('symspellpy')
    import ocrfix
    assert set(E.DICT_FILES) == set(ocrfix.SOURCES)


def test_intact_dictionaries_are_ok(tmp_path):
    c = by_name(E.check_dictionaries(make_root(tmp_path)))
    assert {x.status for x in c.values()} == {E.OK}
    assert '60,000' in c['de-100k.txt (frequency list)'].detail and '3 entries' in c['de_DE_frami.dic (Hunspell words)'].detail


def test_missing_dictionaries_fail_with_the_fetch_command(tmp_path):
    c = E.check_dictionaries(tmp_path)
    freq = by_name(c)['de-100k.txt (frequency list)']
    assert freq.status == E.FAIL and 'ocrfix.py fetch' in freq.hint
    assert E.worst(c) == E.FAIL


def test_missing_dictionaries_are_only_info_when_ocr_correction_is_off(tmp_path):
    assert {x.status for x in E.check_dictionaries(tmp_path, need_ocrfix=False)} == {E.INFO}


def test_damaged_dictionaries_are_noticed(tmp_path):
    root = make_root(tmp_path, freq=10)                                    # far too short
    (root / 'dictionaries' / 'de_DE_frami.dic').write_text('5000\nHaus\n')   # header says 5000, file has 1
    (root / 'dictionaries' / 'de_DE_frami.aff').write_text('<html>404</html>')
    c = by_name(E.check_dictionaries(root))
    assert c['de-100k.txt (frequency list)'].status == E.WARN
    assert c['de_DE_frami.dic (Hunspell words)'].status == E.WARN
    assert c['de_DE_frami.aff (Hunspell rules)'].status == E.WARN
    (root / 'dictionaries' / 'de-100k.txt').write_text('<html>not found</html>\n')
    assert by_name(E.check_dictionaries(root))['de-100k.txt (frequency list)'].status == E.FAIL


def test_parse_langs_both_output_styles():
    new = 'List of available languages in "/opt/local/share/tessdata/" (3):\ndeu\neng\nosd\n'
    assert E.parse_langs(new) == ('/opt/local/share/tessdata/', ['deu', 'eng', 'osd'])
    old = 'List of available languages (2):\neng\ndeu\n'
    assert E.parse_langs(old) == (None, ['eng', 'deu'])


def fake_tesseract(tmp_path, size, langs=('deu', 'osd'), on_path=True):
    td = tmp_path / 'tessdata'
    td.mkdir()
    (td / 'deu.traineddata').write_bytes(b'0' * size)
    out = 'List of available languages in "%s" (%d):\n%s\n' % (td, len(langs), '\n'.join(langs))
    def run(args, timeout=8):
        return (0, 'tesseract 5.3.4\n leptonica') if '--version' in args else (0, out)
    return (lambda: ('/opt/local/bin/tesseract', on_path)), run


def test_fast_model_is_warned_about(tmp_path):
    find, run = fake_tesseract(tmp_path, size=1_600_000)
    c = by_name(E.check_tesseract('deu', find, run))
    assert c['tesseract program'].status == E.OK and '5.3.4' in c['tesseract program'].detail
    assert c['language deu'].status == E.WARN and 'fast' in c['language deu'].detail and 'tessdata_best' in c['language deu'].hint


def test_larger_model_is_ok_but_not_claimed_to_be_best(tmp_path):
    find, run = fake_tesseract(tmp_path, size=8_600_000)
    c = by_name(E.check_tesseract('deu', find, run))
    assert c['language deu'].status == E.OK and 'cannot tell' in c['language deu'].detail


def test_missing_language_and_missing_program(tmp_path):
    find, run = fake_tesseract(tmp_path, size=9_000_000, langs=('osd',))
    assert by_name(E.check_tesseract('deu', find, run))['language deu'].status == E.FAIL
    (c,) = E.check_tesseract('deu', lambda: (None, False), run)
    assert c.status == E.FAIL and 'tesseract' in c.hint


def test_tesseract_outside_path_is_a_warning(tmp_path):
    find, run = fake_tesseract(tmp_path, size=9_000_000, on_path=False)
    c = by_name(E.check_tesseract('deu', find, run))['tesseract program']
    assert c.status == E.WARN and 'not on PATH' in c.detail


def test_glossary_checks(tmp_path):
    g = tmp_path / 'glossary'
    g.mkdir()
    (g / 'vocabulary.txt').write_text('# comment\nPitschen\n\nZiegenhals 5000\n', encoding='utf-8')
    (g / 'terms.csv').write_text('de,pl,kind\nPitschen,Byczyna,place\nZiegenhals,Głuchołazy,place\n', encoding='utf-8')
    (g / 'broken.csv').write_text('a,b\n1,2\n', encoding='utf-8')
    c = by_name(E.check_glossary(tmp_path))
    assert '2 book-specific' in c['glossary/vocabulary.txt'].detail
    assert c['glossary/terms.csv'].status == E.OK and '2 entries' in c['glossary/terms.csv'].detail
    assert c['glossary/broken.csv'].status == E.WARN


def test_assets_of_the_real_repository_are_complete():
    assert {c.status for c in E.check_assets()} == {E.OK}


def test_report_and_exit_logic():
    cs = [E.Check('g', 'a', E.OK, 'fine', ''), E.Check('g', 'b', E.WARN, 'meh', 'do x'), E.Check('h', 'c', E.INFO, 'fyi', '')]
    assert E.worst(cs) == E.WARN
    rep = E.format_report(cs)
    assert '-> do x' in rep and rep.index('g\n') < rep.index('h\n')
    assert E.worst(cs + [E.Check('h', 'd', E.FAIL, '', '')]) == E.FAIL

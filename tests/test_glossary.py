import csv

import glossary as G


def _entries(tmp_path, rows):
    p = tmp_path / 'g.csv'
    with open(p, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, G.FIELDS); w.writeheader()
        for r in rows:
            w.writerow({**{k: '' for k in G.FIELDS}, **r})
    return G.load([p])


def test_norm_folds_umlauts_and_accents():
    assert G.norm('Bösdorf') == G.norm('BOSDORF') == 'bosdorf'
    assert G.norm('Głuchołazy') == 'gluchołazy'.replace('ł', 'l') and G.norm('Łódź') == 'lodz'


def test_places_terms_and_compounds(tmp_path):
    m = G.Matcher(_entries(tmp_path, [
        {'de': 'Pitschen', 'pl': 'Byczyna', 'kind': 'place', 'status': 'confirmed'},
        {'de': 'Friedland OS', 'pl': 'Korfantów', 'kind': 'place', 'status': 'confirmed'},
        {'de': 'Kämpfer', 'pl': 'impost', 'kind': 'term', 'status': 'proposed'}]))
    hits = {e['pl']: mt for e, mt in m.find('In PITSCHEN und Friedland OS, am Portalkämpfer.')}
    assert set(hits) == {'Byczyna', 'Korfantów', 'impost'}


def test_place_names_need_a_capital_and_tolerate_ocr_slips(tmp_path):
    m = G.Matcher(_entries(tmp_path, [{'de': 'Ritterswalde', 'pl': 'Domaszkowice', 'kind': 'place', 'status': 'confirmed'}]))
    assert [e['pl'] for e, _ in m.find('in Rittersvalde stehen')] == ['Domaszkowice']      # one letter wrong
    assert m.find('ritterswalde im Text') == []                                             # lower case: not a name


def test_only_usable_entries_go_into_the_prompt(tmp_path):
    m = G.Matcher(_entries(tmp_path, [
        {'de': 'Albrechtsdorf', 'pl': 'A', 'kind': 'place', 'status': 'ambiguous'},
        {'de': 'Rundstab', 'pl': 'wałek', 'kind': 'term', 'status': 'disputed'},
        {'de': 'Hohlkehle', 'pl': 'kehla', 'kind': 'term', 'status': 'proposed'}]))
    hits = m.find('Albrechtsdorf, Rundstab und Hohlkehle')
    assert len(hits) == 3
    block = G.prompt_block(hits)
    assert 'kehla' in block and 'wałek' not in block and 'Albrechtsdorf' not in block


def test_check_accepts_inflected_forms(tmp_path):
    m = G.Matcher(_entries(tmp_path, [
        {'de': 'Pitschen', 'pl': 'Byczyna', 'kind': 'place', 'status': 'confirmed'},
        {'de': 'Ziegenhals', 'pl': 'Głuchołazy', 'kind': 'place', 'status': 'confirmed'}]))
    hits = m.find('Pitschen und Ziegenhals')
    assert G.check('w Byczynie i w Głuchołazach', hits) == []
    assert [e['pl'] for e in G.check('w Pitschen i w Głuchołazach', hits)] == ['Byczyna']


def test_import_marks_ambiguous_and_drops_duplicates(tmp_path):
    p = tmp_path / 'pairs.csv'
    p.write_text('german,polish\nAlbrechtsdorf,Pokrzywnik\nAlbrechtsdorf,Wojciechów\nAlt Damerow,Stare Dębno\nAlt Damerow,Stare Dębno\n', encoding='utf-8')
    ents = G.import_pairs(p)
    by = {(e['de'], e['pl']): e['status'] for e in ents}
    assert by[('Albrechtsdorf', 'Pokrzywnik')] == 'ambiguous' and by[('Albrechtsdorf', 'Wojciechów')] == 'ambiguous'
    assert by[('Alt Damerow', 'Stare Dębno')] == 'proposed' and len(ents) == 3

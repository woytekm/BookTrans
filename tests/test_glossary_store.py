"""The central glossary, the project layer, rejections, imports and the harvest of candidates from a translation."""
import json
from types import SimpleNamespace

import pytest

import glossary as G
import glossary_harvest as H
import glossary_store as GS
import translate as T


def test_central_folder_is_seeded_once_and_configurable(tmp_path, monkeypatch):
    d = GS.ensure_central()
    assert (d / 'terms.csv').exists() and GS.central_files()[0].name == 'terms.csv'
    n = len(GS.read_file(d / 'terms.csv'))
    assert n == len(GS.read_file(GS.REPO_GLOSSARY / 'terms.csv'))            # a copy of the repository's seed
    GS.add_central({'de': 'Kalkau', 'pl': 'Kałków', 'kind': 'place', 'status': 'confirmed'})
    GS.ensure_central()                                                        # not seeded again
    assert len(GS.read_file(d / 'terms.csv')) == n + 1
    monkeypatch.delenv('BOOKTRANS_GLOSSARY')
    other = GS.set_central_dir(tmp_path / 'elsewhere')
    assert GS.central_dir() == other == (tmp_path / 'elsewhere').resolve() and (other / 'terms.csv').exists()
    assert json.loads(GS.config_file().read_text())['glossary_dir'] == str(other)


def test_project_layer_wins_and_promote_moves_it(tmp_path):
    proj = tmp_path / 'p'
    GS.add_central({'de': 'Kalkau', 'pl': 'Kałków', 'kind': 'place'})
    GS.add_project(proj, {'de': 'Kalkau', 'pl': 'Kalków (ten tom)', 'kind': 'place', 'status': 'confirmed'})
    files = [f for _, f in GS.layer_files(proj)]
    assert files[0] == GS.project_file(proj)
    e = [x for x in G.load(files) if x['de'] == 'Kalkau']
    assert len(e) == 1 and e[0]['pl'] == 'Kalków (ten tom)'                   # first definition wins
    rows = [r for r in GS.all_entries(proj) if r['de'] == 'Kalkau']
    assert [(r['_layer'], r['_shadowed']) for r in rows] == [('project', False), ('central', True)]
    GS.promote(proj, ('kalkau', 'place'))
    assert not GS.read_file(GS.project_file(proj))
    assert [r['pl'] for r in GS.read_file(GS.central_dir() / 'terms.csv') if r['de'] == 'Kalkau'] == ['Kalków (ten tom)']


def test_upsert_delete_and_validation(tmp_path):
    f = tmp_path / 'g.csv'
    assert GS.upsert(f, {'de': 'Wimperg', 'pl': 'wimperga', 'kind': 'term'}) == 'added'
    assert GS.upsert(f, {'de': 'Wimperg', 'pl': 'wimperga', 'kind': 'term', 'status': 'confirmed'}) == 'changed'
    assert GS.upsert(f, {'de': 'Wimperge', 'pl': 'wimperga', 'kind': 'term'}, old_key=('wimperg', 'term')) == 'changed'
    assert [r['de'] for r in GS.read_file(f)] == ['Wimperge']
    assert GS.delete(f, ('wimperge', 'term')) and not GS.read_file(f)
    for bad in ({'de': '', 'pl': 'x'}, {'de': 'a', 'pl': 'b', 'kind': 'thing'}, {'de': 'a', 'pl': 'b', 'status': 'maybe'}):
        with pytest.raises(ValueError):
            GS.upsert(f, bad)


def test_import_keeps_human_entries_and_marks_ambiguous(tmp_path):
    GS.add_central({'de': 'Pitschen', 'pl': 'Byczyna (by hand)', 'kind': 'place', 'status': 'confirmed'})
    src = tmp_path / 'pairs.csv'
    src.write_text('german,polish\nPitschen,Byczyna\nNeudorf,Nowa Wieś\nNeudorf,Nowa Wieś Mała\nKalkau,Kałków\n', encoding='utf-8')
    added, skipped = GS.import_pairs_central([src])
    assert (added, skipped) == (2, 1)
    rows = {r['de']: r for r in GS.read_file(GS.central_dir() / 'places.csv')}
    assert rows['Neudorf']['status'] == 'ambiguous' and rows['Kalkau']['status'] == 'proposed'
    assert 'Pitschen' not in rows


def test_harvest_filters_known_invented_and_rejected_and_aggregates():
    entries = G.load(GS.central_files())                                       # the seed knows Pitschen = Byczyna
    units = [('u1', 'Das Portal in Pitschen und das Portal in Kalkau haben einen Wimperg.'),
             ('u2', 'In Kalkau ist der Wimperg schlicht.')]
    trans = {'u1': {'pl': 'Portal w Byczynie i portal w Kałkowie mają wimpergę.'}, 'u2': {'pl': 'W Kałkowie wimperga jest prosta.'}}
    answers = {
        'u1': '```json\n[{"de": "Pitschen", "pl": "Byczyna", "kind": "place"}, {"de": "Kalkau", "pl": "Kałków", "kind": "place"},'
              ' {"de": "Wimperg", "pl": "wimperga", "kind": "term"}, {"de": "Breslau", "pl": "Wrocław", "kind": "place"},'
              ' {"de": "Portal", "pl": "portal", "kind": "term"}]\n```',
        'u2': '[{"de": "Kalkau", "pl": "Kałkowo", "kind": "place"}, {"de": "Wimperg", "pl": "wimperga", "kind": "term"}]'}
    asked = []

    def call(prompt):
        uid = 'u1' if 'Pitschen' in prompt else 'u2'
        asked.append(uid)
        return answers[uid]
    GS.reject('Portal', 'term')
    cache = {}
    cands, n = H.harvest(units, trans, entries, call, 'opus', cache, GS.rejected())
    by = {c['de']: c for c in cands}
    assert set(by) == {'Kalkau', 'Wimperg'}             # Pitschen known, Breslau not in the text, Portal rejected
    assert by['Kalkau']['pl'] == 'Kałków' and by['Kalkau']['alternatives'] == ['Kałkowo'] and by['Kalkau']['units'] == ['u1', 'u2']
    assert by['Wimperg']['count'] == 2 and n == 2
    H.harvest(units, trans, entries, call, 'opus', cache, GS.rejected())
    assert asked == ['u1', 'u2']                         # the second run is answered from the cache


def test_decide_accepts_into_a_layer_or_rejects(tmp_path):
    f = tmp_path / 'c.json'
    H.write_candidates(f, [{'de': 'Kalkau', 'pl': 'Kałków', 'kind': 'place', 'count': 2, 'alternatives': [], 'units': ['u1']},
                           {'de': 'Wimperg', 'pl': 'wimperga', 'kind': 'term', 'count': 1, 'alternatives': [], 'units': ['u2']},
                           {'de': 'Portal', 'pl': 'portal', 'kind': 'term', 'count': 1, 'alternatives': [], 'units': ['u2']}])
    H.decide(f, ('kalkau', 'place'), 'central', entry={'pl': 'Kałków', 'district': 'Neisse'})
    H.decide(f, ('wimperg', 'term'), 'project', project_root=tmp_path / 'p')
    H.decide(f, ('portal', 'term'), 'reject')
    assert H.read_candidates(f) == []
    k = [r for r in GS.read_file(GS.central_dir() / 'terms.csv') if r['de'] == 'Kalkau'][0]
    assert k['status'] == 'confirmed' and k['district'] == 'Neisse' and 'harvested' in k['source']
    assert GS.read_file(GS.project_file(tmp_path / 'p'))[0]['de'] == 'Wimperg'
    assert ('portal', 'term') in GS.rejected()


def test_translate_harvest_mode_writes_candidates(tmp_path):
    doc = {'pages': [{'blocks': [{'kind': 'text', 'unit_id': 'u1', 'role': 'paragraph', 'translation_status': 'translate',
                                  'typography': {}}]}],
           'units': [{'unit_id': 'u1', 'text': 'In Kalkau ist der Wimperg schlicht.'}]}
    out = tmp_path / 'translations.json'
    out.write_text(json.dumps({'units': {'u1': {'pl': 'W Kałkowie wimperga jest prosta.'}}}), encoding='utf-8')
    a = SimpleNamespace(glossary=None, output=out, harvest=tmp_path / 'cands.json', only=None, model='m', document='d.json')
    cands = T.harvest(doc, a, call=lambda p: '[{"de": "Kalkau", "pl": "Kałków", "kind": "place"}]')
    assert [c['de'] for c in cands] == ['Kalkau'] and H.read_candidates(tmp_path / 'cands.json')[0]['pl'] == 'Kałków'
    assert (tmp_path / 'cands.cache.json').exists()

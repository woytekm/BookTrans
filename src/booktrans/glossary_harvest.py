"""Glossary candidates harvested from a translation (no GUI toolkit; the model call is passed in).

After a translation run, each translated unit (German text + its Polish translation) is shown to the model once more
with one question: which proper names (places, persons, buildings, institutions) and art-history / architecture terms
occur in the German, and which Polish form does the translation use?  The answer (JSON) is filtered:

* what the glossary already has (any layer) is dropped, and what a human rejected (``rejected.csv``);
* a German word that does not occur in the German text is dropped (the model invented or normalised it too far);
* ordinary words the model listed anyway end up as candidates a human rejects once - then never again.

What is left is aggregated over the book: one candidate per German word and kind, with the Polish form used most often,
the other forms seen and the units it came from.  A human then accepts each one into the central glossary (or into
the project's layer), corrects it, or rejects it (GUI: *Glossary…* -> *Candidates*).

Answers are cached per (model, prompt, German, Polish), so running the harvest again after a partial re-translation only
asks about the units whose text changed.  The run is started with ``translate.py --harvest`` (same model settings as
the translation).
"""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import glossary as G
import glossary_store as GS

SCHEMA = 'booktrans.glossary_candidates/1'
KINDS = GS.KINDS
PROMPT = """Below is a German text from an art-history book and its Polish translation.

List the proper names (places, persons, buildings, churches, institutions) and the specialised art-history and
architecture terms that occur in the German text, each with the Polish form the translation uses. Give both in the
nominative singular (for a place: the Polish place name; for a term: the Polish term). Skip ordinary words, numbers,
figure references and titles of cited works.

Answer with JSON only, no other text: a list of objects {"de": "...", "pl": "...", "kind": "place" | "person" | "term"}.
Answer [] when there is nothing to list.

German:
<<<
%s
>>>

Polish:
<<<
%s
>>>"""


def parse_answer(text):
    """The list of ``{de, pl, kind}`` in a model answer (code fences and chatter around the JSON are tolerated)."""
    t = re.sub(r'<think>.*?</think>', '', text or '', flags=re.S)
    m = re.search(r'\[.*\]', t, flags=re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict):
            continue
        de, pl = str(d.get('de') or '').strip(), str(d.get('pl') or '').strip()
        kind = str(d.get('kind') or 'term').strip().lower()
        if de and pl:
            out.append({'de': de, 'pl': pl, 'kind': kind if kind in KINDS else 'term'})
    return out


def in_text(de, text):
    """True when the German word (case, umlauts and accents folded; a term also inside a compound) occurs in ``text``."""
    return G.norm(de) in G.norm(text)


def known(item, matcher, have_keys):
    """True when the glossary (any layer) already covers ``item``."""
    if GS.key_of(item) in have_keys:
        return True
    hits = matcher.find(item['de'])
    return any(G.norm(m.split(' (')[0]) == G.norm(item['de']) or e['kind'] == item['kind'] and G.norm(e['de']) == G.norm(item['de'])
               for e, m in hits)


def cache_key(model, de, pl):
    return hashlib.sha256('\x1f'.join([model, PROMPT, de, pl]).encode('utf-8')).hexdigest()


def harvest(units, translations, entries, call, model='', cache=None, rejected=frozenset(), log=None):
    """``units``: [(unit id, German text)]; ``translations``: {unit id: {pl: ...}}; ``entries``: the glossary
    (``glossary.load`` of all layers); ``call``: prompt -> answer; ``cache``: a dict kept between runs (changed in
    place).  -> candidates (see the module docstring), most frequent first."""
    cache = {} if cache is None else cache
    matcher = G.Matcher(entries)
    have = {GS.key_of(e) for e in entries}
    agg = {}
    n = 0
    for uid, de_text in units:
        pl_text = ((translations.get(uid) or {}).get('pl') or '').strip()
        if not de_text.strip() or not pl_text:
            continue
        k = cache_key(model, de_text, pl_text)
        if k not in cache:
            n += 1
            if log:
                log('%s: asking for names and terms (%d chars)' % (uid, len(de_text)))
            cache[k] = call(PROMPT % (de_text, pl_text))
        for it in parse_answer(cache[k]):
            if not in_text(it['de'], de_text) or GS.key_of(it) in rejected or known(it, matcher, have):
                continue
            c = agg.setdefault(GS.key_of(it), {'de': it['de'], 'kind': it['kind'], 'forms': Counter(), 'units': []})
            c['forms'][it['pl']] += 1
            if uid not in c['units']:
                c['units'].append(uid)
    out = []
    for c in agg.values():
        forms = c['forms'].most_common()
        out.append({'de': c['de'], 'pl': forms[0][0], 'kind': c['kind'], 'count': sum(v for _, v in forms),
                    'alternatives': [f for f, _ in forms[1:]], 'units': c['units']})
    out.sort(key=lambda c: (-c['count'], G.norm(c['de'])))
    return out, n


def write_candidates(path, candidates, document=''):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(json.dumps({'schema': SCHEMA, 'document': str(document), 'candidates': candidates},
                              ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    tmp.replace(p)


def read_candidates(path):
    try:
        d = json.loads(Path(path).read_text(encoding='utf-8'))
        return d.get('candidates', []) if isinstance(d, dict) else []
    except (OSError, ValueError):
        return []


def decide(path, key, action, project_root=None, entry=None):
    """A human's decision about one candidate (``key``: (normalised German, kind)), which then leaves the list.
    ``action``: 'central' / 'project' (into that glossary layer, ``entry`` = the corrected {de, pl, kind, status,
    district, variants, note}; default the candidate as it is, status confirmed) or 'reject' (``rejected.csv``)."""
    cands = read_candidates(path)
    c = next((c for c in cands if GS.key_of(c) == key), None)
    if c is None:
        raise ValueError('no such candidate: %s' % key[0])
    if action == 'reject':
        GS.reject(c['de'], c['kind'], note='harvest: %s' % c['pl'])
    else:
        e = dict(entry or {})
        e.setdefault('de', c['de']); e.setdefault('pl', c['pl']); e.setdefault('kind', c['kind'])
        e.setdefault('status', 'confirmed')
        e.setdefault('source', 'harvested from the translation (%s), checked by a human' % ', '.join(c['units'][:3]))
        if action == 'central':
            GS.add_central(e)
        elif action == 'project':
            if project_root is None:
                raise ValueError('no project for a project entry')
            GS.add_project(project_root, e)
        else:
            raise ValueError('unknown action %r' % action)
    rest = [x for x in cands if GS.key_of(x) != key]
    d = {}
    try:
        d = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    write_candidates(path, rest, d.get('document', ''))
    return c

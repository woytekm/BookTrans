"""Human corrections of the Polish translation (no GUI toolkit, testable alone).

A human polishes the translation of a block (a comma, a word) in the render window.  The corrected text is kept apart
from the machine translation, in ``review/text_edits.json``, so that a new translation run never overwrites it::

    {"schema": "booktrans.text_edits/1",
     "edits": {"<fingerprint of the German text>": {"pl": "corrected Polish", "unit": "u0040", "saved": "2026-11-19T08:40"}}}

An edit is keyed by a fingerprint of the **German text the unit was translated from**, not by the unit number: unit
numbers change when the pipeline runs again, the German of a block does not (unless its OCR is corrected).  ``unit``
and ``saved`` are only there for a human reading the file.  A unit whose German changed no longer matches its edit:
the edit is *orphaned* - listed, never applied to a text it was not written for, never deleted silently.

``apply`` puts the edits over the machine translation (``translations.json`` stays as the translation wrote it); the
renderer and the render window use the result.  Every save keeps one backup (``text_edits.json.bak``).
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

SCHEMA = 'booktrans.text_edits/1'


def fingerprint(german):
    """Short, stable id of a German text (whitespace folded)."""
    t = re.sub(r'\s+', ' ', str(german or '')).strip()
    return hashlib.sha1(t.encode('utf-8')).hexdigest()[:16]


def empty():
    return {'schema': SCHEMA, 'edits': {}}


def load(path):
    p = Path(path)
    if not p.exists():
        return empty()
    try:
        d = json.loads(p.read_text(encoding='utf-8'))
    except ValueError as e:
        raise ValueError('%s does not parse: %s' % (p, e))
    if not isinstance(d, dict) or not isinstance(d.get('edits'), dict):
        raise ValueError('%s is not a text edits file' % p)
    return d


def save(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():                                     # one backup of the previous state
        os.replace(str(p), str(p.with_name(p.name + '.bak')))
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    os.replace(str(tmp), str(p))


def german_of(uid, trans_units, doc=None):
    """The German text unit ``uid`` was translated from: the translation's ``source``, else the document's text."""
    src = (trans_units.get(uid) or {}).get('source')
    if src:
        return src
    for u in (doc or {}).get('units', []):
        if u.get('unit_id') == uid:
            return u.get('text', '')
    return ''


def apply(trans, edits, doc=None):
    """The translation with the human edits over it: (translation, applied unit ids, orphaned edits).  Edited units
    get ``pl`` = the human text, ``pl_machine`` = the translation's, ``edited`` = True.  ``trans`` is not changed."""
    units = dict(trans.get('units', {}))
    by_fp = {}
    for uid in units:
        by_fp.setdefault(fingerprint(german_of(uid, units, doc)), []).append(uid)
    applied, used = [], set()
    for fp, e in edits.get('edits', {}).items():
        for uid in by_fp.get(fp, []):
            u = dict(units[uid])
            u['pl_machine'] = u.get('pl', '')
            u['pl'] = e.get('pl', '')
            u['edited'] = True
            units[uid] = u
            applied.append(uid)
            used.add(fp)
    orphans = [dict(e, fingerprint=fp) for fp, e in edits.get('edits', {}).items() if fp not in used]
    return dict(trans, units=units), sorted(applied), orphans


def set_edit(data, german, uid, pl, machine=None):
    """Store a human text for the unit with this German (``pl`` equal to the ``machine`` text removes the edit).
    -> the fingerprint."""
    fp = fingerprint(german)
    if machine is not None and pl.strip() == str(machine).strip():
        data['edits'].pop(fp, None)
    else:
        data['edits'][fp] = {'pl': pl, 'unit': uid, 'saved': time.strftime('%Y-%m-%dT%H:%M')}
    return fp


def remove(data, fp):
    return data['edits'].pop(fp, None) is not None


def warnings(pl):
    """What the translation checks would say about a human text (``translate.punctuation_problems`` and
    ``tag_problems``): shown as a warning, the human decides."""
    import translate as T
    out = list(T.punctuation_problems(pl))
    tags = T.tag_problems(pl)
    if tags:
        out.append(tags)
    return out

#!/usr/bin/env python3
"""Human edits of unit texts (no GUI toolkit, testable alone).

A human compares the scan with the OCR-corrected text of a unit (paragraph) and fixes it.  The fix is stored apart from
the pipeline output, in ``review/unit_edits.json`` of the GUI project::

    {"schema": "1.0", "units": {"u0004": {"base": "<sha1 of text_raw>", "text": "...", "edited": "2026-11-17T21:30:02"}}}

``apply_edits`` puts the edits on a document (used by the GUI when it loads a project and meant for ``ocrfix.py run``,
so that a re-run reproduces the edits).  ``base`` ties an edit to the OCR text it was made against: unit ids are renumbered
on every pipeline run, so an edit is attached to the unit with the same hash, and not applied at all when no unit has it
(*stale*: the OCR changed under it).

``validate_review`` checks a page's review JSON as edited in the GUI: only a unit's ``text`` may change.
"""
import hashlib
import json
import os
import time
from pathlib import Path

SCHEMA = '1.0'


def text_hash(unit):
    """Hash of what the OCR read (``text_raw``; ``text`` for a document that was never corrected)."""
    return hashlib.sha1(unit.get('text_raw', unit.get('text', '')).encode('utf-8')).hexdigest()


def empty():
    return {'schema': SCHEMA, 'units': {}}


def load_edits(path):
    """The edits file, or an empty set when there is none.  Raises ``ValueError`` for a file that is not an edits file."""
    p = Path(path)
    if not p.exists():
        return empty()
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise ValueError('cannot read %s: %s' % (p, e))
    if not isinstance(data, dict) or not isinstance(data.get('units'), dict):
        raise ValueError('%s is not a unit edits file' % p)
    return data


def save_edits(path, edits):
    """Atomic write (a crash must not leave half a file: this is human work)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(json.dumps(edits, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    os.replace(str(tmp), str(p))


def set_edit(edits, unit, text):
    """Record ``text`` as the human text of ``unit`` (a unit of the document *without* this edit applied).
    An edit that equals the pipeline's text is removed instead.  -> True when an edit is stored."""
    base_text = unit.get('text_ocrfix', unit['text'])
    if text == base_text:
        edits['units'].pop(unit['unit_id'], None)
        return False
    edits['units'][unit['unit_id']] = {'base': text_hash(unit), 'text': text,
                                       'edited': time.strftime('%Y-%m-%dT%H:%M:%S')}
    return True


def apply_edits(doc, edits):
    """Apply ``edits`` to ``doc`` in place.  -> ``{'applied': [unit ids], 'stale': [edit keys]}``.

    The unit gets ``text`` = the human text, ``ocr_edited`` = True and ``text_ocrfix`` = the text the pipeline made
    (kept so that an edit can be undone).  Idempotent: a document that already carries the edit is left as it is."""
    by_id = {u['unit_id']: u for u in doc.get('units', [])}
    by_hash = {}
    for u in doc.get('units', []):
        by_hash.setdefault(text_hash(u), []).append(u)
    applied, stale = [], []
    for key, e in edits.get('units', {}).items():
        u = by_id.get(key)
        if u is None or text_hash(u) != e.get('base'):
            same = by_hash.get(e.get('base'), [])
            u = same[0] if len(same) == 1 else None            # renumbered; ambiguous hashes are not guessed
        if u is None:
            stale.append(key)
            continue
        if not u.get('ocr_edited'):
            u['text_ocrfix'] = u['text']
        u['text'] = e['text']
        u['ocr_edited'] = True
        applied.append(u['unit_id'])
    return {'applied': applied, 'stale': stale}


def revert(unit):
    """Take a human edit off a unit (back to the text the pipeline made)."""
    if unit.get('ocr_edited'):
        unit['text'] = unit.pop('text_ocrfix', unit['text'])
        unit.pop('ocr_edited', None)


def validate_review(original, text):
    """Check the edited review JSON of a page against the one that was shown.

    -> ``(changes, errors)``: ``changes`` maps unit id -> new text for every unit whose ``text`` differs; ``errors`` is a
    list of readable messages (empty when the JSON may be saved).  Only ``text`` of a unit may change."""
    try:
        new = json.loads(text)
    except ValueError as e:
        return {}, ['not valid JSON: %s' % e]
    if not isinstance(new, dict) or not isinstance(new.get('units'), list):
        return {}, ['the top level must be an object with a "units" list']
    errors = []
    for k in original:
        if k != 'units' and new.get(k) != original[k]:
            errors.append('"%s" must not be changed' % k)
    ou, nu = original['units'], new['units']
    if [u.get('unit_id') if isinstance(u, dict) else None for u in nu] != [u['unit_id'] for u in ou]:
        errors.append('units must not be added, removed, renamed or reordered')
        return {}, errors
    changes = {}
    for o, n in zip(ou, nu):
        for k in set(o) | set(n):
            if k != 'text' and n.get(k) != o.get(k):
                errors.append('%s: "%s" must not be changed' % (o['unit_id'], k))
        if not isinstance(n.get('text'), str):
            errors.append('%s: "text" must be a string' % o['unit_id'])
        elif n['text'] != o['text']:
            changes[o['unit_id']] = n['text']
    return ({} if errors else changes), errors


if __name__ == '__main__':
    import sys
    e = load_edits(sys.argv[1])
    print('%d edits' % len(e['units']))

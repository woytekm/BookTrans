#!/usr/bin/env python3
"""Human decisions about OCR flags, kept outside the pipeline output (no GUI toolkit, testable alone).

``ocrfix.py`` flags words it cannot decide.  A human answers in the GUI; the answer is stored in
``review/ocr_decisions.json`` of the project (``booktrans.py`` deletes its result folder on a full run, ``review/`` is not touched)::

    {"schema": "1.0",
     "resolutions": {"u0007|vesten|1": "=", "u0009|Hamann|1": "Hamann"},      # flag key -> "=" (accept as it is) or the right word
     "accepted_words": ["Pitschen"]}                                           # accepted everywhere

``ocrfix.py run --decisions FILE`` merges them with what ``ocr_review.json`` already knows (the decisions win), so the next
pipeline run (``--resume``) puts them into ``document.corrected.json``.  Until then the GUI shows the flag as decided.
A flag key is ``unit|token|occurrence``; unit ids stay the same as long as stages 1-4 are not re-run on other pages.
"""
import json
import os
from pathlib import Path

SCHEMA = '1.0'
ACCEPT = '='                                   # "the word is right as it is"


def empty():
    return {'schema': SCHEMA, 'resolutions': {}, 'accepted_words': []}


def load_decisions(path):
    """The decisions file, or empty ones when there is none.  Raises ``ValueError`` for a file of another kind."""
    p = Path(path)
    if not p.exists():
        return empty()
    try:
        d = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise ValueError('cannot read %s: %s' % (p, e))
    if not isinstance(d, dict) or not isinstance(d.get('resolutions', {}), dict) or not isinstance(d.get('accepted_words', []), list):
        raise ValueError('%s is not an OCR decisions file' % p)
    d.setdefault('resolutions', {})
    d.setdefault('accepted_words', [])
    return d


def save_decisions(path, d):
    """Atomic write: this is human work."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(str(tmp), str(p))


def resolve(d, key, resolution=ACCEPT):
    """Decide one flag: ``"="`` accepts the word, anything else is the word to put in its place."""
    d['resolutions'][key] = resolution


def unresolve(d, key):
    d['resolutions'].pop(key, None)


def accept_word(d, word):
    """Accept ``word`` everywhere (case-insensitive, like ocrfix)."""
    if word.lower() not in {w.lower() for w in d['accepted_words']}:
        d['accepted_words'].append(word)


def forget_word(d, word):
    d['accepted_words'] = [w for w in d['accepted_words'] if w.lower() != word.lower()]


def status(d, key, token):
    """How a flag stands: ``None`` (open), ``'accepted'`` (as it is), ``'replaced:<word>'`` or ``'word accepted'``."""
    r = d['resolutions'].get(key)
    if r is not None:
        return 'accepted' if r == ACCEPT else 'replaced:%s' % r
    if token and token.lower() in {w.lower() for w in d['accepted_words']}:
        return 'word accepted'
    return None


def merge(res, accepted, d):
    """Add the decisions to what ``ocr_review.json`` gave ``ocrfix`` (``load_review``).  -> (res, accepted); the decisions win."""
    res = dict(res)
    res.update(d.get('resolutions', {}))
    return res, list(accepted) + [w for w in d.get('accepted_words', []) if w not in accepted]

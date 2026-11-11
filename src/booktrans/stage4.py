#!/usr/bin/env python3
"""Stage 4: link paragraphs across page breaks and build translation units.

Input: one or more Stage 3 JSON files, in book order (page order inside each
file is kept; files are concatenated).  A paragraph that runs over a page (or
spread) boundary is one sentence flow, so it must be translated as one unit.

For every page the last and the first *body paragraph* are the only candidates:

* the earlier one must not end a sentence (no ``. ! ? : ;`` or closing quote
  after trimming), or must end in a hyphen;
* the later one must start with a lower-case letter, or the earlier one must
  end in a hyphen.

Hyphen handling when joining (``dehyphenate``), applied inside units as well as
across page breaks:

* ``Spitz-`` + ``bogen``            -> ``Spitzbogen`` (hyphen removed);
* ``Gra-`` + ``Nit`` (upper case)   -> ``Gra-Nit`` kept without space;
* ``Werk-`` + ``und Form-``         -> ``Werk- und Form-`` (suspended compound:
  next word is und/oder/bzw/sowie/noch, hyphen kept, space inserted).

Every join that touched a hyphen is recorded in ``joins`` for audit.

Output (``units.json``): ``units`` (id, text, source object ids, pages,
``dehyphenated`` flag) plus ``pages`` with ``continues_from`` / ``continues_to``
on the linked paragraphs.  Illustration-associated text is excluded; captions
are separate units.
"""
import argparse
import json
import re
from pathlib import Path

CONJ = {'und', 'oder', 'bzw', 'bzw.', 'sowie', 'noch'}
SENTENCE_END = tuple('.!?:;') + ('»', '“', '"', "'")


def ends_hyphen(t):
    return t.rstrip().endswith(('-', '‐', '¬'))


def ends_sentence(t):
    t = t.rstrip().rstrip(')]')
    return t.endswith(SENTENCE_END)


def join_pair(a, b):
    """Join text ``a`` (ending in a hyphen) with ``b``. Returns (text, rule)."""
    a = a.rstrip(); b = b.lstrip()
    stem = a[:-1]
    first = re.match(r"[^\W\d_]+\.?", b)
    word = first.group(0).lower() if first else ''
    if word in CONJ:
        return a + ' ' + b, 'suspended_compound'
    if b[:1].isupper():
        return a + b, 'kept_hyphen_uppercase'
    return stem + b, 'removed_hyphen'


def dehyphenate(lines):
    """Join OCR lines of one paragraph into text. Returns (text, joins)."""
    text = ''; joins = []
    for ln in lines:
        t = ln.strip()
        if not text:
            text = t; continue
        if ends_hyphen(text):
            text, rule = join_pair(text, t)
            joins.append({'rule': rule})
        else:
            text += ' ' + t
    return text, joins


def body_paragraphs(page):
    out = [r for r in page['regions']
           if r['type'] == 'paragraph' and r.get('translation_status') == 'translate']
    return sorted(out, key=lambda r: (r['bbox'][0] // 200, r['bbox'][1]))


def continues(prev, nxt):
    p = prev['text'].rstrip(); n = nxt['text'].lstrip()
    if not p or not n:
        return False
    if ends_hyphen(p):
        return True
    return (not ends_sentence(p)) and n[:1].islower()


def link_pages(pages):
    """Set continues_from / continues_to (object ids) over a list of page dicts."""
    prev = None
    for page in pages:
        paras = body_paragraphs(page)
        if not paras:
            continue
        first = paras[0]
        if prev is not None and continues(prev, first):
            prev['continues_to'] = first['object_id']
            first['continues_from'] = prev['object_id']
        prev = paras[-1]
    return pages


def build_units(pages):
    units = []; chain_of = {}
    seq = [(p, r) for p in pages for r in p['regions']]
    # chains of linked paragraphs
    chain = None
    for page, r in seq:
        if r.get('translation_status') != 'translate':
            continue
        if r.get('continues_from') in chain_of:
            chain = chain_of[r['continues_from']]
            chain['parts'].append((page, r)); chain_of[r['object_id']] = chain; continue
        chain = {'parts': [(page, r)]}
        chain_of[r['object_id']] = chain
        units.append(chain)
    out = []
    for i, c in enumerate(units, 1):
        text = ''; joins = []
        for k, (page, r) in enumerate(c['parts']):
            t, j = dehyphenate([l['text'] for l in r['lines']])
            joins += j
            if k == 0:
                text = t
            elif ends_hyphen(text):
                text, rule = join_pair(text, t)
                joins.append({'rule': rule, 'across_page': True, 'page': page['page'], 'input_index': page['input_index']})
            else:
                text += ' ' + t
        r0 = c['parts'][0][1]
        out.append({'unit_id': 'u%04d' % i, 'type': r0['type'], 'text': text,
                    'source_object_ids': [r['object_id'] for _, r in c['parts']],
                    'pages': sorted({(p['input_index'], p['page']) for p, _ in c['parts']}),
                    'joins': joins, 'dehyphenated': bool(joins)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('inputs', nargs='+', type=Path, help='stage3 JSON files, in book order')
    ap.add_argument('-o', '--output', type=Path, default=Path('stage4_out'))
    a = ap.parse_args()
    pages = []
    for k, f in enumerate(a.inputs, 1):
        for pg in json.loads(f.read_text(encoding='utf-8'))['pages']:
            # page numbers and object ids restart in every input file: make them unique
            for r in pg['regions']:
                if r.get('object_id'):
                    r['object_id'] = 's%d-%s' % (k, r['object_id'])
            pg['input_index'] = k
            pages.append(pg)
    link_pages(pages)
    units = build_units(pages)
    a.output.mkdir(parents=True, exist_ok=True)
    doc = {'schema_version': '0.4.0', 'stage': 'stage4_units',
           'inputs': [str(f) for f in a.inputs], 'units': units, 'pages': pages}
    (a.output / 'units.json').write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
    for u in units:
        if len(u['source_object_ids']) > 1 or u['joins']:
            print(u['unit_id'], u['pages'], [j['rule'] for j in u['joins']], repr(u['text'][:70]))
    print(len(units), 'units')


if __name__ == '__main__':
    main()

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

import log
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


# ---------------------------------------------------------------- OCR region errors
def junk_line(line, width):
    """A stray mark read as a line (``)``, ``5``, ``Ef`` from a margin mark): narrower than 12 % of its region and at
    most 2 letters or digits.  Such lines are left out of the unit text (``unit_lines``); ``pageindex.replay`` uses the
    same rule, so word positions stay exact."""
    box = line.get('bbox')
    if not box or not width:
        return False
    return (box[2] - box[0]) < 0.12 * width and sum(c.isalnum() for c in line.get('text', '')) <= 2


def region_width(r):
    return (r['bbox'][2] - r['bbox'][0]) if r.get('bbox') else 0


def unit_lines(r):
    """The lines of a region that go into its unit text (stray marks left out)."""
    w = region_width(r)
    return [ln for ln in r.get('lines', []) if not junk_line(ln, w)]


def clean_text(r):
    return ' '.join(ln['text'] for ln in unit_lines(r)) if r.get('lines') else r.get('text', '')


def mark_noise(page):
    """Regions that are scan noise, not text: fewer than 3 letters once stray marks are removed (``|``, ``N``, a
    figure or page number), or a "paragraph" narrower than a quarter of the page's widest paragraph (a strip at the
    page edge).  They get ``translation_status: exclude`` (``exclude_reason: noise``): no unit, nothing to translate;
    the renderer keeps their pixels inside the text column and drops them outside."""
    paras = [r for r in page['regions'] if r.get('type') == 'paragraph' and r.get('translation_status') == 'translate']
    widest = max((region_width(r) for r in paras), default=0)
    for r in page['regions']:
        if r.get('translation_status') != 'translate':
            continue
        letters = sum(c.isalpha() for c in clean_text(r))
        narrow = r.get('type') == 'paragraph' and widest and region_width(r) < 0.25 * widest
        if letters < 3 or narrow:
            r['translation_status'] = 'exclude'
            r['exclude_reason'] = 'noise'
    return page


def _glyph(r):
    g = [ln.get('glyph_height') for ln in r.get('lines', []) if ln.get('glyph_height')]
    return sorted(g)[len(g) // 2] if g else None


def body_glyph(pages):
    """Median glyph height of the book's body paragraphs (None when unknown)."""
    g = [_glyph(r) for p in pages for r in p['regions'] if r.get('type') == 'paragraph' and _glyph(r)]
    return sorted(g)[len(g) // 2] if g else None


def body_paragraphs(page, gh=None):
    """The page's body paragraphs in reading order (column, then top).  A *caption* of 4 lines or more at body size
    (``gh``: the book's body glyph height) counts as body text: stage 2 sometimes takes the text under a plate for its
    caption, and the paragraph flowing into it must be linked (the renderer sets it as body text as well)."""
    def body(r):
        if r.get('translation_status') != 'translate':
            return False
        if r['type'] == 'paragraph':
            return True
        g = _glyph(r)
        return r['type'] == 'caption' and len(r.get('lines', [])) >= 4 and gh and g and g >= 0.9 * gh
    return sorted([r for r in page['regions'] if body(r)], key=lambda r: (r['bbox'][0] // 200, r['bbox'][1]))


def last_line_full(r, right=None):
    """The region's last text line reaches the right edge of the column (``right``; default the region's widest line),
    within 6 % of the width: in this book a paragraph ends with a short line, so a full last line means the paragraph
    goes on."""
    ls = unit_lines(r)
    if len(ls) < 1 or not ls[-1].get('bbox'):
        return False
    edge = right if right is not None else max(ln['bbox'][2] for ln in ls if ln.get('bbox'))
    return edge - ls[-1]['bbox'][2] <= 0.06 * max(region_width(r), 1)


def continues(prev, nxt, same_page=False, right=None):
    """``nxt`` continues the paragraph ``prev``: ``prev`` ends in a hyphen, or does not end a sentence and ``nxt``
    starts with a small letter.  On one page (``same_page``: a paragraph stage 2 split, e.g. at a margin mark) a full
    last line of ``prev`` is enough too: German nouns start with a capital, so the small-letter test alone misses
    ``... mit tiefen`` / ``Hohlkehlen ab``.  Across pages the stricter test stays: pages may be missing from the scans."""
    p = clean_text(prev).rstrip(); n = clean_text(nxt).lstrip()
    if not p or not n:
        return False
    if ends_hyphen(p):
        return True
    return (not ends_sentence(p)) and (n[:1].islower() or (same_page and last_line_full(prev, right)))


def link_pages(pages):
    """Mark noise regions, then set continues_from / continues_to (object ids): between the last body paragraph of a
    page and the first of the next, and between consecutive body paragraphs of one column on a page (a paragraph that
    stage 2 split, e.g. at a margin mark)."""
    prev = None
    for page in pages:
        mark_noise(page)
    gh = body_glyph(pages)
    for page in pages:
        paras = body_paragraphs(page, gh)
        if not paras:
            continue
        col_right = {}
        for r in paras:
            col_right[r['bbox'][0] // 200] = max(col_right.get(r['bbox'][0] // 200, 0), r['bbox'][2])
        for a, b in zip(paras, paras[1:]):
            same_col = a['bbox'][0] // 200 == b['bbox'][0] // 200
            if same_col and not b.get('continues_from') and \
                    continues(a, b, same_page=True, right=col_right[a['bbox'][0] // 200]):
                a['continues_to'] = b['object_id']
                b['continues_from'] = a['object_id']
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
            t, j = dehyphenate([l['text'] for l in unit_lines(r)])
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
    log.info('stage4','linking paragraphs across %d pages' % len(pages))
    link_pages(pages)
    for pg in pages:
        for r in pg['regions']:
            if r.get('continues_to'):
                log.info('stage4','  paragraph %s continues on the next page in %s' % (r['object_id'],r['continues_to']))
    units = build_units(pages)
    log.info('stage4','%d translation units, %d with dehyphenation joins' % (len(units),sum(u['dehyphenated'] for u in units)))
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

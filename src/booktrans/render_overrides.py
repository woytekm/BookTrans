#!/usr/bin/env python3
"""Human adjustments of the rebuilt pages (no GUI toolkit, testable alone).

The automatic layout (``layout.py``) should get most pages right; where it does not, a human can adjust a block or
a page.  The adjustments live apart from the pipeline output, in ``review/render_overrides.json`` of the project::

    {"schema": "1.0",
     "blocks": {"s1-p50-cap-2": {"base": "<sha1 of the block's OCR text>", "size_scale": 0.95, "anchor": "center"}},
     "pages":  {"0001.tif#1":   {"base": "<sha1 of the page's OCR text>", "size_factor": 0.97, "track_delta_em": -0.01}}}

Block adjustments (fixed frames: headings, captions, bylines, footnotes; ``keep_original`` for any text block):

* ``size_scale``   multiplies the fitted font size of the block (0.5 ... 1.5)
* ``tracking_em``  fixed letter-spacing in em instead of the fitted one (0 ... 0.5)
* ``word_space``   multiplies the natural word space (0.5 ... 3)
* ``anchor``       ``left`` / ``right`` / ``center`` instead of the detected one
* ``max_width``    the widest the text may be, as a factor of the frame width (0.5 ... 2), instead of 1.0 ... 1.3
* ``keep_original`` true: keep the scan's pixels of this block, do not set the translation

Page adjustments (the body text of a page): ``size_factor`` (0.8 ... 1.2) and ``track_delta_em`` (-0.05 ... 0.05)
replace the automatic per-page fit (see ``layout.PAGE_FIT``).

``base`` ties an adjustment to the OCR text it was made for.  Block ids come from the pipeline's regions and stay the
same on a re-run as long as the page is detected the same way; when the text under an id changes, the adjustment is
*stale* and not applied (``resolve`` lists it).  Pages are keyed by their TIFF and position in it (``source_tiff#n``),
or by the page number for a single TIFF without that information.
"""
import hashlib
import json
import os
from pathlib import Path

SCHEMA = '1.0'
BLOCK_KEYS = {'size_scale': (0.5, 1.5), 'tracking_em': (0.0, 0.5), 'word_space': (0.5, 3.0), 'max_width': (0.5, 2.0)}
ANCHORS = ('left', 'right', 'center')
PAGE_KEYS = {'size_factor': (0.8, 1.2), 'track_delta_em': (-0.05, 0.05)}


def empty():
    return {'schema': SCHEMA, 'blocks': {}, 'pages': {}}


def _sha(text):
    return hashlib.sha1(text.encode('utf-8')).hexdigest()


def block_hash(block):
    """Hash of what the OCR read in a block (its line texts)."""
    return _sha('\n'.join(l.get('text', '') for l in block.get('lines') or []) or block.get('text', ''))


def page_key(page):
    if page.get('source_tiff'):
        return '%s#%s' % (page['source_tiff'], page.get('page_in_spread', page.get('side', '')))
    return 'page %s' % page['page']


def page_hash(page):
    return _sha('\n'.join(block_hash(b) for b in page['blocks'] if b.get('kind') == 'text'))


def load(path):
    """The overrides file, or an empty set when there is none.  Raises ``ValueError`` for a file that is not one."""
    p = Path(path)
    if not p.exists():
        return empty()
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise ValueError('cannot read %s: %s' % (p, e))
    if not isinstance(data, dict) or not isinstance(data.get('blocks', {}), dict) or not isinstance(data.get('pages', {}), dict):
        raise ValueError('%s is not a render overrides file' % p)
    data.setdefault('blocks', {}); data.setdefault('pages', {})
    return data


def save(path, data):
    """Atomic write (a crash must not leave half a file: this is human work)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    os.replace(str(tmp), str(p))


def check_block(adj):
    """Validate a block adjustment (without ``base``); raises ValueError with a readable message."""
    for k, v in adj.items():
        if k in BLOCK_KEYS:
            lo, hi = BLOCK_KEYS[k]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= v <= hi:
                raise ValueError('%s must be a number from %g to %g' % (k, lo, hi))
        elif k == 'anchor':
            if v not in ANCHORS:
                raise ValueError('anchor must be one of %s' % ', '.join(ANCHORS))
        elif k == 'keep_original':
            if not isinstance(v, bool):
                raise ValueError('keep_original must be true or false')
        elif k != 'base':
            raise ValueError('unknown block adjustment "%s"' % k)
    return adj


def check_page(adj):
    for k, v in adj.items():
        if k in PAGE_KEYS:
            lo, hi = PAGE_KEYS[k]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= v <= hi:
                raise ValueError('%s must be a number from %g to %g' % (k, lo, hi))
        elif k != 'base':
            raise ValueError('unknown page adjustment "%s"' % k)
    return adj


def set_block(data, block, **adj):
    """Set (merge) adjustments of ``block``; a value None removes that key; no keys left removes the entry."""
    cur = {k: v for k, v in data['blocks'].get(block['block_id'], {}).items() if k != 'base'}
    cur.update(adj)
    cur = {k: v for k, v in cur.items() if v is not None}
    check_block(cur)
    if cur:
        data['blocks'][block['block_id']] = dict(cur, base=block_hash(block))
    else:
        data['blocks'].pop(block['block_id'], None)
    return data


def set_page(data, page, **adj):
    key = page_key(page)
    cur = {k: v for k, v in data['pages'].get(key, {}).items() if k != 'base'}
    cur.update(adj)
    cur = {k: v for k, v in cur.items() if v is not None}
    check_page(cur)
    if cur:
        data['pages'][key] = dict(cur, base=page_hash(page))
    else:
        data['pages'].pop(key, None)
    return data


def resolve(doc, data):
    """-> (blocks: {block_id: adj}, pages: {page index: adj}, stale: [description]) for ``layout.make_plan``."""
    blocks, pages, stale = {}, {}, []
    by_id = {b['block_id']: b for p in doc['pages'] for b in p['blocks']}
    for bid, adj in (data or {}).get('blocks', {}).items():
        b = by_id.get(bid)
        if b is None or adj.get('base') not in (None, block_hash(b)):
            stale.append('block %s: %s' % (bid, 'not in the document' if b is None else 'its OCR text changed'))
            continue
        blocks[bid] = {k: v for k, v in adj.items() if k != 'base'}
    keys = {page_key(p): (i, p) for i, p in enumerate(doc['pages'])}
    for key, adj in (data or {}).get('pages', {}).items():
        hit = keys.get(key)
        if hit is None or adj.get('base') not in (None, page_hash(hit[1])):
            stale.append('page %s: %s' % (key, 'not in the document' if hit is None else 'its OCR text changed'))
            continue
        pages[hit[0]] = {k: v for k, v in adj.items() if k != 'base'}
    return blocks, pages, stale

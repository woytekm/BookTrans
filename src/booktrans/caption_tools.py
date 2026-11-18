"""Caption help for the OCR correction window (no GUI toolkit, testable alone).

Captions are short, small and often letter-spaced: the OCR reads them worst of all, and a caption may come out in
pieces (several units).  The correction window's *Captions…* dialog shows each caption of the page enlarged, lets a
human read it again (``read_again``: the caption strip alone, enlarged, as one text line), join a piece with the next
caption piece (``join_texts``: the next unit is emptied - an empty unit is not translated and nothing is drawn for
it) and write the result into the page's review JSON (``apply_to_review``), which is then saved as usual.
"""
import json

import numpy as np
from PIL import Image

import captions

ZOOM_PAD = 14                     # page px around a caption in the enlarged view


def caption_units(review, page):
    """The caption units of a page review (``pageindex.page_review``) with the box of their blocks on this page:
    ``[{unit_id, text, text_raw, bbox, blocks}]`` top to bottom, left to right."""
    blocks = {b['block_id']: b for b in page.get('blocks', [])}
    out = []
    for u in review.get('units', []):
        if u.get('role') != 'caption':
            continue
        bs = [blocks[b]['bbox'] for b in u.get('blocks', []) if b in blocks]
        if not bs:
            continue
        box = [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]
        out.append({'unit_id': u['unit_id'], 'text': u['text'], 'text_raw': u.get('text_raw', u['text']),
                    'bbox': box, 'blocks': list(u.get('blocks', []))})
    return sorted(out, key=lambda c: (c['bbox'][1] // 6, c['bbox'][0]))


def row_box(cap, caps, page_w=None):
    """The area to show for a caption: its own box widened to the whole row of caption pieces it shares a line with
    (the letters between the pieces are often what the OCR missed), plus ``ZOOM_PAD``."""
    x1, y1, x2, y2 = cap['bbox']
    for o in caps:
        ox1, oy1, ox2, oy2 = o['bbox']
        if min(y2, oy2) - max(y1, oy1) > 0.4 * min(y2 - y1, oy2 - oy1) and abs((ox1 + ox2) / 2 - (x1 + x2) / 2) < 400:
            x1, x2 = min(x1, ox1), max(x2, ox2)
    box = [x1 - ZOOM_PAD, y1 - ZOOM_PAD, x2 + ZOOM_PAD, y2 + ZOOM_PAD]
    if page_w:
        box[0], box[2] = max(0, box[0]), min(page_w, box[2])
    box[1] = max(0, box[1])
    return box


def zoomed(img, box, width=760, max_zoom=5.0):
    """``img`` (PIL) cut to ``box`` and enlarged to about ``width`` px (at most ``max_zoom`` x): (image, zoom)."""
    crop = img.crop(tuple(int(round(v)) for v in box))
    z = float(min(max_zoom, max(1.0, width / max(1, crop.width))))
    return crop.resize((max(1, int(crop.width * z)), max(1, int(crop.height * z))), Image.LANCZOS), z


def read_again(img, bbox, lang, ocr=None):
    """The caption strip ``bbox`` of ``img`` (PIL or array) read alone: (text, confidence)."""
    arr = np.asarray(img.convert('RGB') if hasattr(img, 'convert') else img)
    text, conf, _ = captions.read_strip(arr, bbox, lang, ocr=ocr)
    return text, conf


def join_texts(texts, i):
    """Caption piece ``i`` joined with piece ``i + 1``: the new list of texts (the next one emptied)."""
    if not 0 <= i < len(texts) - 1:
        raise ValueError('there is no next caption to join with')
    out = list(texts)
    out[i] = ' '.join(t.strip() for t in (out[i], out[i + 1]) if t.strip())
    out[i + 1] = ''
    return out


def apply_to_review(review_text, changes):
    """The review JSON text with ``changes`` (unit id -> new text) put into the units' ``text``.  Raises ValueError
    when the JSON does not parse or a unit is not on the page."""
    try:
        data = json.loads(review_text)
    except ValueError as e:
        raise ValueError('the review JSON does not parse (fix it first): %s' % e)
    ids = {u.get('unit_id') for u in data.get('units', []) if isinstance(u, dict)}
    missing = sorted(set(changes) - ids)
    if missing:
        raise ValueError('not on this page: %s' % ', '.join(missing))
    for u in data['units']:
        if u.get('unit_id') in changes:
            u['text'] = changes[u['unit_id']]
    return json.dumps(data, ensure_ascii=False, indent=1)

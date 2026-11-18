"""Caption OCR helpers for stage 2 (no GUI, no project: pure functions on page arrays and line dicts).

Captions are the weakest text on a page: small, often letter-spaced capitals, set right against the picture.  Three
things go wrong in a whole-region OCR pass and are repaired here:

* the paper mask painted over a figure before OCR (``illustrations.mask_box``, a few pixels larger than the figure)
  covers the tops of a caption that touches the picture -> ``clip_mask`` stops it at the caption;
* Tesseract (``--psm 6`` over a large region) breaks one caption into several few-letter pieces ->
  ``join_caption_rows`` makes the pieces under one figure on one row one line again;
* the pieces come back as near-random letters -> ``reocr_line`` reads the caption's own strip again, enlarged and as
  a single text line (``--psm 7``); the better reading (higher confidence) is kept.

Coordinates are working-scale page pixels; lines are stage 2 line dicts (``bbox``, ``text``, ``confidence``,
``glyph_height``, ``words``, ``type``).
"""
import re

import numpy as np

LETTER = re.compile(r'[A-Za-zÄÖÜäöüß0-9]')


def _ov(a1, a2, b1, b2):
    return max(0, min(a2, b2) - max(a1, b1))


def clip_mask(box, text_boxes, max_overlap=14):
    """The figure mask ``box`` [x1,y1,x2,y2] shrunk so it does not cover text regions that only touch the figure (an
    overlap of at most ``max_overlap`` px at one edge: a caption set against the picture).  Text regions deep inside
    the figure (labels in a drawing) are left alone."""
    x1, y1, x2, y2 = box
    for tx1, ty1, tx2, ty2 in text_boxes:
        if _ov(x1, x2, tx1, tx2) <= 0 or _ov(y1, y2, ty1, ty2) <= 0:
            continue
        horiz = _ov(x1, x2, tx1, tx2) > 0.5 * (tx2 - tx1)
        vert = _ov(y1, y2, ty1, ty2) > 0.5 * (ty2 - ty1)
        if horiz and ty2 > y2 and y2 - ty1 <= max_overlap:          # caption below, its top under the mask
            y2 = min(y2, ty1 - 1)
        elif horiz and ty1 < y1 and ty2 - y1 <= max_overlap:        # caption above
            y1 = max(y1, ty2 + 1)
        elif vert and tx2 > x2 and x2 - tx1 <= max_overlap:         # caption right of the figure
            x2 = min(x2, tx1 - 1)
        elif vert and tx1 < x1 and tx2 - x1 <= max_overlap:         # caption left of the figure
            x1 = max(x1, tx2 + 1)
    return [x1, y1, x2, y2]


def figure_of(line, figures, below=45):
    """The figure (index into ``figures``: [x1,y1,x2,y2] boxes) a caption line sits directly under or over, or None."""
    x1, y1, x2, y2 = line['bbox']
    cx = (x1 + x2) / 2
    best = None
    for i, (fx1, fy1, fx2, fy2) in enumerate(figures):
        if not fx1 - 10 <= cx <= fx2 + 10:
            continue
        d = y1 - fy2 if y1 >= fy2 - 12 else (fy1 - y2 if y2 <= fy1 + 12 else None)
        if d is not None and d <= below and (best is None or d < best[0]):
            best = (d, i)
    return best[1] if best else None


def _same_row(a, b):
    return _ov(a['bbox'][1], a['bbox'][3], b['bbox'][1], b['bbox'][3]) >= 0.4 * min(
        a['bbox'][3] - a['bbox'][1], b['bbox'][3] - b['bbox'][1])


def gap_has_ink(page, a, b, frac=0.12):
    """True when the row between caption pieces ``a`` and ``b`` (left to right) holds ink: letters the OCR dropped.  A
    blank gap wider than two glyph heights separates two captions."""
    x1, x2 = a['bbox'][2], b['bbox'][0]
    gh = max(a.get('glyph_height') or 0, b.get('glyph_height') or 0, 6)
    if x2 - x1 <= 2 * gh:
        return True
    if page is None:
        return False
    y1 = min(a['bbox'][1], b['bbox'][1]); y2 = max(a['bbox'][3], b['bbox'][3])
    strip = np.asarray(page[max(0, int(y1)):int(y2) + 1, int(x1) + 2:int(x2) - 1], dtype=np.float32)
    if strip.size == 0:
        return False
    g = strip.mean(axis=2) if strip.ndim == 3 else strip
    paper = float(np.percentile(np.asarray(page, dtype=np.float32).mean(axis=2) if np.ndim(page) == 3 else page, 90))
    cols = (g < 0.75 * paper).any(axis=0)
    return float(cols.mean()) >= frac


def join_caption_rows(lines, figures, page=None):
    """Caption pieces on one row under (or over) one figure -> one line, left to right, when the gaps between them hold
    ink (``gap_has_ink``: letters the OCR dropped) or are narrow.  Side-by-side captions - under different figures, or
    under one wide picture with blank paper between them - stay apart.  Returns the new list (other lines unchanged,
    order by position)."""
    caps = [l for l in lines if l.get('type') == 'caption_line']
    rest = [l for l in lines if l.get('type') != 'caption_line']
    groups = []
    for ln in sorted(caps, key=lambda l: l['bbox'][0]):
        fi = figure_of(ln, figures)
        for g in groups:
            if fi is not None and g['fig'] == fi and _same_row(ln, g['lines'][-1]) and gap_has_ink(page, g['lines'][-1], ln):
                g['lines'].append(ln)
                break
        else:
            groups.append({'fig': fi, 'lines': [ln]})
    out = list(rest)
    for g in groups:
        ls = g['lines']
        if len(ls) == 1:
            out.append(ls[0])
            continue
        ls.sort(key=lambda l: l['bbox'][0])
        m = dict(ls[0])
        m['bbox'] = [min(l['bbox'][0] for l in ls), min(l['bbox'][1] for l in ls),
                     max(l['bbox'][2] for l in ls), max(l['bbox'][3] for l in ls)]
        m['text'] = ' '.join(l['text'] for l in ls)
        m['words'] = [w for l in ls for w in l.get('words', [])]
        m['word_count'] = sum(l.get('word_count', 0) for l in ls)
        m['confidence'] = round(float(np.mean([l['confidence'] for l in ls])), 1)
        hs = [l['glyph_height'] for l in ls if l.get('glyph_height')]
        m['glyph_height'] = round(float(np.median(hs)), 1) if hs else ls[0].get('glyph_height', 0)
        m['joined_from'] = [l['text'] for l in ls]
        m['classification_reason'] = 'caption pieces on one row under one figure, joined'
        out.append(m)
    return sorted(out, key=lambda z: (z['bbox'][1], z['bbox'][0]))


def strip_for(page, bbox, hires=None, f=1.0, target=32.0):
    """The caption strip of ``bbox`` as a grey image enlarged so the glyphs are ~``target`` px tall, with a replicated
    border: (image, scale, (ox, oy)) - page = ox + x / scale."""
    import cv2
    x1, y1, x2, y2 = bbox
    h = max(4.0, y2 - y1)
    px, py = 8, max(4, int(round(0.6 * h)))
    src, k = (hires, f) if hires is not None and f > 1 else (page, 1.0)
    H, W = src.shape[:2]
    X1, Y1 = max(0, int((x1 - px) * k)), max(0, int((y1 - py) * k))
    X2, Y2 = min(W, int((x2 + px) * k)), min(H, int((y2 + py) * k))
    crop = src[Y1:Y2, X1:X2]
    if crop.size == 0:
        return None, 1.0, (0, 0)
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY) if crop.ndim == 3 else crop
    s = float(min(4.0, max(2.0, target / h)))           # px per working px
    gray = cv2.resize(gray, None, fx=s / k, fy=s / k, interpolation=cv2.INTER_CUBIC if s > k else cv2.INTER_AREA)
    b = 12
    gray = cv2.copyMakeBorder(gray, b, b, b, b, cv2.BORDER_REPLICATE)
    return gray, s, (X1 / k - b / s, Y1 / k - b / s)


def reading_score(text, conf):
    """How believable an OCR reading of a caption is: its confidence, less for few letters and for junk characters."""
    t = text.strip()
    if not t:
        return -1.0
    letters = len(LETTER.findall(t))         # letters and figures (a caption number counts)
    junk = len(re.findall(r'[^\w\s,.;:()\-–\'"„“]', t))
    return float(conf) * min(1.0, letters / 6.0) - 8.0 * junk


def read_strip(page, bbox, lang, hires=None, f=1.0, ocr=None):
    """OCR of one caption strip (enlarged, ``--psm 7``): (text, mean confidence, words in page coordinates) or
    ('', 0, []).  ``ocr``: image -> tesseract data dict (for tests)."""
    img, s, (ox, oy) = strip_for(page, bbox, hires, f)
    if img is None:
        return '', 0.0, []
    if ocr is None:
        import pytesseract
        from pytesseract import Output
        ocr = lambda im: pytesseract.image_to_data(im, lang=lang, config='--psm 7', output_type=Output.DICT)
    d = ocr(img)
    words = []
    for i, t in enumerate(d['text']):
        t = (t or '').strip()
        try:
            c = float(d['conf'][i])
        except (TypeError, ValueError):
            c = -1
        if not t or c < 0:
            continue
        words.append({'text': t, 'conf': round(c, 1),
                      'bbox': [int(round(ox + d['left'][i] / s)), int(round(oy + d['top'][i] / s)),
                               int(round(ox + (d['left'][i] + d['width'][i]) / s)),
                               int(round(oy + (d['top'][i] + d['height'][i]) / s))]})
    if not words:
        return '', 0.0, []
    return ' '.join(w['text'] for w in words), round(float(np.mean([w['conf'] for w in words])), 1), words


def reocr_line(line, page, lang, hires=None, f=1.0, ocr=None):
    """Read a caption line's strip again (``read_strip``); returns the line updated with the new reading when it
    scores better (``reading_score``), else the line unchanged."""
    text, conf, words = read_strip(page, line['bbox'], lang, hires, f, ocr)
    if not words:
        return line
    if reading_score(text, conf) <= reading_score(line['text'], line.get('confidence', 0)) + 2:
        return line
    new = dict(line, text=text, confidence=conf, words=words, word_count=len(words),
               ocr_first={'text': line['text'], 'confidence': line.get('confidence')}, reocr='caption strip, psm 7')
    chars = re.sub(r'[^A-Za-zÄÖÜäöüß]', '', text)
    new['uppercase_ratio'] = round(sum(c.isupper() for c in chars) / max(1, len(chars)), 2)
    return new


def is_junk_caption(line):
    """A short caption piece that even the re-read could not make sense of (a speck of the picture read as letters)."""
    t = line['text'].strip()
    return len(t) < 8 and reading_score(t, line.get('confidence', 0)) < 12


def drop_junk_in_figures(lines, figures, conf=50):
    """Lines (not captions) lying inside a figure with low confidence: the picture read as letters."""
    out = []
    for ln in lines:
        x1, y1, x2, y2 = ln['bbox']
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        inside = any(fx1 + 4 <= cx <= fx2 - 4 and fy1 + 4 <= cy <= fy2 - 4 for fx1, fy1, fx2, fy2 in figures)
        if inside and ln.get('type') != 'caption_line' and ln.get('confidence', 100) < conf:
            continue
        out.append(ln)
    return out

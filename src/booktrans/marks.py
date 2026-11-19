#!/usr/bin/env python3
"""Small graphic page furniture outside the text blocks: page numbers, printer's
marks and horizontal rules (e.g. the line above a footnote).

They carry no translatable text, so they are kept as image crops and pasted back
at the same place.  (A translated edition may want new page numbers; the crop
tells the renderer where they sit and how large they are.)

Rules are found anywhere outside text/figure boxes; page numbers and marks only
in the header/footer zones, so stray ink between text lines is never mistaken for
them.  All coordinates are working-scale page pixels.
"""
import cv2
import numpy as np

from deskew import ink_mask

PAD = 3


def is_rule(mask):
    """A candidate rule's raw ink (``mask``: a strip around it, nonzero = ink) is a rule when the ink lies in a thin band
    (a hairline: 70 % of it in 4 rows) that runs nearly unbroken (85 % of the columns).  Letter-spaced capitals pass
    the morphological test - after closing they look like a long faint bar - but their ink spreads over the glyph
    height and leaves gaps between the letters."""
    m = np.asarray(mask) > 0
    if m.size == 0 or not m.any():
        return False
    rows = m.sum(axis=1).astype(float)
    k = min(4, len(rows))
    band = np.convolve(rows, np.ones(k), 'valid')
    j = int(np.argmax(band))
    if band[j] < 0.7 * rows.sum():
        return False
    return float(m[j:j + k].any(axis=0).mean()) >= 0.85


def find_marks(rgb, boxes, content_area, columns, pad_boxes=4):
    """Return ``[{'role': 'rule'|'page_mark', 'bbox': [x1,y1,x2,y2]}]``.  ``columns``: the page's text columns; a page
    without body text (a plate: pictures and captions only) has none, and its marks are looked for across the width of
    its content instead (before 2026-11-20 such pages lost their page number); rules (separators of text) are not looked
    for there - a picture's faint edge would pass."""
    if content_area is None:
        return []
    plate = not columns
    if plate:
        columns = [{'left': content_area[0], 'right': content_area[2]}]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    ink = ink_mask(gray)
    colL, colR = columns[0]['left'], columns[0]['right']
    zone = np.zeros_like(ink)
    zone[:, max(0, int(colL - 30)):min(w, int(colR + 30))] = 255
    zone[:40, :] = 0; zone[h - 40:, :] = 0
    ink = cv2.bitwise_and(ink, zone)
    for x1, y1, x2, y2 in boxes:
        ink[max(0, int(y1 - pad_boxes)):int(y2 + pad_boxes), max(0, int(x1 - pad_boxes)):int(x2 + pad_boxes)] = 0
    top, bottom = content_area[1], content_area[3]
    out = []

    # horizontal rules: long, thin, only slightly darker than the paper, so they get
    # their own low-contrast test (a printed hairline is ~1 px and often broken up).
    bg = cv2.GaussianBlur(gray, (0, 0), 25).astype(np.int16)
    faint = ((bg - gray.astype(np.int16)) > 14).astype(np.uint8) * 255
    faint = cv2.bitwise_and(faint, zone)
    for x1, y1, x2, y2 in boxes:
        faint[max(0, int(y1 - pad_boxes)):int(y2 + pad_boxes), max(0, int(x1 - pad_boxes)):int(x2 + pad_boxes)] = 0
    line = cv2.morphologyEx(faint, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1)))
    line = cv2.morphologyEx(line, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (41, 1)))
    line = cv2.morphologyEx(line, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 5)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(line, 8)
    rule_boxes = []
    for i in range(1, n):
        x, y, ww, hh, a = st[i]
        if not plate and ww >= 60 and hh <= 8 and ww >= 12 * hh and top - 60 <= y <= bottom + 60 \
                and is_rule(faint[max(0, y - 4):y + hh + 4, x:x + ww]):      # not the book's edge, not letters
            rule_boxes.append([int(x), int(y), int(x + ww), int(y + hh)])
            out.append({'role': 'rule', 'bbox': [int(x) - 1, int(y) - 2, int(x + ww) + 1, int(y + hh) + 2]})
    for x1, y1, x2, y2 in rule_boxes:
        ink[y1:y2 + 1, x1:x2 + 1] = 0

    # page numbers / marks: only above or below the content (close to it, or - page-number sized - up to a fifth of
    # the page height away)
    reach = max(130, int(0.2 * h))
    blobs = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(blobs, 8)
    for i in range(1, n):
        x, y, ww, hh, _ = st[i]
        if ww > 140 or hh > 45 or ww < 4 or hh < 4:
            continue
        cy = y + hh / 2.0
        near = top - 130 <= cy < top - 8 or bottom + 8 < cy <= bottom + 130
        # a plate page's content (pictures, captions) can end far above where the page number is printed: further
        # away only something the size of a page number counts, not a speck
        far = top - reach <= cy < top - 8 or bottom + 8 < cy <= bottom + reach
        if not (near or far):
            continue
        ys, xs = np.nonzero((lab[y:y + hh, x:x + ww] == i) & (ink[y:y + hh, x:x + ww] > 0))
        if len(xs) < 12:
            continue
        x1, y1, x2, y2 = int(x + xs.min()), int(y + ys.min()), int(x + xs.max()) + 1, int(y + ys.max()) + 1
        if y2 - y1 < 5:
            continue                          # a hairline or an edge of a picture, not a mark
        if not near and not (8 <= y2 - y1 <= 40 and 6 <= x2 - x1 <= 90 and len(xs) >= 25):
            continue
        out.append({'role': 'page_mark', 'bbox': [x1 - PAD, y1 - PAD, x2 + PAD, y2 + PAD]})
    out.sort(key=lambda m: (m['bbox'][1], m['bbox'][0]))
    return out

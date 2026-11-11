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


def find_marks(rgb, boxes, content_area, columns, pad_boxes=4):
    """Return ``[{'role': 'rule'|'page_mark', 'bbox': [x1,y1,x2,y2]}]``."""
    if not columns or content_area is None:
        return []
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
        if ww >= 60 and hh <= 8 and ww >= 12 * hh and top - 60 <= y <= bottom + 60:     # not the book's edge
            rule_boxes.append([int(x), int(y), int(x + ww), int(y + hh)])
            out.append({'role': 'rule', 'bbox': [int(x) - 1, int(y) - 2, int(x + ww) + 1, int(y + hh) + 2]})
    for x1, y1, x2, y2 in rule_boxes:
        ink[y1:y2 + 1, x1:x2 + 1] = 0

    # page numbers / marks: only above or below the content
    blobs = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(blobs, 8)
    for i in range(1, n):
        x, y, ww, hh, _ = st[i]
        if ww > 140 or hh > 45 or ww < 4 or hh < 4:
            continue
        cy = y + hh / 2.0
        if not (top - 130 <= cy < top - 8 or bottom + 8 < cy <= bottom + 130):
            continue
        ys, xs = np.nonzero((lab[y:y + hh, x:x + ww] == i) & (ink[y:y + hh, x:x + ww] > 0))
        if len(xs) < 12:
            continue
        x1, y1, x2, y2 = int(x + xs.min()), int(y + ys.min()), int(x + xs.max()) + 1, int(y + ys.max()) + 1
        out.append({'role': 'page_mark', 'bbox': [x1 - PAD, y1 - PAD, x2 + PAD, y2 + PAD]})
    out.sort(key=lambda m: (m['bbox'][1], m['bbox'][0]))
    return out

#!/usr/bin/env python3
"""Illustration detection, run *before* any text OCR.

Why this exists: the old pipeline found text blocks first, OCR'd every one of
them, and only afterwards tried to decide that some of that OCR was really a
drawing (a "large region with low OCR confidence" fallback).  On a drawing page
that sent junk such as ``ä faf`` and ``{ LEA l" A`` to translation, and it only
worked for one drawing group.  Here the page is split geometrically first:

1. **Tonal images** (photographs, halftones) - large dark/mid-tone blobs.
2. **Line art** (technical drawings) - everything that is *not* part of a text
   line.  A text line is a row of glyph-sized pieces; anything left over that
   forms a sizeable cluster is an illustration.

Illustration boxes are then masked out of the text analysis, so drawing
fragments never reach OCR, and captions (which are ordinary text lines) are
left to the text pipeline instead of being swallowed by a figure box.

Only geometry and ink statistics are used.  Thresholds are expressed in pixels
for the ~135 DPI sample scans (body glyph height ~8 px, line pitch ~24 px).
"""
import cv2
import numpy as np

from deskew import ink_mask

OUTER_MARGIN = 65      # px, ignore the physical book edge
INNER_MARGIN = 10      # px, gutter side (line art); photos use OUTER on both sides
MERGE_GAP = 40         # px, nearby line-art pieces belong to one figure
MIN_FIGURE_W = 48      # px
MIN_FIGURE_H = 48      # px
MIN_FIGURE_INK = 150   # px of non-text ink


CROP_MARGIN = 10       # px, a crop has no physical book edge to ignore


def edge_margins(side):
    """(left, top, right, bottom) ink-ignore margins, in working px.

    ``L`` / ``R``: a page of an open spread (physical edge outside, gutter inside);
    ``P``: a single whole page (edge on all sides); ``C``: a crop of a page.
    """
    if side == 'L':
        return OUTER_MARGIN, OUTER_MARGIN, INNER_MARGIN, OUTER_MARGIN
    if side == 'R':
        return INNER_MARGIN, OUTER_MARGIN, OUTER_MARGIN, OUTER_MARGIN
    if side == 'C':
        return (CROP_MARGIN,) * 4
    return (OUTER_MARGIN,) * 4


def page_margins(side):
    """(left, right) ignore margins for the line-art detector."""
    m = edge_margins(side)
    return m[0], m[2]


def _is_glyph(h, w, area):
    """Component sized like a printed letter (or a pair of touching letters)."""
    return 4 <= h <= 22 and 2 <= w <= 30 and area >= 6


def text_line_mask(ink):
    """Boolean mask of ink pixels that belong to text-line-shaped structures.

    A *text line* is a horizontally closed blob that is wide enough (>= 50 px),
    about one line high, dense, contains several glyph-sized pieces, and whose
    ink is mostly made of such pieces.  Dimension lines, hatching, frames and
    halftone texture fail at least one of these tests.
    """
    n0, lab0, st0, _ = cv2.connectedComponentsWithStats(ink, 8)
    glyph = np.zeros(n0, bool)
    for i in range(1, n0):
        x, y, ww, hh, a = st0[i]
        glyph[i] = _is_glyph(hh, ww, a)

    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (21, 1)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(closed, 8)
    inkpix = ink > 0
    glyph_pix = glyph[lab0] & inkpix
    glyph_ink = np.bincount(lab[glyph_pix], minlength=n)
    total_ink = np.bincount(lab[inkpix], minlength=n)

    # number of glyph-sized pieces per blob (one sample point per piece)
    cnt = np.zeros(n, int)
    for i in np.nonzero(glyph)[0]:
        x, y, ww, hh, a = st0[i]
        b = lab[y + hh // 2, x + ww // 2]
        if b == 0:
            sub = lab[y:y + hh, x:x + ww]
            v = sub[sub > 0]
            b = np.bincount(v).argmax() if v.size else 0
        if b > 0:
            cnt[b] += 1

    keep = np.zeros(n, bool)
    for i in range(1, n):
        x, y, ww, hh, a = st[i]
        if not (6 <= hh <= 40 and ww >= 50):
            continue
        if a / float(ww * hh) < 0.30:
            continue
        if cnt[i] < max(3, 0.04 * ww):
            continue
        if glyph_ink[i] < 0.40 * max(1, total_ink[i]):
            continue
        keep[i] = True
    line_blobs = keep[lab]
    return line_blobs & inkpix, line_blobs


def _merge_boxes(boxes, gap):
    """Merge [x1,y1,x2,y2,ink] boxes that are within ``gap`` px of each other."""
    boxes = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
                dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
                if dx <= gap and dy <= gap:
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]), a[4] + b[4]]
                    boxes.pop(j)
                    changed = True
                    break
            if changed:
                break
    return boxes


def detect_line_art(gray, side, exclude=()):
    """Find line drawings: clusters of ink that is not part of any text line.

    ``exclude`` lists boxes (already-found photographs) whose ink is ignored, so
    that a caption set right next to a photo is not fused with it.
    """
    h, w = gray.shape
    ink = ink_mask(gray)
    ml, mt, mr, mb = edge_margins(side)
    ink[:mt, :] = 0; ink[h - mb:, :] = 0; ink[:, :ml] = 0; ink[:, w - mr:] = 0
    for x1, y1, x2, y2 in exclude:
        ink[max(0, y1):y2, max(0, x1):x2] = 0

    text_ink, line_blobs = text_line_mask(ink)
    zone = cv2.dilate(line_blobs.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    nontext = (ink > 0) & ~zone
    closed = cv2.morphologyEx((nontext * 255).astype(np.uint8), cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))
    n, lab, _, _ = cv2.connectedComponentsWithStats(closed, 8)
    boxes = []
    for i in range(1, n):
        ys, xs = np.nonzero((lab == i) & nontext)
        if len(xs) >= 20:
            boxes.append([int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1, int(len(xs))])
    out = []
    for x1, y1, x2, y2, ink_px in _merge_boxes(boxes, MERGE_GAP):
        if x2 - x1 >= MIN_FIGURE_W and y2 - y1 >= MIN_FIGURE_H and ink_px >= MIN_FIGURE_INK:
            out.append({'bbox': [x1, y1, x2, y2], 'ink_pixels': ink_px})
    return out


def detect_tonal_images(rgb, side='L'):
    """Photographs / halftones: large, dense contours of dark and mid tones.

    Otsu is deliberately avoided (uneven lighting).  Typical printed text has an
    ink density well below the 0.35 required here.
    """
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    bw = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY_INV)[1]
    ml, mt, mr, mb = edge_margins(side)
    bw[:mt, :] = 0; bw[h - mb:, :] = 0; bw[:, :ml] = 0; bw[:, w - mr:] = 0
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        x, y, ww, hh = cv2.boundingRect(c)
        if ww < 120 or hh < 120 or ww * hh < w * h * .01:
            continue
        density = float(np.mean(gray[y:y + hh, x:x + ww] < 170))
        if density < .35:
            continue
        out.append({'bbox': [int(x), int(y), int(x + ww), int(y + hh)], 'ink_density': round(density, 3)})
    return out


# Illustration boxes are masked out of text detection with a small safety margin.
# Photographs leave faint halftone residue just outside the dark-tone contour
# (mostly below it); line art is tight. Captions sit >= ~9 px beside/below a
# figure, so the horizontal margin must stay small.
MASK_PAD = {'tonal': (4, 4), 'line_art': (4, 5)}


def mask_box(region):
    px,py=MASK_PAD.get(region.get('detector'),(4,5))
    x1,y1,x2,y2=region['bbox']
    return [x1-px,y1-py,x2+px,y2+py]



def detect_illustrations(rgb, side):
    """All illustrations on a page, as dicts with ``bbox`` and ``detector``.

    Photographs first; their boxes are masked before the line-art pass.
    """
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    tonal = detect_tonal_images(rgb, side)
    out = [dict(t, detector='tonal') for t in tonal]
    for fig in detect_line_art(gray, side, exclude=[t['bbox'] for t in tonal]):
        out.append(dict(fig, detector='line_art'))
    out.sort(key=lambda r: (r['bbox'][1], r['bbox'][0]))
    return out

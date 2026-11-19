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


EDGE_MAX_FRAC = 0.15    # the stacked page edges never take more than this of the page width
EDGE_START_FRAC = 0.05  # ... and start within this of the outer border
EDGE_LINE_FRAC = 0.08   # a page-edge line runs unbroken over at least this much of the page height (faint lines break)
EDGE_GAP = 40           # px between neighbouring edge lines (scaled with the page, see book_edge)


def book_edge(gray, side):
    """Width in px of the band of stacked page edges on the outer side(s) of a page: many long, nearly vertical lines
    at the border of the scan (a thick book seen from the side, the further from its middle the wider).  The fixed
    ``OUTER_MARGIN`` is too narrow for it near the front and the back of a book.  -> {'left': px, 'right': px}."""
    h, w = gray.shape
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 41, 15)
    tall = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, int(h * EDGE_LINE_FRAC)))))
    cols = (tall > 0).sum(axis=0) > 0
    gap = max(12, int(EDGE_GAP * w / 900.0))

    def band(order):
        end, last = 0, None
        for i, x in enumerate(order):
            if i > w * EDGE_MAX_FRAC:
                break
            if cols[x]:
                if last is None and i > w * EDGE_START_FRAC:
                    break                               # a line further in is content (a frame, a rule), not the edge
                last = i
                end = i + 1
            elif last is not None and i - last > gap:
                break
        return end
    out = {'left': 0, 'right': 0}
    if side in ('L', 'P'):
        out['left'] = band(range(w))
    if side in ('R', 'P'):
        out['right'] = band(range(w - 1, -1, -1))
    return out


def page_ignore_margins(gray, side):
    """``edge_margins`` widened over a detected band of stacked page edges (``book_edge``) on the outer side(s)."""
    ml, mt, mr, mb = edge_margins(side)
    if side == 'C':
        return ml, mt, mr, mb
    e = book_edge(gray, side)
    pad = 8
    if e['left']:
        ml = max(ml, e['left'] + pad)
    if e['right']:
        mr = max(mr, e['right'] + pad)
    return ml, mt, mr, mb


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


def detect_line_art(gray, side, exclude=(), margins=None):
    """Find line drawings: clusters of ink that is not part of any text line.

    ``exclude`` lists boxes (already-found photographs) whose ink is ignored, so
    that a caption set right next to a photo is not fused with it.  ``margins``: (left, top, right, bottom) px to
    ignore (default ``edge_margins(side)``).
    """
    h, w = gray.shape
    ink = ink_mask(gray)
    ml, mt, mr, mb = margins or edge_margins(side)
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


GROW_STEP = 2           # px per step when a photograph's box grows over its light parts
GROW_MAX = 200          # px at most per side (a light half of a photo: stone columns, sky)
GROW_NEAR = 12          # a pixel within this of the local paper grey looks like paper
GROW_PAPER_FRAC = 0.4   # a strip with more paper-like pixels than this is paper (or text on paper), not picture
GROW_LOOKAHEAD = 2      # strips: a pale stripe inside a picture this thin is crossed


def grow_halftone(gray, box, paper, limits):
    """A photograph's box grown over its light parts (a sunlit pavement, sky, pale stone): the dark-tone contour stops
    where the picture gets lighter than the threshold, and the rest of the picture was then taken for a line drawing
    that also swallowed the caption.  Each side moves out in ``GROW_STEP`` strips while the strip is picture: fewer
    than ``GROW_PAPER_FRAC`` of its pixels are as light as the paper beside that side (the paper's grey is the 75th
    percentile of a wide band beside the side - a curved page is darker towards the gutter or the edge, a gutter has a
    bright highlight; text on paper still has mostly paper pixels).  A thin light stripe inside the picture (a pale
    column) is crossed when picture follows within ``GROW_LOOKAHEAD`` strips.  ``paper`` is kept for callers;
    ``limits``: the ignore margins (left, top, right, bottom)."""
    h, w = gray.shape
    x1, y1, x2, y2 = box
    ml, mt, mr, mb = limits
    reach = GROW_MAX + 60              # far enough to reach paper beyond a light part of the picture

    def local(region):
        return float(np.percentile(region, 75)) if region.size else None

    def picture(strip, ref):
        return strip.size > 0 and ref is not None and float(np.mean(strip >= ref - GROW_NEAR)) < GROW_PAPER_FRAC

    def grow(pos, step, lo, hi, strip_at, ref):
        start = pos
        while abs(pos - start) < GROW_MAX:
            ok = False
            for k in range(1, GROW_LOOKAHEAD + 2):          # this strip, or one shortly after it
                a = pos + step * (k - 1) * GROW_STEP
                b = a + step * GROW_STEP
                if not (lo <= min(a, b) and max(a, b) <= hi):
                    break
                if picture(strip_at(min(a, b), max(a, b)), ref):
                    pos = b
                    ok = True
                    break
            if not ok:
                break
        return pos
    y2 = grow(y2, 1, 0, h - mb, lambda a, b: gray[a:b, x1:x2], local(gray[y2:min(h, y2 + reach), x1:x2]))
    y1 = grow(y1, -1, mt, h, lambda a, b: gray[a:b, x1:x2], local(gray[max(0, y1 - reach):y1, x1:x2]))
    x1 = grow(x1, -1, ml, w, lambda a, b: gray[y1:y2, a:b], local(gray[y1:y2, max(0, x1 - reach):x1]))
    x2 = grow(x2, 1, 0, w - mr, lambda a, b: gray[y1:y2, a:b], local(gray[y1:y2, x2:min(w, x2 + reach)]))
    return [x1, y1, x2, y2]


def detect_tonal_images(rgb, side='L', margins=None):
    """Photographs / halftones: large, dense contours of dark and mid tones, grown over their light parts
    (``grow_halftone``).

    Otsu is deliberately avoided (uneven lighting).  Typical printed text has an
    ink density well below the 0.35 required here.
    """
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    bw = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY_INV)[1]
    ml, mt, mr, mb = margins or edge_margins(side)
    paper = float(np.percentile(gray, 90))
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
        box = grow_halftone(gray, [int(x), int(y), int(x + ww), int(y + hh)], paper, (ml, mt, mr, mb))
        out.append({'bbox': [int(v) for v in box], 'ink_density': round(density, 3)})
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



def detect_illustrations(rgb, side, margins=None):
    """All illustrations on a page, as dicts with ``bbox`` and ``detector``.

    Photographs first; their boxes are masked before the line-art pass.  ``margins``: px to ignore at the page's
    sides (default: ``page_ignore_margins``, which covers the stacked page edges).
    """
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    margins = margins or page_ignore_margins(gray, side)
    tonal = detect_tonal_images(rgb, side, margins)
    out = [dict(t, detector='tonal') for t in tonal]
    for fig in detect_line_art(gray, side, exclude=[t['bbox'] for t in tonal], margins=margins):
        out.append(dict(fig, detector='line_art'))
    out.sort(key=lambda r: (r['bbox'][1], r['bbox'][0]))
    return out

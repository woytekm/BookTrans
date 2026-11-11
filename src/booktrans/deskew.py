#!/usr/bin/env python3
"""Per-page geometric correction (skew + shear) of scanned book pages.

Scanned book pages are rarely square to the sensor, and the distortion differs
from page to page.  Measured on the first sample spread: the right page's
text lines slope by 1.0 deg *and* its column edges tilt by the same amount
(a plain rotation), but on the left page the lines slope by 0.45 deg while the
column edges are already vertical (a shear, caused by the curved spread and the
scanner).  A pure rotation fixes the first case and damages the second, so two
numbers are estimated per page:

* the baseline slope  ``a = dy/dx`` of the text lines, and
* the column-edge slope ``s = dx/dy`` of the left/right edges of full-width
  (justified) lines.

and one small affine map is applied that makes baselines horizontal *and*
edges vertical.  If a page has too few full-width lines for ``s``, the
rotation assumption ``s = -a`` is used.

Why it matters: uncorrected distortion hides first-line indents and short last
lines (both are paragraph evidence), makes line boxes slope against the text,
and would carry over into the reconstructed Polish page.

The estimator only looks at *text-line-shaped* blobs, so photographs and line
drawings cannot dominate the result.
"""
import math

import cv2
import numpy as np

from imgutil import paper_color

MIN_LINES = 6          # below this the page is left untouched
MIN_FULL_LINES = 8     # below this the edge slope falls back to rotation (s = -a)
MAX_ABS_SLOPE = 0.07   # ~4 deg; anything steeper is not scanner skew


def _ink_mask(gray):
    """Illumination-normalised binary ink mask (uint8 0/255)."""
    bg = cv2.GaussianBlur(gray, (0, 0), 25)
    norm = cv2.divide(gray, bg, scale=230)
    return ((norm < 165) * 255).astype(np.uint8)


def line_blobs(gray, ignore=(40, 40, 40, 40)):
    """Find text-line-shaped blobs.

    Letters are joined horizontally so that one printed line becomes one long
    thin blob.  Only blobs that look like a line of text are kept (wide, but not
    taller than a text line, reasonably dense).  Returns ``(labels, blobs)``
    with ``blobs`` a list of ``(label, x, y, w, h)``.
    """
    h, w = gray.shape
    ink = _ink_mask(gray)
    l, t, r, b = ignore
    ink[:t, :] = 0; ink[h - b:, :] = 0; ink[:, :l] = 0; ink[:, w - r:] = 0
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (31, 1)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
    out = []
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if ww < 200 or hh > 45 or hh < 6:
            continue
        if area / float(ww * hh) < 0.35:     # frames/rules are not dense
            continue
        out.append((i, int(x), int(y), int(ww), int(hh)))
    return labels, out


def _theil_sen(xs, ys, min_dx=100.0):
    """Median of pairwise slopes dy/dx (robust line fit); None if too few pairs."""
    xs = np.asarray(xs, float); ys = np.asarray(ys, float)
    dx = xs[:, None] - xs[None, :]
    dy = ys[:, None] - ys[None, :]
    iu = np.triu_indices(len(xs), 1)
    dx = dx[iu]; dy = dy[iu]
    keep = np.abs(dx) >= min_dx
    if keep.sum() < 10:
        return None
    return float(np.median(dy[keep] / dx[keep]))


def estimate_geometry(gray):
    """Estimate ``(a, s, info)``: baseline slope dy/dx and column-edge slope dx/dy.

    ``a`` > 0 means the text lines descend to the right; ``s`` > 0 means the
    column edges lean to the right going down.  Both are 0.0 when the page has
    too few text lines (e.g. an all-photo page).
    """
    labels, blobs = line_blobs(gray)
    slopes, weights, rows = [], [], []
    for i, x, y, ww, hh in blobs:
        ys, xs = np.nonzero(labels[y:y + hh, x:x + ww] == i)
        if len(xs) < 200:
            continue
        xs = xs.astype(np.float64); ys = ys.astype(np.float64)
        xc = xs - xs.mean(); yc = ys - ys.mean()
        sl = float((xc * yc).sum() / max(1e-9, (xc * xc).sum()))
        if abs(sl) <= MAX_ABS_SLOPE:
            slopes.append(sl); weights.append(ww)
            rows.append((x, x + ww, y + hh / 2.0, ww))
    info = {'lines_used': len(slopes), 'full_lines_used': 0}
    if len(slopes) < MIN_LINES:
        return 0.0, 0.0, info
    order = np.argsort(slopes)
    sl_sorted = np.asarray(slopes)[order]; w_sorted = np.asarray(weights, float)[order]
    cum = np.cumsum(w_sorted)
    a = float(sl_sorted[np.searchsorted(cum, cum[-1] / 2.0)])

    # Column edges from full-width lines: both edges of a justified line sit on
    # the column boundary, so their slope against y is the edge slope.
    widths = np.array([r[3] for r in rows])
    full = [r for r in rows if r[3] >= 0.97 * np.percentile(widths, 90)]
    info['full_lines_used'] = len(full)
    s = None
    if len(full) >= MIN_FULL_LINES:
        yc = [r[2] for r in full]
        sl = _theil_sen(yc, [r[0] for r in full])     # x_left  as a function of y -> dx/dy
        sr = _theil_sen(yc, [r[1] for r in full])
        cand = [v for v in (sl, sr) if v is not None and abs(v) <= MAX_ABS_SLOPE]
        if cand:
            s = float(np.mean(cand))
    if s is None:
        s = -a
        info['edge_model'] = 'rotation_fallback'
    else:
        info['edge_model'] = 'measured'
    return a, s, info


def correction_matrix(a, s, shape):
    """2x3 matrix (source -> destination for ``cv2.warpAffine``) about the page centre."""
    h, w = shape[:2]
    A = np.array([[1.0, -s], [-a, 1.0]])
    c = np.array([w / 2.0, h / 2.0])
    t = c - A @ c
    return np.hstack([A, t.reshape(2, 1)])


def apply_correction(img, m, fill=None):
    if fill is None:
        fill = paper_color(img)
    h, w = img.shape[:2]
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=fill)


def deskew_page(rgb):
    """Return ``(corrected_rgb, info)`` for one page image."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    a, s, info = estimate_geometry(gray)
    m = correction_matrix(a, s, rgb.shape)
    if abs(a) < 0.0005 and abs(s) < 0.0005:
        out = rgb
    else:
        out = apply_correction(rgb, m)
    info.update({
        'angle_deg': round(math.degrees(math.atan(a)), 3),            # baseline slope as an angle
        'edge_tilt_deg': round(math.degrees(math.atan(s)), 3),        # column-edge tilt
        'baseline_slope': round(a, 5), 'edge_slope': round(s, 5),
        'method': 'affine_shear_rotation',
        'matrix_src_to_dst': [[round(float(v), 6) for v in row] for row in m],
    })
    return out, info


if __name__ == '__main__':
    import sys
    from PIL import Image
    im = np.asarray(Image.open(sys.argv[1]).convert('RGB'))
    _, inf = deskew_page(im)
    print(inf)

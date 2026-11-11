#!/usr/bin/env python3
"""Estimate how much larger a scan is than the resolution the detectors expect.

Every geometric threshold in the pipeline is a pixel value tuned on the sample
scans, where body text has a line pitch of ~23.3 px.  A bigger scan (600 DPI
original), a crop, or a different book changes that pitch, and the page height
says nothing about it (a crop is shorter than a page).  So the scale is taken from
the text itself: the line pitch is the lag of the strongest peak of the
autocorrelation of the horizontal ink profile, measured in several vertical
strips of a downsampled copy (median over strips, so a photo or a gutter shadow
in some strips does not matter).

``estimate_scale`` returns ``(f, info)``: ``f`` = full px per working px (>= 1).
"""
import cv2
import numpy as np

from deskew import ink_mask

REF_PITCH = 23.3        # px, body line pitch of the sample scans (the tuning size)
SNAP = 1.25             # below this ratio the scan is used as it is
MIN_PEAK = 0.10         # autocorrelation peak / lag-0 value needed to trust a strip


def estimate_line_pitch(rgb, target=1000):
    """Median line pitch in full-resolution px, or ``(None, info)``.

    On a copy downsampled to ~``target`` px wide, ink is closed horizontally so
    each printed line becomes one thin blob; the vertical distance between each
    blob and the next one below it (sharing a column) is a line pitch candidate.
    The answer is the median of the largest cluster of candidates, so photos,
    drawings, headings and paragraph gaps (rarer than plain line spacing) do not
    matter.  Unlike a projection profile this tolerates the ~1 deg skew of a scan.
    """
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    g = max(1.0, max(gray.shape) / float(target))
    small = cv2.resize(gray, (int(gray.shape[1] / g), int(gray.shape[0] / g)), interpolation=cv2.INTER_AREA) if g > 1 else gray
    ink = ink_mask(small)
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1)))
    n, _, st, _ = cv2.connectedComponentsWithStats(closed, 8)
    blobs = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if w >= 80 and 3 <= h <= 60 and w >= 4 * h and a / float(w * h) >= 0.2:
            blobs.append((x, x + w, y + h / 2.0))
    gaps = []
    for x1, x2, cy in blobs:
        best = None
        for u1, u2, uy in blobs:
            d = uy - cy
            if d <= 3 or d > 120 or (best is not None and d >= best):
                continue
            ov = min(x2, u2) - max(x1, u1)
            if ov >= 0.6 * min(x2 - x1, u2 - u1):
                best = d
        if best is not None:
            gaps.append(best)
    info = {'line_gaps': len(gaps)}
    if len(gaps) < 8:
        return None, info
    gaps = np.sort(np.asarray(gaps))
    # densest window of +-12 %
    best_n, best_med = 0, None
    for c in gaps:
        sel = gaps[(gaps >= c * 0.88) & (gaps <= c * 1.12)]
        if len(sel) > best_n:
            best_n, best_med = len(sel), float(np.median(sel))
    info['cluster_size'] = best_n
    if best_n < 6 or best_n < 0.3 * len(gaps):
        return None, info
    return best_med * g, info


def estimate_scale(rgb, override=None):
    """``(f, info)``; ``override`` forces a value (``f <= 1`` means "no downscaling")."""
    if override is not None:
        return max(1.0, float(override)), {'method': 'override'}
    pitch, info = estimate_line_pitch(rgb)
    info['method'] = 'line_pitch'
    if pitch is None:
        info['note'] = 'no reliable line pitch; scan used as is'
        return 1.0, info
    f = pitch / REF_PITCH
    info['line_pitch_px'] = round(pitch, 1)
    if f < SNAP:
        f = 1.0
    return float(f), info

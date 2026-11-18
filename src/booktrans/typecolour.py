#!/usr/bin/env python3
"""Typographic colour: how dark a paragraph is, on the scan and when set in a font (no page model, no GUI).

Used by ``fontcheck.py`` (which font matches the scan) and ``render.py`` (``--ink auto``: the ink colour at which the
rebuilt text is as dark as the scan).
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from typeset import pieces_of
import layout as L

ZOOM = 2
NOISE = 0.08            # darkness below this fraction of the paper is paper texture, not ink


def darkness(arr, paper=None):
    """Mean ink coverage of an RGB array: (paper - luminance) / paper, below NOISE counted as 0."""
    lum = arr.astype(np.float32).mean(axis=2)
    paper = paper if paper is not None else np.percentile(lum, 90)
    d = np.clip((paper - lum) / max(paper, 1.0), 0.0, 1.0)
    return float(np.mean(np.where(d < NOISE, 0.0, d)))


def scan_metrics(img, block):
    """(x-height, ascender height) of the scan's letters in px, median over the block's lines, measured from the ink:
    the x-band is where a line's row profile is at least half its maximum; the baseline is the band's lower edge, the
    ascender height the distance from the line's top to the baseline.  Independent of the OCR glyph height."""
    arr = np.asarray(img.convert('L')).astype(np.float32)
    xs, ascs = [], []
    for ln in block['lines']:
        x1, y1, x2, y2 = [int(round(v)) for v in ln['bbox']]
        if x2 - x1 < 20 or y2 - y1 < 4:
            continue
        t = max(0, y1 - 2)
        crop = arr[t:y2 + 2, max(0, x1):x2]
        paper = np.percentile(crop, 90)
        dens = np.clip((paper - crop) / max(paper, 1.0), 0, 1)
        prof = dens.mean(axis=1)
        if prof.max() <= 0:
            continue
        rows = np.where(prof >= 0.5 * prof.max())[0]
        ink_rows = np.where(dens.max(axis=1) >= 0.35)[0]            # any clear ink in the row (ascenders are sparse)
        ink_rows = ink_rows[ink_rows <= rows[-1]] if len(ink_rows) else rows
        base = t + rows[-1] + 1
        xs.append(rows[-1] - rows[0] + 1)
        ascs.append(base - (t + ink_rows[0]))
    if not xs:
        return None, None
    return float(np.median(xs)), float(np.median(ascs))


def set_block(block, fonts, size, track, ink, paper, S, origin):
    """The block's original text set into its original line boxes at scale S; returns the image of the block's box."""
    x0, y0, x1b, y1b = origin
    img = Image.new('RGB', (int(round((x1b - x0) * S)), int(round((y1b - y0) * S))), tuple(paper))
    draw = ImageDraw.Draw(img)
    font = fonts.get('regular', size * S)
    asc = fonts.ratios('regular')['asc'] * size
    tops = L.line_tops(block)
    justified = block['typography'].get('alignment') == 'justified'
    n = len(block['lines'])
    for k, ln in enumerate(block['lines']):
        text = ln['text'].strip()
        if not text:
            continue
        words = text.split()
        widths = [sum(p['w'] for p in pieces_of(w, font, track * S)) for w in words]
        space = font.getlength(' ') + track * S
        target = (ln['bbox'][2] - ln['bbox'][0]) * S
        gap = space
        if justified and k < n - 1 and len(words) > 1:
            gap = (target - sum(widths)) / (len(words) - 1)
        cx = (ln['bbox'][0] - x0) * S
        base = (tops[k] + asc - y0) * S
        for w, ww in zip(words, widths):
            if track > 0:
                for i, ch in enumerate(w):
                    draw.text((cx + font.getlength(w[:i]) + track * S * i, base), ch, font=font, fill=ink, anchor='ls')
            else:
                draw.text((cx, base), w, font=font, fill=ink, anchor='ls')
            cx += ww + gap
    return img


def sample_blocks(doc, pages, count):
    cand = []
    for pi in pages:
        for b in doc['pages'][pi]['blocks']:
            if b['kind'] == 'text' and b.get('role') == 'paragraph' and len(b.get('lines') or []) >= 3 \
                    and b['typography'].get('glyph_height_px'):
                cand.append((pi, b))
    if len(cand) <= count:
        return cand
    big = sorted(cand, key=lambda c: -len(c[1]['lines']))[:count * 3]
    big.sort(key=lambda c: (c[0], c[1]['bbox'][1]))                   # spread over the book
    step = len(big) / count
    return [big[int(i * step)] for i in range(count)]



def _sample_pixels(doc, blocks):
    base = Path(doc.get('_dir', '.'))
    out = []
    for pi, b in blocks:
        try:
            img = Image.open(base / doc['pages'][pi]['image']).convert('RGB')
        except OSError:
            continue
        pad = 6
        box = (max(0, b['bbox'][0] - pad), max(0, b['bbox'][1] - pad), b['bbox'][2] + pad, b['bbox'][3] + pad)
        out.append((pi, b, box, np.asarray(img.crop(box))))
    return out


def match_ink(doc, fonts, count=6):
    """The ink colour for the rebuilt text: the hue of the scan's ink (median colour of the pixels clearly darker than
    the paper), at the shade where the scan's body paragraphs, set in ``fonts`` with the renderer's fit, come out as
    dark as on the scan (mean ink coverage).  Between black and that median colour.  -> (rgb, info) or (None, info)
    when there is no body text to measure."""
    blocks = sample_blocks(doc, range(len(doc['pages'])), count)
    samples = _sample_pixels(doc, blocks)
    if not samples:
        return None, {'blocks': 0}
    darks, targets, sets = [], [], []
    for pi, b, box, arr in samples:
        lum = arr.astype(np.float32).mean(axis=2)
        paper_l = float(np.percentile(lum, 90))
        paper_rgb = np.percentile(arr.reshape(-1, 3), 90, axis=0).astype(int).tolist()
        dk = arr.reshape(-1, 3)[lum.reshape(-1) < 0.6 * paper_l]
        if len(dk):
            darks.append(dk)
        targets.append(darkness(arr, paper_l))
        r = fonts.ratios('regular')
        size = L.calibrated_size(b, fonts, 'regular', False, r, L.FIT_UP)[0]
        track = L.fit_track([b], fonts.get('regular', size * L.M), L.M, size)
        sets.append((b, size, track, paper_rgb, paper_l, box))
    if not darks:
        return None, {'blocks': len(samples)}
    hue = np.median(np.concatenate(darks), axis=0)
    target = float(np.mean(targets))
    # the set text's coverage is drawn once per block (alpha mask); the darkness for any ink shade follows from it:
    # luminance = paper (1 - a) + ink a  ->  darkness = a (paper - ink) / paper, below the noise floor counted as 0
    masks = []
    for b, size, track, paper_rgb, paper_l, box in sets:
        a = np.asarray(set_block(b, fonts, size, track, (255, 255, 255), (0, 0, 0), 1, box).convert('L'),
                       dtype=np.float32) / 255.0
        masks.append((a, paper_l))
    hue_l = float(hue.mean())

    def dark_at(k):
        out = []
        for a, paper_l in masks:
            d = a * max(paper_l - k * hue_l, 0.0) / max(paper_l, 1.0)
            out.append(float(np.mean(np.where(d < NOISE, 0.0, d))))
        return float(np.mean(out))

    info = {'blocks': len(samples), 'darkness_scan': round(target, 4), 'hue': [int(v) for v in hue]}
    d0, d1 = dark_at(0.0), dark_at(1.0)
    if d0 <= target:                                  # even black is lighter: the font is lighter than the print
        k = 0.0
        info['note'] = 'the font is lighter than the print even in black'
    elif d1 >= target:
        k = 1.0
    else:
        lo, hi = 0.0, 1.0
        for _ in range(20):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if dark_at(mid) > target else (lo, mid)
        k = (lo + hi) / 2
    info.update(k=round(k, 3), darkness_set=round(dark_at(k), 4))
    return tuple(int(round(v)) for v in hue * k), info

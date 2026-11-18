#!/usr/bin/env python3
"""Which font matches the scan?  A specimen sheet and a score per candidate font.

    python fontcheck.py output/book/document.json -o output/book/fontcheck \\
        --candidate "Crimson Pro Light=~/fonts/CrimsonPro[wght].ttf@300" \\
        --candidate "Spectral Light=~/fonts/Spectral-Light.ttf" [--pages 50-53] [--blocks 6]

For a few body paragraphs of the book (the ``--blocks`` largest, spread over the pages), every candidate sets the
paragraph's *original German text* into its *original line boxes*, with the same automatic size and letter-spacing
fit the renderer uses, in the ink colour of the scan.  So original and candidate can be compared line by line, and
measured:

* ``width``     the size that reproduces the original line widths over the size that reproduces the scan's
                ascender height (measured from the ink).  1.0 = the font has the original's proportions; above 1 it
                is narrower than the original (the renderer adds letter-spacing or a larger size), below 1 wider.
* ``x_height``  the font's x-height / ascender height over the scan's (both measured from the ink profile of every
                line).  Above 1 the font's small letters are larger than the original's.
* ``darkness``  mean ink coverage of the rebuilt paragraph over the scan's (stroke weight; noise floor removed).
                Above 1 the page looks darker than the original.
* ``tracking``  the letter-spacing the fit chose, in em.
* ``score``     3 |ln width| + 3 |ln x_height| + 2 |ln darkness|; lower is better.

Writes ``specimen_<block>.png`` (original on top, then each candidate, 2x) and ``fontcheck.json``; prints a table.
The bundled fonts (Crimson Text; Crimson Pro Light and Regular) are always included.  A candidate is ``NAME=REGULAR[,BOLD[,ITALIC]]``;
``path@300`` is a variable font at weight 300.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import log  # noqa: E402
from typeset import Fonts, pieces_of  # noqa: E402
import layout as L  # noqa: E402
from render import measure_ink, parse_pages, INK  # noqa: E402

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


def check(doc, candidates, pages=None, count=6, out_dir=None):
    """-> {'blocks': [...], 'fonts': {name: {width, x_height, darkness, tracking, score}}}; writes specimens."""
    base = Path(doc.get('_dir', '.'))
    pages = list(range(len(doc['pages']))) if pages is None else pages
    ink = measure_ink(doc) or INK
    blocks = sample_blocks(doc, pages, count)
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
    per = {name: {'width': [], 'x_height': [], 'darkness': [], 'tracking': []} for name, _ in candidates}
    rows = []
    for pi, b in blocks:
        page = doc['pages'][pi]
        img = Image.open(base / page['image']).convert('RGB')
        pad = 6
        box = (max(0, b['bbox'][0] - pad), max(0, b['bbox'][1] - pad), b['bbox'][2] + pad, b['bbox'][3] + pad)
        orig = img.crop(box)
        o_arr = np.asarray(orig)
        paper_rgb = np.percentile(o_arr.reshape(-1, 3), 90, axis=0).astype(int).tolist()
        paper_l = float(np.percentile(o_arr.astype(np.float32).mean(axis=2), 90))
        d_orig = darkness(o_arr, paper_l)
        xh, ah = scan_metrics(img, b)
        xr_orig = xh / ah if xh and ah else None
        tiles = [('original (page %s, %s)  x-height / ascender %.3f' % (page['page'], b['block_id'], xr_orig or 0),
                  orig.resize((orig.width * ZOOM, orig.height * ZOOM), Image.LANCZOS))]
        row = {'page': page['page'], 'block': b['block_id'], 'x_height_scan': xr_orig, 'darkness_scan': round(d_orig, 4),
               'fonts': {}}
        for name, fonts in candidates:
            r = fonts.ratios('regular')
            size, s_h, s_w = L.calibrated_size(b, fonts, 'regular', False, r, L.FIT_UP)
            s_a = ah / r['asc'] if ah else s_h
            track = L.fit_track([b], fonts.get('regular', size * L.M), L.M, size)
            one = set_block(b, fonts, size, track, tuple(ink), paper_rgb, 1, box)
            d = darkness(np.asarray(one), paper_l)
            m = {'size_px': round(size, 2), 'width': round(s_w / s_a, 3), 'tracking': round(track / size, 3),
                 'darkness': round(d / d_orig, 3) if d_orig > 0 else None,
                 'x_height': round(r['x'] / r['asc'] / xr_orig, 3) if xr_orig else None}
            row['fonts'][name] = m
            for k in ('width', 'x_height', 'darkness', 'tracking'):
                if m[k] is not None:
                    per[name][k].append(m[k])
            tiles.append(('%s  size %.1f  width %.2f  x-height %s  darkness %s  tracking %.3f em' % (
                name, size, m['width'], m['x_height'], m['darkness'], m['tracking']),
                set_block(b, fonts, size, track, tuple(ink), paper_rgb, ZOOM, box)))
        rows.append(row)
        if out_dir:
            lab = candidates[0][1].get('regular', 22)
            W = max(t.width for _, t in tiles); H = sum(t.height + 30 for _, t in tiles)
            sheet = Image.new('RGB', (W, H), (255, 255, 255)); dr = ImageDraw.Draw(sheet); y = 0
            for title, t in tiles:
                dr.text((6, y + 4), title, font=lab, fill=(170, 0, 0)); sheet.paste(t, (0, y + 30)); y += t.height + 30
            sheet.save(Path(out_dir) / ('specimen_%s.png' % b['block_id']))
    summary = {}
    for name, v in per.items():
        med = {k: (float(np.median(x)) if x else None) for k, x in v.items()}
        score = sum(w * abs(math.log(med[k])) for k, w in (('width', 3), ('x_height', 3), ('darkness', 2))
                    if med[k] and med[k] > 0)
        summary[name] = dict({k: (round(x, 3) if x is not None else None) for k, x in med.items()}, score=round(score, 3))
    result = {'ink': list(ink), 'blocks': rows, 'fonts': summary,
              'ranking': sorted(summary, key=lambda n: summary[n]['score'])}
    if out_dir:
        (Path(out_dir) / 'fontcheck.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    return result


def parse_candidate(spec):
    name, _, files = spec.partition('=')
    if not files:
        raise SystemExit('a candidate is NAME=REGULAR[,BOLD[,ITALIC]]: %s' % spec)
    parts = [str(Path(p).expanduser()) if '@' not in p else str(Path(p.rsplit('@', 1)[0]).expanduser()) + '@' + p.rsplit('@', 1)[1]
             for p in files.split(',')]
    return name.strip(), Fonts(*(parts + [None, None])[:3])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('document', type=Path)
    ap.add_argument('-o', '--output', type=Path, help='default: <document dir>/fontcheck')
    ap.add_argument('--candidate', action='append', default=[], help='NAME=REGULAR[,BOLD[,ITALIC]] (repeat)')
    ap.add_argument('--pages', help='take paragraphs from these pages (printed numbers, e.g. 50-53)')
    ap.add_argument('--blocks', type=int, default=6, help='how many paragraphs (default 6)')
    a = ap.parse_args()
    doc = json.loads(a.document.read_text(encoding='utf-8'))
    doc['_dir'] = str(a.document.parent)
    cands = [('Crimson Text (bundled)', Fonts())]
    for name, fn in (('Crimson Pro Light (bundled)', 'CrimsonPro-Light.ttf'), ('Crimson Pro (bundled)', 'CrimsonPro-Regular.ttf')):
        p = HERE.parent.parent / 'fonts' / fn
        if p.exists():
            cands.append((name, Fonts(p)))
    cands += [parse_candidate(c) for c in a.candidate]
    out = a.output or a.document.parent / 'fontcheck'
    res = check(doc, cands, parse_pages(a.pages, doc), a.blocks, out)
    log.info('fontcheck', '%d paragraphs, ink %s' % (len(res['blocks']), ','.join(map(str, res['ink']))))
    log.info('fontcheck', '%-34s %7s %8s %8s %8s %7s' % ('font', 'width', 'x-height', 'darkness', 'tracking', 'score'))
    for n in res['ranking']:
        f = res['fonts'][n]
        fmt = lambda v: '%.3f' % v if v is not None else '-'
        log.info('fontcheck', '%-34s %7s %8s %8s %8s %7s' % (n[:34], fmt(f['width']), fmt(f['x_height']), fmt(f['darkness']),
                                                             fmt(f['tracking']), fmt(f['score'])))
    log.info('fontcheck', 'specimens and fontcheck.json in %s' % out)


if __name__ == '__main__':
    main()

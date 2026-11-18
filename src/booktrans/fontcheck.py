#!/usr/bin/env python3
"""Which font matches the scan?  A specimen sheet and a score per candidate font.

    python fontcheck.py output/book/document.json -o output/book/fontcheck \\
        --candidate "Crimson Pro Light=~/fonts/CrimsonPro[wght].ttf@300" \\
        --candidate "Spectral Light=~/fonts/Spectral-Light.ttf" [--pages 50-53] [--blocks 6]

For a few body paragraphs of the book (the ``--blocks`` largest, spread over the pages), every candidate sets the
paragraph's *original German text* into its *original line boxes*, with the same automatic size and letter-spacing
fit the renderer uses, in black.  So original and candidate can be compared line by line, and
measured:

* ``width``     the size that reproduces the original line widths over the size that reproduces the scan's
                ascender height (measured from the ink).  1.0 = the font has the original's proportions; above 1 it
                is narrower than the original (the renderer adds letter-spacing or a larger size), below 1 wider.
* ``x_height``  the font's x-height / ascender height over the scan's (both measured from the ink profile of every
                line).  Above 1 the font's small letters are larger than the original's.
* ``darkness``  mean ink coverage of the paragraph set in *black* over the scan's (stroke weight; noise floor
                removed).  Must be at least 1: ``render.py --ink auto`` then lightens the ink until the page is as
                dark as the scan; below 1 the font is too thin even in black; far above 1 its strokes are heavier
                than the original's and the lightened ink looks grey.
* ``tracking``  the letter-spacing the fit chose, in em.
* ``score``     3 |ln width| + 3 |ln x_height| + (2 below 1, 0.7 above) |ln darkness|; lower is better.

Writes ``specimen_<block>.png`` (original on top, then each candidate, 2x) and ``fontcheck.json``; prints a table.
The default font (``config/fonts.json``) and the bundled fonts (Crimson Text; Crimson Pro Light and Regular) are always
included.  A candidate is ``NAME=REGULAR[,BOLD[,ITALIC]]``;
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
from typeset import Fonts  # noqa: E402
import layout as L  # noqa: E402
from render import parse_pages  # noqa: E402
from typecolour import ZOOM, darkness, scan_metrics, set_block, sample_blocks  # noqa: E402,F401

def check(doc, candidates, pages=None, count=6, out_dir=None):
    """-> {'blocks': [...], 'fonts': {name: {width, x_height, darkness, tracking, score}}}; writes specimens."""
    base = Path(doc.get('_dir', '.'))
    pages = list(range(len(doc['pages']))) if pages is None else pages
    ink = (0, 0, 0)            # every candidate in black: how dark it can get (render --ink auto lightens the ink)
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
        score = sum(w * abs(math.log(med[k])) for k, w in (('width', 3), ('x_height', 3)) if med[k] and med[k] > 0)
        if med['darkness'] and med['darkness'] > 0:      # too light cannot be fixed by the ink, too heavy looks grey
            score += (2.0 if med['darkness'] < 1 else 0.7) * abs(math.log(med['darkness']))
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
    default = Fonts()
    cands = [('%s (default)' % default.name, default)]
    for name, fn in (('Crimson Text (bundled)', 'CrimsonText-Regular.ttf'), ('Crimson Pro Light (bundled)', 'CrimsonPro-Light.ttf'),
                     ('Crimson Pro (bundled)', 'CrimsonPro-Regular.ttf')):
        p = HERE.parent.parent / 'fonts' / fn
        if p.exists() and str(p) != default.paths['regular']:
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

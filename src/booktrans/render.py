#!/usr/bin/env python3
"""Page assembly: rebuild each page from ``document.json`` with translated text.

    python render.py output/0001/document.json translations.json -o output/0001/render

* A clean canvas in the page's paper colour is created (``--background scan``
  starts from the scanned page instead and only wipes the text blocks).
* Figures are pasted from ``figures/`` at their original place.
* Every translated text unit is typeset into the frames (original text blocks):
  optimum-fit line breaking with justification, Polish hyphenation (pyphen) when
  installed, original line pitch, original anchoring (left / right / centre), and
  no line ending in a one-letter word (``w``, ``z``, ``i`` ...).
  A unit that continues on another page flows from frame to frame.
* The font size of a block is chosen so that its *original* text would set with
  the same line width in the new font (same typographic colour), within +-15 %
  of the size implied by the original glyph height.  If the translation needs
  more lines than the original had, the size is reduced up to 10 %, after which
  the text overflows below the block and the report says so.
* A unit without translation keeps the original pixels of its blocks.

Writes ``page_NN_X.png`` (full resolution when the scan was high resolution),
``compare.png`` (original | reconstruction) and ``report.json``.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import log  # noqa: E402

try:
    import pyphen
    _HYPH = pyphen.Pyphen(lang='pl_PL')
except Exception:       # pyphen is optional: without it words are not hyphenated
    _HYPH = None

DEFAULT_FONT_DIRS = [HERE.parent.parent / 'fonts']
SINGLE_LETTER = {'a', 'i', 'o', 'u', 'w', 'z'}      # Polish: do not end a line with these
INK = (34, 30, 26)
RMIN, RMAX = 0.75, 2.0           # allowed word-space stretch (1 = natural)
FIT_UP = 1.04                    # with fitted tracking the size may exceed the glyph-height size by this much
FIT_MAX_EM = 0.12                # fitted letter-spacing is capped at this fraction of the font size


# ---------------------------------------------------------------- fonts
class Fonts:
    def __init__(self, regular=None, bold=None, italic=None):
        def find(name, given):
            if given:
                return str(given)
            for d in DEFAULT_FONT_DIRS:
                p = d / name
                if p.exists():
                    return str(p)
            raise SystemExit('font %s not found; pass --font (a serif TTF with Polish letters)' % name)
        self.paths = {'regular': find('CrimsonText-Regular.ttf', regular)}
        self.paths['bold'] = find('CrimsonText-SemiBold.ttf', bold) if (bold or any((d / 'CrimsonText-SemiBold.ttf').exists() for d in DEFAULT_FONT_DIRS)) else self.paths['regular']
        self.paths['italic'] = find('CrimsonText-Italic.ttf', italic) if (italic or any((d / 'CrimsonText-Italic.ttf').exists() for d in DEFAULT_FONT_DIRS)) else self.paths['regular']
        self._cache = {}

    def get(self, style, size):
        key = (style, round(size, 2))
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(self.paths[style], size)
        return self._cache[key]

    def ratios(self, style='regular'):
        f = self.get(style, 200)
        cap = -f.getbbox('H', anchor='ls')[1] / 200.0
        asc = -f.getbbox('dbl', anchor='ls')[1] / 200.0
        box = (f.getbbox('Hg', anchor='ls')[3] - f.getbbox('Hg', anchor='ls')[1]) / 200.0
        return {'cap': cap, 'asc': asc, 'box': box}


# ---------------------------------------------------------------- typesetting
def pieces_of(text, font, track=0.0):
    """Split text into pieces with break kinds: space | glue | hard | soft | None (last)."""
    words = text.split()
    merged = []                       # (word, glue_to_next)
    for i, w in enumerate(words):
        glue = w.lower().strip('.,;:') in SINGLE_LETTER and i + 1 < len(words) and len(w.strip('.,;:')) == 1
        merged.append((w, glue))
    out = []
    for w, glue in merged:
        subs = re.split(r'(?<=[-–—])', w)          # hard break after hyphens / dashes
        for si, sub in enumerate(subs):
            parts = [sub]
            core = re.match(r'^(\W*)([^\W\d_]{6,})(\W*)$', sub)
            if _HYPH is not None and core:
                pre, word, post = core.groups()
                cuts = _HYPH.positions(word)
                if cuts:
                    bounds = [0] + list(cuts) + [len(word)]
                    parts = [word[a:b] for a, b in zip(bounds, bounds[1:])]
                    parts[0] = pre + parts[0]
                    parts[-1] = parts[-1] + post
            whole = ''.join(parts)
            cum = 0.0; done = ''
            for pi, part in enumerate(parts):
                last_part = pi == len(parts) - 1
                if not last_part:
                    brk = 'soft'
                elif si < len(subs) - 1:
                    brk = 'hard'
                else:
                    brk = 'glue' if glue else 'space'
                done += part
                wpart = font.getlength(done) - cum if len(parts) > 1 else font.getlength(part)
                cum += wpart
                out.append({'t': part, 'w': wpart + track * len(part), 'brk': brk, 'track': track})
    if out:
        out[-1]['brk'] = None
    return out


def break_lines(pieces, widths, space, hyph_w, justify, rmin=RMIN, rmax=RMAX):
    """Optimum-fit line breaking. ``widths(line_no)`` gives each line's width.

    Returns a list of (first_piece, last_piece) or None if impossible.
    """
    n = len(pieces)
    if n == 0:
        return []
    INF = float('inf')
    pw = np.concatenate([[0.0], np.cumsum([p['w'] for p in pieces])])
    gapflag = np.concatenate([[0], np.cumsum([1 if p['brk'] in ('space', 'glue') else 0 for p in pieces])])
    # best[(a, line_no)] memo over start piece and line index (line index only matters through widths)
    memo = {}

    def solve(a, ln):
        key = (a, ln if ln < 4 else 4 + (ln % 1))
        if a >= n:
            return 0.0, None
        W = widths(ln)
        k = (a, ln)
        if k in memo:
            return memo[k]
        best = (INF, None)
        for b in range(a, n):
            last = b == n - 1
            brk = pieces[b]['brk']
            if not last and brk not in ('space', 'hard', 'soft'):
                continue
            w_pieces = pw[b + 1] - pw[a]
            gaps = int(gapflag[b] - gapflag[a])      # gaps strictly inside the line
            hw = hyph_w if (not last and brk == 'soft') else 0.0
            if w_pieces + hw + gaps * space * rmin > W + 0.01:
                break
            if last:
                natural = w_pieces + gaps * space
                if natural > W + 0.01:
                    continue
                cost = (150.0 if natural > 0.92 * W else 0.0) if justify else 100.0 * ((W - natural) / W) ** 2 * 0.2
            else:
                if justify:
                    if gaps == 0:
                        slack = W - w_pieces - hw
                        if slack > 0.1 * W:
                            continue
                        cost = 60.0 * (slack / W) ** 2 * 100
                    else:
                        r = (W - w_pieces - hw) / (gaps * space)
                        if r < rmin or r > rmax:
                            continue
                        cost = 120.0 * (r - 1) ** 2 if r >= 1 else 600.0 * (1 - r) ** 2
                else:
                    natural = w_pieces + gaps * space + hw
                    cost = 100.0 * ((W - natural) / W) ** 2
                if brk == 'soft':
                    cost += 35.0
            cost += 8.0
            rest, _ = solve(b + 1, ln + 1)
            if cost + rest < best[0]:
                best = (cost + rest, b)
        memo[k] = best
        return best

    sys.setrecursionlimit(max(10000, n * 4))
    total, _ = solve(0, 0)
    if total == INF:
        return None
    lines, a, ln = [], 0, 0
    while a < n:
        _, b = solve(a, ln)
        lines.append((a, b)); a = b + 1; ln += 1
    return lines


def typeset(text, font, W, indent, justify, space_factor=1.0, track=0.0):
    """Lines for ``text``; each line is a dict with pieces, gap, hyphen flag and justification."""
    pieces = pieces_of(text, font, track)
    space = font.getlength(' ') * space_factor + track
    hyph_w = font.getlength('-')
    widths = lambda ln: W - (indent if ln == 0 else 0.0)
    lines = break_lines(pieces, widths, space, hyph_w, justify)
    relaxed = False
    if lines is None:
        lines = break_lines(pieces, widths, space, hyph_w, justify, rmin=0.5, rmax=4.0)
        relaxed = True
    if lines is None:                       # last resort: greedy, ragged
        lines = []; a = 0
        while a < len(pieces):
            b = a
            while b + 1 < len(pieces) and sum(p['w'] for p in pieces[a:b + 2]) + space * (b + 1 - a) <= widths(len(lines)):
                b += 1
            lines.append((a, b)); a = b + 1
        justify = False; relaxed = True
    out = []
    for ln, (a, b) in enumerate(lines):
        seg = pieces[a:b + 1]
        last = b == len(pieces) - 1
        gaps = sum(1 for p in seg[:-1] if p['brk'] in ('space', 'glue'))
        hyph = (not last) and seg[-1]['brk'] == 'soft'
        wpieces = sum(p['w'] for p in seg) + (hyph_w if hyph else 0.0)
        width = widths(ln)
        gap = space
        if justify and not last and gaps:
            gap = (width - wpieces) / gaps
        out.append({'pieces': seg, 'gap': gap, 'hyphen': hyph, 'width': wpieces + gaps * gap,
                    'indent': indent if ln == 0 else 0.0, 'last': last})
    return out, relaxed


def draw_line(draw, line, font, x, baseline, ink, hyph_char='-'):
    cx = x + line['indent']
    for i, p in enumerate(line['pieces']):
        tr = p.get('track', 0.0)
        if tr > 0:
            for k, ch in enumerate(p['t']):
                draw.text((cx + font.getlength(p['t'][:k]) + tr * k, baseline), ch, font=font, fill=ink, anchor='ls')
        else:
            draw.text((cx, baseline), p['t'], font=font, fill=ink, anchor='ls')
        cx += p['w']
        if i < len(line['pieces']) - 1 and p['brk'] in ('space', 'glue'):
            cx += line['gap']
    if line['hyphen']:
        draw.text((cx, baseline), hyph_char, font=font, fill=ink, anchor='ls')


# ---------------------------------------------------------------- block helpers
def block_style(block):
    t = block['typography']
    role = block['role']
    upper = t.get('uppercase_ratio', 0) >= 0.6
    style = 'bold' if role in ('heading',) and not upper else 'regular'
    return style, upper


def anchor_of(block, col):
    """left / right / center, from where the original block sits in its column."""
    x1, _, x2, _ = block['bbox']
    if block['typography'].get('alignment') == 'justified' or not col:
        return 'left'
    L, R = col['left'], col['right']
    if abs(x2 - R) <= 6 and x1 - L > 20:
        return 'right'
    if abs((x1 + x2) / 2 - (L + R) / 2) <= 8 and x1 - L > 20:
        return 'center'
    return 'left'


def calibrated_size(block, fonts, style, upper, ratios, max_up=1.15):
    """Font size (work px) that sets the block's original text with its original line widths."""
    t = block['typography']
    gh = t['glyph_height_px'] or 12.0
    s_h = gh / (ratios['cap'] if upper else ratios['box'])
    lines = block['lines']
    justified = t.get('alignment') == 'justified'
    num = den = 0.0
    f = fonts.get(style, 100)
    for i, ln in enumerate(lines):
        txt = ln['text'].strip()
        if not txt:
            continue
        natural = f.getlength(txt) / 100.0                     # em widths
        width = ln['bbox'][2] - ln['bbox'][0]
        full = justified and i < len(lines) - 1
        num += width * (0.95 if full else 1.0)
        den += natural
    s_w = num / den if den else s_h
    return float(min(max(s_w, 0.85 * s_h), max_up * s_h)), s_h, s_w


def fit_track(blocks, font, scale, size, max_em=FIT_MAX_EM):
    """Letter-spacing (work px per character) that makes the original lines of ``blocks`` set in
    ``font`` (loaded at ``scale`` x ``size``) as wide as they were on the scan: the median over lines
    of (original width - natural width) / (characters - 1), clamped to [0, max_em * size]."""
    gaps = []
    for b in blocks:
        lines = b['lines']
        justified = b['typography'].get('alignment') == 'justified'
        for i, ln in enumerate(lines):
            t = ln['text'].strip(); n = len(t)
            if n <= 3 or (justified and i == len(lines) - 1):
                continue
            full = justified and i < len(lines) - 1
            target = (ln['bbox'][2] - ln['bbox'][0]) * (0.95 if full else 1.0)
            gaps.append((target - font.getlength(t) / scale) / (n - 1))
    if not gaps:
        return 0.0
    return float(min(max(np.median(gaps), 0.0), max_em * size))


def paper_fill(img_arr, bbox, fallback):
    return tuple(fallback)


# ---------------------------------------------------------------- main
def wipe(arr, box, S):
    """Image of ``box`` with the text removed: the local paper tone (max filter + blur of the
    region and its surround) so shading and the gutter gradient continue under the new text."""
    import cv2
    h, w = arr.shape[:2]
    x1, y1, x2, y2 = max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])
    m = int(round(12 * S))
    X1, Y1, X2, Y2 = max(0, x1 - m), max(0, y1 - m), min(w, x2 + m), min(h, y2 + m)
    ctx = arr[Y1:Y2, X1:X2]
    k = int(round(9 * S)) | 1
    bg = cv2.dilate(ctx, np.ones((k, k), np.uint8))
    bg = cv2.GaussianBlur(bg, (0, 0), max(1.0, 5 * S)).astype(np.float32)
    inner = (slice(y1 - Y1, y2 - Y1), slice(x1 - X1, x2 - X1))
    ring = np.ones(ctx.shape[:2], bool); ring[inner] = False
    if ring.any():          # the max filter is brighter than real paper: match the surrounding tone
        gain = np.median(ctx[ring], axis=0) / np.maximum(np.median(bg[inner].reshape(-1, 3), axis=0), 1.0)
        bg *= gain
    return Image.fromarray(np.clip(bg[inner], 0, 255).astype(np.uint8))


def render(doc, trans, out_dir, fonts, background='paper', ink=INK, resolution='auto', para_indent_em=None,
           tracking=None, size_scale=1.0, word_space=1.0):
    """``tracking``: None = fit the letter-spacing per block so the translation fills the original
    line widths; a number = fixed letter-spacing in em (0 = none).  ``size_scale`` and ``word_space``
    multiply the chosen font size and the natural word space."""
    up = FIT_UP if tracking is None else 1.15
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    base = Path(doc.get('_dir', '.'))
    tr = trans['units']
    ratios = {st: fonts.ratios(st) for st in ('regular', 'bold')}
    report = {'units': [], 'pages': [], 'warnings': []}

    chains = {}                                   # unit -> [(page index, block)] in document order
    for pi, page in enumerate(doc['pages']):
        for b in page['blocks']:
            if b['kind'] == 'text' and b.get('unit_id'):
                chains.setdefault(b['unit_id'], []).append((pi, b))

    canvases, scales, originals = [], [], []
    for page in doc['pages']:
        f = float(page.get('scale') or 1.0)
        use_full = page.get('image_full') and resolution != 'work'
        S = f if use_full else 1.0
        orig = Image.open(base / (page['image_full'] if use_full else page['image'])).convert('RGB')
        canvas = orig.copy() if background == 'scan' else Image.new('RGB', orig.size, tuple(page['layout']['paper_color_rgb']))
        canvases.append(canvas); scales.append(S); originals.append(orig)

    # wipe the original text (scan background) and paste figures / page furniture
    for pi, (page, canvas, S) in enumerate(zip(doc['pages'], canvases, scales)):
        paper = tuple(page['layout']['paper_color_rgb'])
        for b in page['blocks']:
            if b['kind'] == 'text' and background == 'scan' and b.get('unit_id') in tr:
                x1, y1, x2, y2 = [int(round(v * S)) for v in (b['bbox'][0] - 3, b['bbox'][1] - 3, b['bbox'][2] + 3, b['bbox'][3] + 3)]
                canvas.paste(wipe(np.asarray(canvas), (x1, y1, x2, y2), S), (max(0, x1), max(0, y1)))
            if b['kind'] in ('figure', 'graphic'):
                img = Image.open(base / b['file']).convert('RGB')
                x1, y1, x2, y2 = b['crop_bbox']
                size = (int(round((x2 - x1) * S)), int(round((y2 - y1) * S)))
                if img.size != size:
                    img = img.resize(size, Image.LANCZOS)
                pos = (int(round(x1 * S)), int(round(y1 * S)))
                if b['kind'] == 'graphic':
                    # page numbers, rules: keep only the ink, not the scan's paper tone
                    arr = np.asarray(img).astype(np.float32)
                    paper_c = np.percentile(arr.reshape(-1, 3), 90, axis=0)
                    dark = np.clip(1.0 - arr / np.maximum(paper_c, 1.0), 0.0, 1.0)
                    ratio = 1.0 - np.clip((dark - 0.06) / 0.94, 0.0, 1.0)      # drop paper noise, keep the ink
                    region = np.asarray(canvas.crop((pos[0], pos[1], pos[0] + img.width, pos[1] + img.height))).astype(np.float32)
                    canvas.paste(Image.fromarray((region * ratio).astype(np.uint8)), pos)
                else:
                    canvas.paste(img, pos)
    draws = [ImageDraw.Draw(c) for c in canvases]

    def keep_original(frames, why):
        report['warnings'].append(why)
        for pi, b in frames:
            S = scales[pi]; x1, y1, x2, y2 = [int(round(v * S)) for v in b['bbox']]
            pad = int(round(2 * S))
            box = (max(0, x1 - pad), max(0, y1 - pad), x2 + pad, y2 + pad)
            canvases[pi].paste(originals[pi].crop(box), box[:2])

    # ------------------------------------------------ body text: one flow through the columns
    body_units = [u for u, fr in chains.items() if all(b['role'] == 'paragraph' for _, b in fr)]
    fixed_units = [u for u in chains if u not in body_units]
    body_units.sort(key=lambda u: (chains[u][0][0], chains[u][0][1]['bbox'][1]))
    for u in list(body_units):
        if u not in tr:
            keep_original(chains[u], '%s: no translation; original pixels kept' % u)
            body_units.remove(u)

    pages_body = {}                               # page index -> geometry of its body column
    for pi, page in enumerate(doc['pages']):
        bl = [b for b in page['blocks'] if b['kind'] == 'text' and b['role'] == 'paragraph']
        if not bl:
            continue
        col = page['layout']['columns'][0] if page['layout']['columns'] else None
        pitches = [b['typography']['line_pitch_px'] for b in bl if b['typography'].get('line_pitch_px')]
        pages_body[pi] = {'top': min(b['bbox'][1] for b in bl),
                          'x1': col['left'] if col else min(b['bbox'][0] for b in bl),
                          'width': (col['right'] - col['left']) if col else float(np.median([b['bbox'][2] - b['bbox'][0] for b in bl])),
                          'pitch': float(np.median(pitches)) if pitches else 22.0,
                          'cap': sum(b['line_count'] for b in bl), 'bottom': max(b['bbox'][3] for b in bl)}
    if body_units and pages_body:
        sample = [b for u in body_units for _, b in chains[u]]
        sizes = [calibrated_size(b, fonts, 'regular', False, ratios['regular'], up)[0] for b in sample]
        size0 = float(np.median(sizes)) * size_scale
        W = float(np.median([g['width'] for g in pages_body.values()]))
        ind = [b['typography'].get('first_line_indent_px') or 0 for b in sample]
        indent = float(np.median(ind)) if np.median(ind) > 6 else 0.0
        if para_indent_em is not None:
            indent = para_indent_em * size0
        total_cap = sum(g['cap'] for g in pages_body.values())
        for shrink in (1.0, 0.98, 0.96, 0.94, 0.92, 0.9):
            size = size0 * shrink
            font = fonts.get('regular', size * 8)
            btrack = tracking * size if tracking is not None else fit_track(sample, font, 8, size)
            typeset_units = {}
            for u in body_units:
                typeset_units[u] = typeset(tr[u]['pl'].strip(), font, W * 8, indent * 8, True,
                                           space_factor=word_space, track=btrack * 8)
            total = sum(len(v[0]) for v in typeset_units.values())
            if total <= total_cap:
                break
        if total > total_cap:
            report['warnings'].append('body text needs %d lines, the pages hold %d: overflow below the last frame' % (total, total_cap))
        order = sorted(pages_body)
        pos = 0; used = {pi: 0 for pi in order}
        r = ratios['regular']
        for u in body_units:
            lines, relaxed = typeset_units[u]
            first_page = None
            for line in lines:
                while pos < len(order) - 1 and used[order[pos]] >= pages_body[order[pos]]['cap']:
                    pos += 1
                pi = order[pos]; g = pages_body[pi]
                S = scales[pi]
                ft = fonts.get('regular', size * S)
                ln = {'pieces': [{'t': p['t'], 'w': p['w'] / 8 * S, 'brk': p['brk'], 'track': p.get('track', 0.0) / 8 * S} for p in line['pieces']],
                      'gap': line['gap'] / 8 * S, 'hyphen': line['hyphen'], 'indent': line['indent'] / 8 * S}
                baseline = g['top'] + r['asc'] * size + used[pi] * g['pitch']
                draw_line(draws[pi], ln, ft, g['x1'] * S, baseline * S, ink)
                used[pi] += 1
                if first_page is None:
                    first_page = pi
            report['units'].append({'unit': u, 'kind': 'body', 'lines': len(lines), 'size_px': round(size, 2),
                                    'tracking_px': round(btrack, 2), 'tracking_em': round(btrack / size, 3),
                                    'lines_original': sum(b['line_count'] for _, b in chains[u]),
                                    'hyphenated_lines': sum(l['hyphen'] for l in lines), 'relaxed_spacing': relaxed,
                                    'starts_on_page': doc['pages'][first_page]['page'] if first_page is not None else None})
        for pi in order:
            report['pages'].append({'page': doc['pages'][pi]['page'], 'body_lines_used': used[pi],
                                    'body_lines_capacity': pages_body[pi]['cap']})

    # ------------------------------------------------ headings, captions, bylines, footnotes: fixed frames
    for uid in fixed_units:
        frames = chains[uid]
        if uid not in tr:
            keep_original(frames, '%s: no translation; original pixels kept' % uid)
            continue
        text = tr[uid]['pl'].strip()
        first = frames[0][1]
        style, upper = block_style(first)
        if first['role'] == 'author_byline_candidate':
            style = 'italic'
        if upper:
            text = text.upper()
        t0 = first['typography']
        size0, s_h, s_w = calibrated_size(first, fonts, style, upper, ratios.get(style, ratios['regular']), up)
        if upper:
            size0 = float(min(max(s_w, 0.7 * s_h), 1.15 * s_h))
        size0 *= size_scale
        col = doc['pages'][frames[0][0]]['layout']['columns']
        col = col[0] if col else None
        anchor = anchor_of(first, col)
        capacity = sum(b['line_count'] for _, b in frames)
        W0 = min(b['bbox'][2] - b['bbox'][0] for _, b in frames)
        Ws = [W0]
        if col:
            avail = (col['right'] - first['bbox'][0]) if anchor == 'left' else (first['bbox'][2] - col['left'])
            # do not grow into a neighbouring block on the same rows (side-by-side captions)
            fx1, fy1, fx2, fy2 = first['bbox']
            for ob in doc['pages'][frames[0][0]]['blocks']:
                if ob is first or ob['kind'] not in ('text', 'figure'):
                    continue
                ox1, oy1, ox2, oy2 = ob['bbox']
                if min(fy2, oy2) - max(fy1, oy1) > 0.3 * (fy2 - fy1):
                    if anchor == 'left' and ox1 >= fx2:
                        avail = min(avail, ox1 - 6 - fx1)
                    elif anchor == 'right' and ox2 <= fx1:
                        avail = min(avail, fx2 - ox2 - 6)
            Ws = [W0 * k for k in (1.0, 1.06, 1.12, 1.2, 1.3) if W0 * k <= avail + 0.5] or [W0]
        chosen = None
        for shrink in (1.0, 0.98, 0.96, 0.94, 0.92, 0.9, 0.86, 0.82):
            size = size0 * shrink
            font = fonts.get(style, size * 8)
            track0 = 0.0
            if tracking is not None:
                track0 = tracking * size
            elif not upper:                               # fit the letter-spacing to the original line widths
                track0 = fit_track([first], font, 8, size)
            else:                                         # letter-space capitals up to the original line widths
                gaps = []
                for ln in first['lines']:
                    n = len(ln['text'].strip())
                    if n > 3:
                        nat = font.getlength(ln['text'].strip()) / 8
                        gaps.append(((ln['bbox'][2] - ln['bbox'][0]) - nat) / (n - 1))
                track0 = float(min(max(np.median(gaps) if gaps else 0.0, 0.0), 0.25 * size))
            for tf in ((1.0, 0.5, 0.0) if track0 > 0 else (1.0,)):
                track = track0 * tf
                for Wc in Ws:
                    lines, relaxed = typeset(text, font, Wc * 8, 0.0, False, space_factor=word_space, track=track * 8)
                    if len(lines) <= capacity and max(l['width'] for l in lines) / 8 <= Wc + 0.5:
                        chosen = (shrink, Wc, lines, relaxed, size, track); break
                if chosen:
                    break
            if chosen:
                break
        if not chosen:
            shrink, Wc = 0.82, Ws[-1]; size = size0 * shrink
            font = fonts.get(style, size * 8)
            lines, relaxed = typeset(text, font, Wc * 8, 0.0, False, space_factor=word_space, track=0.0)
            chosen = (shrink, Wc, lines, relaxed, size, 0.0)
            report['warnings'].append('%s: needs %d lines, frame holds %d (overflow)' % (uid, len(lines), capacity))
        shrink, Wc, lines, relaxed, size, track = chosen
        pitch = t0.get('line_pitch_px') or size * 1.5
        r = ratios.get(style, ratios['regular'])
        li = 0
        for fi, (pi, b) in enumerate(frames):
            S = scales[pi]
            ft = fonts.get(style, size * S)
            x1, x2, top = b['bbox'][0], b['bbox'][2], b['bbox'][1]
            asc = (r['cap'] if upper else r['asc']) * size
            n_here = b['line_count'] if fi < len(frames) - 1 else len(lines) - li
            for k in range(n_here):
                if li >= len(lines):
                    break
                line = lines[li]
                ln = {'pieces': [{'t': p['t'], 'w': p['w'] / 8 * S, 'brk': p['brk'], 'track': p.get('track', 0.0) / 8 * S} for p in line['pieces']],
                      'gap': line['gap'] / 8 * S, 'hyphen': line['hyphen'], 'indent': 0.0}
                width = line['width'] / 8
                x = x2 - width if anchor == 'right' else ((x1 + x2) / 2 - width / 2 if anchor == 'center' else x1)
                draw_line(draws[pi], ln, ft, x * S, (top + asc + k * pitch) * S, ink)
                li += 1
        report['units'].append({'unit': uid, 'kind': first['role'], 'lines': len(lines), 'capacity': capacity,
                                'size_px': round(size, 2), 'tracking_px': round(track, 2), 'tracking_em': round(track / size, 3),
                                'lines_original': capacity, 'shrink': shrink})

    # ------------------------------------------------ outputs
    names = []
    for page, canvas in zip(doc['pages'], canvases):
        name = 'page_%02d_%s.png' % (page['page'], page['side'])
        canvas.save(out_dir / name); names.append(name)
    H = 1100
    tiles = []
    for orig, canvas in zip(originals, canvases):
        for im in (orig, canvas):
            tiles.append(im.resize((int(im.width * H / im.height), H), Image.LANCZOS))
    comp = Image.new('RGB', (sum(t.width for t in tiles) + 10 * (len(tiles) - 1), H), (90, 90, 90))
    x = 0
    for t in tiles:
        comp.paste(t, (x, 0)); x += t.width + 10
    comp.save(out_dir / 'compare.png')
    (out_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return names, report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('document', type=Path, help='document.json written by booktrans.py')
    ap.add_argument('translations', type=Path, help='JSON with {"units": {unit_id: {"pl": text}}}')
    ap.add_argument('-o', '--output', type=Path, default=None, help='default: <document dir>/render')
    ap.add_argument('--font', type=Path, help='regular serif TTF with Polish letters (default: bundled Crimson Text)')
    ap.add_argument('--bold-font', type=Path)
    ap.add_argument('--italic-font', type=Path)
    ap.add_argument('--background', choices=['paper', 'scan'], default='paper',
                    help='paper: clean canvas (default); scan: the scanned page with text blocks wiped')
    ap.add_argument('--resolution', choices=['auto', 'work'], default='auto',
                    help='auto: full resolution when the scan has it; work: analysis scale')
    ap.add_argument('--tracking', default='auto',
                    help="letter-spacing: 'auto' (default) fits it per block so the translation fills the original "
                         "line widths; a number in em (e.g. 0.05) fixes it; 0 = none")
    ap.add_argument('--size-scale', type=float, default=1.0, help='multiply the chosen font size (default 1.0)')
    ap.add_argument('--word-space', type=float, default=1.0, help='multiply the natural word space (default 1.0)')
    ap.add_argument('--ink', default='%d,%d,%d' % INK)
    ap.add_argument('--paragraph-indent', type=float, default=None,
                    help='first-line indent in em (default: as in the original page, normally none)')
    a = ap.parse_args()
    doc = json.loads(a.document.read_text(encoding='utf-8'))
    doc['_dir'] = str(a.document.parent)
    trans = json.loads(a.translations.read_text(encoding='utf-8'))
    out = a.output or a.document.parent / 'render'
    ink = tuple(int(v) for v in a.ink.split(','))
    log.info('render', 'typesetting %d pages with %d translated units' % (len(doc['pages']), len(trans['units'])))
    if _HYPH is None:
        log.info('render', 'pyphen not installed: no hyphenation (pip install pyphen)')
    names, rep = render(doc, trans, out, Fonts(a.font, a.bold_font, a.italic_font), a.background, ink, a.resolution, a.paragraph_indent,
                      None if a.tracking == 'auto' else float(a.tracking), a.size_scale, a.word_space)
    for r in rep['units']:
        log.info('render', '%s %s: %d lines (original %d), size %.1f px, tracking %.3f em' % (r['unit'], r['kind'], r['lines'], r['lines_original'], r['size_px'], r['tracking_em']))
    for r in rep['pages']:
        log.info('render', 'page %d body: %d of %d lines used' % (r['page'], r['body_lines_used'], r['body_lines_capacity']))
    for w in rep['warnings']:
        log.info('render', 'WARNING ' + w)
    log.info('render', 'wrote %s' % ', '.join(names) + ', compare.png, report.json in %s' % out)


if __name__ == '__main__':
    main()

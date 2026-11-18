#!/usr/bin/env python3
"""Typesetting: fonts, pieces with break kinds, optimum-fit line breaking (no drawing, no page model).

Used by ``layout.py`` (the layout plan) and ``render.py`` (drawing).  All widths are in the units of the
font that was passed in; callers measure at 8x the working size for sub-pixel precision.
"""
import re
import sys
from pathlib import Path

import numpy as np
from PIL import ImageFont

HERE = Path(__file__).resolve().parent

try:
    import pyphen
    _HYPH = pyphen.Pyphen(lang='pl_PL')
except Exception:       # pyphen is optional: without it words are not hyphenated
    _HYPH = None

DEFAULT_FONT_DIRS = [HERE.parent.parent / 'fonts']
SINGLE_LETTER = {'a', 'i', 'o', 'u', 'w', 'z'}      # Polish: do not end a line with these
RMIN, RMAX = 0.75, 2.0           # allowed word-space stretch (1 = natural)


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
        desc = f.getbbox('gjpqy', anchor='ls')[3] / 200.0
        return {'cap': cap, 'asc': asc, 'box': box, 'desc': desc}


# ---------------------------------------------------------------- typesetting
_REF = 800.0                     # pieces are measured once per text at this size and scaled (fonts scale linearly)
_PIECES = {}
_REF_FONTS = {}


def pieces_of(text, font, track=0.0):
    """Split text into pieces with break kinds: space | glue | hard | soft | None (last).

    Widths are measured once per (font file, text) at a reference size and scaled to ``font.size``; the
    layout tries many sizes of the same paragraph, and measuring is the expensive part."""
    path = getattr(font, 'path', None)
    if not path:
        return _pieces_of(text, font, track)
    key = (path, getattr(font, 'index', 0), text)
    base = _PIECES.get(key)
    if base is None:
        rf = _REF_FONTS.get(path)
        if rf is None:
            rf = _REF_FONTS[path] = ImageFont.truetype(path, _REF)
        if len(_PIECES) > 20000:
            _PIECES.clear()
        base = _PIECES[key] = _pieces_of(text, rf, 0.0)
    k = font.size / _REF
    return [{'t': p['t'], 'w': p['w'] * k + track * len(p['t']), 'brk': p['brk'], 'track': track} for p in base]


def _pieces_of(text, font, track=0.0):
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


def typeset(text, font, W, indent, justify, space_factor=1.0, track=0.0, widths=None):
    """Lines for ``text``; each line is a dict with pieces, gap, hyphen flag and justification.

    ``widths``: optional callable ``line_no -> width`` (line slots of different widths, e.g. beside a
    figure); the first-line ``indent`` is subtracted from it.  Without it every line is ``W`` wide."""
    pieces = pieces_of(text, font, track)
    space = font.getlength(' ') * space_factor + track
    hyph_w = font.getlength('-')
    base = widths if widths is not None else (lambda ln: W)
    widths = lambda ln: base(ln) - (indent if ln == 0 else 0.0)
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


def line_words(line):
    """A typeset line as drawable words: ``[[text, width], ...]``.  Pieces of one word (hyphenation parts,
    a hard break after a dash) are joined; word gaps come between the returned words."""
    words, cur, w = [], '', 0.0
    for i, p in enumerate(line['pieces']):
        cur += p['t']; w += p['w']
        if p['brk'] in ('space', 'glue') or i == len(line['pieces']) - 1:
            words.append([cur, w]); cur, w = '', 0.0
    return words


def text_of(lines):
    """The text of typeset lines (to typeset the rest of a paragraph again, e.g. on the next page)."""
    out = ''
    for ln in lines:
        for p in ln['pieces']:
            out += p['t']
            if p['brk'] in ('space', 'glue'):
                out += ' '
        if ln['pieces'] and ln['pieces'][-1]['brk'] in ('space', 'glue', None):
            out = out.rstrip() + ' '
    return out.strip()

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
FAMILY_STYLES = {'bold': ('SemiBold', 'Bold', 'Medium'), 'italic': ('Italic', 'LightItalic', 'BookItalic')}


def font_spec(spec):
    """'path/Font.ttf' or 'path/Font[wght].ttf@300' (a variable font at that weight) -> (path, weight or None)."""
    s = str(spec)
    if '@' in s:
        path, w = s.rsplit('@', 1)
        try:
            return path, float(w.replace('wght=', ''))
        except ValueError:
            pass
    return s, None


def load_font(spec, size):
    path, wght = font_spec(spec)
    f = ImageFont.truetype(path, size)
    if wght is not None:
        axes = f.get_variation_axes()
        f.set_variation_by_axes([wght if a.get('name', b'') in (b'Weight', 'Weight') or i == 0 else a['default']
                                 for i, a in enumerate(axes)])
    f.bt_spec = str(spec)
    return f


def family_of(regular, style):
    """A sibling of a regular font file for ``style`` (``X-Regular.ttf`` -> ``X-SemiBold.ttf`` / ``X-Italic.ttf``,
    ``X-Light.ttf`` -> ``X-LightItalic.ttf``), or None."""
    path, wght = font_spec(regular)
    p = Path(path)
    stem, suf = p.stem, p.suffix
    base, _, weight = stem.rpartition('-')
    if not base:
        base, weight = stem, ''
    if wght is not None:                              # a variable font: its italic sibling, same weight; bold = heavier
        if style == 'bold':
            return '%s@%g' % (path, min(900, wght + 300))
        for cand in (p.with_name(stem.replace('[', '-Italic[', 1) + suf), p.with_name(base + '-Italic' + suf)):
            if cand.exists():
                return '%s@%g' % (cand, wght)
        return None
    names = list(FAMILY_STYLES[style])
    if style == 'italic' and weight and weight not in ('Regular', 'Book'):
        names.insert(0, weight + 'Italic')
    for n in names:
        cand = p.with_name('%s-%s%s' % (base, n, suf))
        if cand.exists() and cand != p:
            return str(cand)
    return None


class Fonts:
    """The regular, bold and italic font of the rebuilt text.  A font is a TTF/OTF path, or ``path@weight`` for a
    variable font.  Without bold / italic, siblings of the regular file are used (``X-Regular.ttf`` ->
    ``X-SemiBold.ttf``, ``X-Italic.ttf``), else the regular font itself; nothing is mixed in from another family."""

    def __init__(self, regular=None, bold=None, italic=None):
        def find(name):
            for d in DEFAULT_FONT_DIRS:
                p = d / name
                if p.exists():
                    return str(p)
            raise SystemExit('font %s not found; pass --font (a serif TTF with Polish letters)' % name)
        reg = str(regular) if regular else find('CrimsonText-Regular.ttf')
        if not Path(font_spec(reg)[0]).is_file():
            raise SystemExit('font file not found: %s' % font_spec(reg)[0])
        self.paths = {'regular': reg,
                      'bold': str(bold) if bold else (family_of(reg, 'bold') or reg),
                      'italic': str(italic) if italic else (family_of(reg, 'italic') or reg)}
        self._cache = {}
        self._ratios = {}

    def get(self, style, size):
        key = (style, round(size, 2))
        if key not in self._cache:
            if len(self._cache) > 2000:
                self._cache.clear()
            self._cache[key] = load_font(self.paths[style], size)
        return self._cache[key]

    def ratios(self, style='regular'):
        if style in self._ratios:
            return self._ratios[style]
        f = self.get(style, 200)
        cap = -f.getbbox('H', anchor='ls')[1] / 200.0
        asc = -f.getbbox('dbl', anchor='ls')[1] / 200.0
        box = (f.getbbox('Hg', anchor='ls')[3] - f.getbbox('Hg', anchor='ls')[1]) / 200.0
        desc = f.getbbox('gjpqy', anchor='ls')[3] / 200.0
        x = -f.getbbox('x', anchor='ls')[1] / 200.0
        self._ratios[style] = {'cap': cap, 'asc': asc, 'box': box, 'desc': desc, 'x': x}
        return self._ratios[style]


# ---------------------------------------------------------------- typesetting
_REF = 800.0                     # pieces are measured once per text at this size and scaled (fonts scale linearly)
_PIECES = {}
_REF_FONTS = {}


def pieces_of(text, font, track=0.0):
    """Split text into pieces with break kinds: space | glue | hard | soft | None (last).

    Widths are measured once per (font file, text) at a reference size and scaled to ``font.size``; the
    layout tries many sizes of the same paragraph, and measuring is the expensive part."""
    spec = getattr(font, 'bt_spec', None) or getattr(font, 'path', None)
    if not spec:
        return _pieces_of(text, font, track)
    key = (spec, getattr(font, 'index', 0), text)
    base = _PIECES.get(key)
    if base is None:
        rf = _REF_FONTS.get(spec)
        if rf is None:
            rf = _REF_FONTS[spec] = load_font(spec, _REF) if getattr(font, 'bt_spec', None) else ImageFont.truetype(spec, _REF)
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


# ---------------------------------------------------------------- emphasis inside a paragraph
TAG = re.compile(r'</?(i|b|sp)>')
SP_EM = 0.16                     # extra letter-spacing of <sp> (Sperrsatz, the German emphasis by spacing), in em


def has_tags(text):
    return TAG.search(text) is not None


def strip_tags(text):
    return TAG.sub('', text)


def upper_keep_tags(text):
    return ''.join(seg if TAG.fullmatch(seg) else seg.upper() for seg in re.split(r'(</?(?:i|b|sp)>)', text))


def _style_of(base, tags):
    if 'b' in tags:
        return 'bold'
    if 'i' in tags:
        return 'italic'
    return base


def styled_pieces(text, fonts_for, base_style, track=0.0, sp_track=0.0):
    """Pieces of a text with ``<i>``, ``<b>``, ``<sp>`` runs (``fonts_for(style)`` gives the font at the measuring
    size).  A word in one run is split as usual (hyphenation, hard breaks); a word that changes style inside is one
    unbreakable group of pieces (break kind ``join`` between them).  Every piece carries ``tags`` (the open tags), and
    ``style`` / ``sp`` when they differ from the paragraph's."""
    chars, open_ = [], set()
    pos = 0
    for m in TAG.finditer(text):
        chars += [(c, frozenset(open_)) for c in text[pos:m.start()]]
        name = m.group(1)
        if m.group(0).startswith('</'):
            open_.discard(name)
        else:
            open_.add(name)
        pos = m.end()
    chars += [(c, frozenset(open_)) for c in text[pos:]]
    words, cur = [], []
    for c, t in chars:
        if c.isspace():
            if cur:
                words.append(cur); cur = []
        else:
            cur.append((c, t))
    if cur:
        words.append(cur)
    out = []
    plain = [''.join(c for c, _ in w) for w in words]
    for wi, w in enumerate(words):
        runs = []
        for c, t in w:
            if runs and runs[-1][1] == t:
                runs[-1][0] += c
            else:
                runs.append([c, t])
        word = plain[wi]
        glue = word.lower().strip('.,;:') in SINGLE_LETTER and wi + 1 < len(words) and len(word.strip('.,;:')) == 1
        end = None if wi == len(words) - 1 else ('glue' if glue else 'space')
        if len(runs) == 1:
            t = runs[0][1]
            st = _style_of(base_style, t)
            tr = track + (sp_track if 'sp' in t else 0.0)
            ps = pieces_of(word, fonts_for(st), tr)
            ps[-1]['brk'] = end
        else:
            ps = []
            for ri, (rt, t) in enumerate(runs):
                st = _style_of(base_style, t)
                tr = track + (sp_track if 'sp' in t else 0.0)
                ps.append({'t': rt, 'w': fonts_for(st).getlength(rt) + tr * len(rt), 'brk': 'join', 'track': tr})
                ps[-1]['_t'] = t
            ps[-1]['brk'] = end
        for p in ps:
            t = p.pop('_t', runs[0][1])
            p['tags'] = tuple(sorted(t))
            st = _style_of(base_style, t)
            if st != base_style:
                p['style'] = st
            if 'sp' in t:
                p['sp'] = True
        out += ps
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


def typeset(text, font, W, indent, justify, space_factor=1.0, track=0.0, widths=None, fonts_for=None, style='regular'):
    """Lines for ``text``; each line is a dict with pieces, gap, hyphen flag and justification.

    ``widths``: optional callable ``line_no -> width`` (line slots of different widths, e.g. beside a
    figure); the first-line ``indent`` is subtracted from it.  Without it every line is ``W`` wide.
    ``fonts_for(style)`` (fonts at the measuring size) enables ``<i>``/``<b>``/``<sp>`` runs in ``text``; without it
    tags are dropped."""
    if has_tags(text) and fonts_for is not None:
        pieces = styled_pieces(text, fonts_for, style, track, SP_EM * font.size)
    else:
        pieces = pieces_of(strip_tags(text), font, track)
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
        out.append({'pieces': seg, 'gap': gap, 'space': space, 'hyphen': hyph, 'width': wpieces + gaps * gap,
                    'indent': indent if ln == 0 else 0.0, 'last': last})
    return out, relaxed


def line_words(line):
    """A typeset line as drawable words: ``[[text, width], ...]``; an entry gets a third element ``{style, track,
    join}`` when its style or letter-spacing differs from the paragraph's (``join``: no word space after it, the
    word goes on in another style).  Pieces of one word in one style are joined."""
    words, cur = [], None
    pieces = line['pieces']
    for i, p in enumerate(pieces):
        key = (p.get('style'), p['track'] if p.get('sp') else None)
        if cur is not None and cur['key'] == key:
            cur['t'] += p['t']; cur['w'] += p['w']
        else:
            if cur is not None:                     # style change inside a word
                words.append(_entry(cur, join=True))
            cur = {'t': p['t'], 'w': p['w'], 'key': key}
        if p['brk'] in ('space', 'glue') or i == len(pieces) - 1:
            words.append(_entry(cur, join=False)); cur = None
    return words


def _entry(cur, join):
    style, track = cur['key']
    meta = {}
    if style:
        meta['style'] = style
    if track is not None:
        meta['track'] = track
    if join:
        meta['join'] = True
    return [cur['t'], cur['w'], meta] if meta else [cur['t'], cur['w']]


def text_of(lines):
    """The text of typeset lines (to typeset the rest of a paragraph again, e.g. on the next page), with its
    ``<i>``/``<b>``/``<sp>`` tags."""
    out, open_, space = '', (), ''
    for ln in lines:
        for p in ln['pieces']:
            tags = p.get('tags', ())
            if tags != open_:
                out += ''.join('</%s>' % t for t in reversed(open_) if t not in tags)
                out += space; space = ''
                out += ''.join('<%s>' % t for t in tags if t not in open_)
                open_ = tags
            out += space + p['t']; space = ''
            if p['brk'] in ('space', 'glue'):
                space = ' '
        if ln['pieces'] and ln['pieces'][-1]['brk'] in ('space', 'glue', None):
            space = ' '
    return (out + ''.join('</%s>' % t for t in reversed(open_))).strip()

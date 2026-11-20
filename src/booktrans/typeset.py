#!/usr/bin/env python3
"""Typesetting: fonts, pieces with break kinds, optimum-fit line breaking (no drawing, no page model).

Used by ``layout.py`` (the layout plan) and ``render.py`` (drawing).  All widths are in the units of the
font that was passed in; callers measure at 8x the working size for sub-pixel precision.
"""
import json
import os
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

ROOT = HERE.parent.parent
DEFAULT_FONT_DIRS = [ROOT / 'fonts']
DEFAULT_FONTS_FILE = ROOT / 'config' / 'fonts.json'
BUILTIN_FONTS = {'regular': 'fonts/CrimsonText-Regular.ttf', 'bold': 'fonts/CrimsonText-SemiBold.ttf',
                 'italic': 'fonts/CrimsonText-Italic.ttf'}
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


def _resolve(spec, base=ROOT):
    path, wght = font_spec(spec)
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = Path(base) / p
    return '%s@%g' % (p, wght) if wght is not None else str(p)


def default_fonts_file():
    """The file that names the default font: ``$BOOKTRANS_FONTS`` or ``config/fonts.json``."""
    env = os.environ.get('BOOKTRANS_FONTS', '').strip()
    return Path(os.path.expanduser(env)) if env else DEFAULT_FONTS_FILE


def default_fonts():
    """The default font as {regular, bold, italic} specs (absolute paths; bold / italic None when the file does not
    name them), from ``default_fonts_file()``; the bundled Crimson Text when there is no such file.  Raises
    ``ValueError`` for a file that cannot be read or names no regular font."""
    f = default_fonts_file()
    if not f.exists():
        if os.environ.get('BOOKTRANS_FONTS', '').strip():
            raise ValueError('BOOKTRANS_FONTS: %s does not exist' % f)
        return {k: _resolve(v) for k, v in BUILTIN_FONTS.items()}
    try:
        data = json.loads(f.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise ValueError('cannot read %s: %s' % (f, e))
    if not isinstance(data, dict) or not str(data.get('regular') or '').strip():
        raise ValueError('%s names no "regular" font' % f)
    base = ROOT if f == DEFAULT_FONTS_FILE else f.parent
    return {k: (_resolve(str(data[k]).strip(), base) if str(data.get(k) or '').strip() else None)
            for k in ('regular', 'bold', 'italic')}


class Fonts:
    """The regular, bold and italic font of the rebuilt text.  Without a regular font: the configured default
    (``config/fonts.json`` or ``$BOOKTRANS_FONTS``, see ``default_fonts``).  A font is a TTF/OTF path, or ``path@weight`` for a
    variable font.  Without bold / italic, siblings of the regular file are used (``X-Regular.ttf`` ->
    ``X-SemiBold.ttf``, ``X-Italic.ttf``), else the regular font itself; nothing is mixed in from another family."""

    def __init__(self, regular=None, bold=None, italic=None, f2=None):
        if not regular:                               # the configured default font (config/fonts.json)
            try:
                d = default_fonts()
            except ValueError as e:
                raise SystemExit('default font: %s' % e)
            regular = d['regular']
            bold = bold or d['bold']
            italic = italic or d['italic']
        reg = str(regular)
        for spec in (reg, bold, italic, f2):
            if spec and not Path(font_spec(str(spec))[0]).is_file():
                raise SystemExit('font file not found: %s (pass --font, or set the default in %s)'
                                 % (font_spec(str(spec))[0], default_fonts_file()))
        self.paths = {'regular': reg,
                      'bold': str(bold) if bold else (family_of(reg, 'bold') or reg),
                      'italic': str(italic) if italic else (family_of(reg, 'italic') or reg),
                      'f2': str(f2) if f2 else reg}           # "font B" (<f2>): another typeface, e.g. for quotations
        self._cache = {}
        self._ratios = {}

    @property
    def name(self):
        return Path(font_spec(self.paths['regular'])[0]).stem

    def get(self, style, size):
        """The font of ``style`` (regular, bold, italic, f2; ``sup_<style>``: that font at ``SUP_SCALE`` of the size, for
        superscripts) at ``size`` px."""
        key = (style, round(size, 2))
        if key not in self._cache:
            if len(self._cache) > 2000:
                self._cache.clear()
            if style.startswith('sup_'):
                self._cache[key] = load_font(self.paths[style[4:]], size * SUP_SCALE)
            else:
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
TAG = re.compile(r'</?(i|b|sp|f2|sup)>')
TAGS = ('i', 'b', 'sp', 'f2', 'sup')   # italic, bold, Sperrsatz, font B, superscript
SP_EM = 0.16                     # extra letter-spacing of <sp> (Sperrsatz, the German emphasis by spacing), in em
SUP_SCALE = 0.62                 # size of <sup> (a note number) relative to the line
SUP_RAISE = 0.36                 # how far <sup> sits above the baseline, in the line's size


def has_tags(text):
    return TAG.search(text) is not None


def strip_tags(text):
    return TAG.sub('', text)


def upper_keep_tags(text):
    return ''.join(seg if TAG.fullmatch(seg) else seg.upper() for seg in re.split(r'(</?(?:i|b|sp|f2|sup)>)', text))


def _style_of(base, tags):
    """The font style of characters inside ``tags``: font B over bold over italic over the paragraph's; inside
    ``<sup>`` the superscript variant of it."""
    if 'f2' in tags:
        s = 'f2'
    elif 'b' in tags:
        s = 'bold'
    elif 'i' in tags:
        s = 'italic'
    else:
        s = base
    return 'sup_' + s if 'sup' in tags else s


def styled_pieces(text, fonts_for, base_style, track=0.0, sp_track=0.0):
    """Pieces of a text with ``<i>``, ``<b>``, ``<sp>``, ``<f2>``, ``<sup>`` runs (``fonts_for(style)`` gives the font at the measuring
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


def break_lines(pieces, widths, space, hyph_w, justify, rmin=RMIN, rmax=RMAX, uniform=False):
    """Optimum-fit line breaking. ``widths(line_no)`` gives each line's width; ``uniform``: every line after the
    first has the same width (the memo then ignores the line number, which keeps long ragged texts fast).

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
        k = (a, min(ln, 1) if uniform else ln)
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


def _merge_soft(pieces):
    """Pieces with the hyphenation points removed (a word is one piece again): for text that is never hyphenated."""
    out = []
    for p in pieces:
        if out and out[-1]['brk'] == 'soft':
            q = out[-1]
            out[-1] = dict(q, t=q['t'] + p['t'], w=q['w'] + p['w'], brk=p['brk'])
        else:
            out.append(dict(p))
    return out


def typeset(text, font, W, indent, justify, space_factor=1.0, track=0.0, widths=None, fonts_for=None, style='regular',
            uniform=None, hyphenate=True):
    """Lines for ``text``; each line is a dict with pieces, gap, hyphen flag and justification.

    ``widths``: optional callable ``line_no -> width`` (line slots of different widths, e.g. beside a
    figure); the first-line ``indent`` is subtracted from it.  Without it every line is ``W`` wide.
    ``fonts_for(style)`` (fonts at the measuring size) enables ``<i>``/``<b>``/``<sp>`` runs in ``text``; without it
    tags are dropped.  ``hyphenate=False``: no word is split (captions, headings)."""
    if has_tags(text) and fonts_for is not None:
        pieces = styled_pieces(text, fonts_for, style, track, SP_EM * font.size)
    else:
        pieces = pieces_of(strip_tags(text), font, track)
    if not hyphenate:                                 # captions and headings: words are never split
        pieces = _merge_soft(pieces)
    space = font.getlength(' ') * space_factor + track
    hyph_w = font.getlength('-')
    base_widths = widths
    base = widths if widths is not None else (lambda ln: W)
    widths = lambda ln: base(ln) - (indent if ln == 0 else 0.0)
    uni = (base_widths is None) if uniform is None else uniform
    lines = break_lines(pieces, widths, space, hyph_w, justify, uniform=uni)
    relaxed = False
    if lines is None:
        lines = break_lines(pieces, widths, space, hyph_w, justify, rmin=0.5, rmax=4.0, uniform=uni)
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

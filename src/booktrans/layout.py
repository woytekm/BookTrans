#!/usr/bin/env python3
"""Layout plan: where every line of the translated text goes on which page (no drawing).

    plan = layout.make_plan(doc, translations, fonts, settings)

The plan is plain JSON.  For every page it holds the lines to draw (words with their widths, font
style and size, letter-spacing, x, baseline, the justification gap), the blocks whose original pixels
are kept, and the body-text fit of the page; plus per-unit measurements and warnings for
``report.json``.  ``render.py`` draws any page from it, so one page can be redrawn (or shown in the GUI)
without drawing the whole book.  All coordinates and sizes are working pixels of the page image.

Body text (paragraphs) flows through **line slots**: every original body line is a slot (its left and
right edge, its top), in reading order (column by column).  The Polish lines are poured into the slots,
so the text keeps the original line positions and never runs over a figure, a heading or a gap in the
column, and it can flow through two columns or beside a picture.

The flow is **kept in step with the pages**: a paragraph never starts before the page it started on in
the original, and the size and letter-spacing are fitted per page (within narrow limits, around one size
for the whole book) so that every paragraph that started on a page starts there again and the page is
filled.  A page that cannot hold its paragraphs pushes the rest to the next page (the report says so);
the next page is back in step as soon as it can be.  A paragraph's first line is not left alone at the
foot of a page (orphan), nor its last line alone at the head of the next (widow).

Headings, captions, bylines and footnotes are set in their original block ("fixed frames").
"""
import numpy as np

from typeset import typeset, line_words, text_of, upper_keep_tags

SCHEMA = 'booktrans.layout/1'
M = 8                            # text is measured at M x the working size (sub-pixel precision)
FIT_UP = 1.04                    # with fitted tracking the size may exceed the glyph-height size by this much
FIT_MAX_EM = 0.12                # fitted letter-spacing is capped at this fraction of the font size

DEFAULTS = {
    'tracking': None,            # None: fitted; a number: fixed letter-spacing in em
    'size_scale': 1.0,           # multiplies the chosen font size
    'word_space': 1.0,           # multiplies the natural word space
    'paragraph_indent_em': None, # None: as in the original
    'page_fit': True,            # fit size/tracking of the body text per page (False: one size, shrink only)
    'blocks': {},                # human adjustments per block id (render_overrides.resolve)
    'pages': {},                 # human adjustments of the body fit per page index
}

# Per-page fit of the body text: (size factor, letter-spacing change in em, visual cost).  Tried in order of
# cost; letter-spacing is less visible than size, a smaller size less than a larger one.
PAGE_FIT = sorted([(1.0, 0.0, 0), (1.0, -0.01, 2), (1.0, 0.01, 2), (1.0, -0.02, 5), (1.0, 0.02, 5),
                   (0.985, 0.0, 6), (0.985, -0.01, 8), (1.015, 0.0, 8), (0.97, 0.0, 10), (0.97, -0.02, 14),
                   (1.03, 0.0, 14), (0.955, 0.0, 16), (0.94, 0.0, 22), (0.92, 0.0, 30), (0.90, -0.02, 40)],
                  key=lambda c: c[2])
PAGE_SHRINK = [(f, 0.0, k * 10) for k, f in enumerate((1.0, 0.98, 0.96, 0.94, 0.92, 0.9))]
# Costs of a page's flow (see _Flow.simulate).
COST_DELAY = 1000                # a paragraph that started on this page in the original starts later
COST_BLANK = 25                  # an empty line slot at the foot of a page
COST_CARRY = 12                  # a line carried to the next page beyond what the original carried
COST_BREAK = 40                  # an orphan or widow that had to be avoided (leaves a slot empty)
COST_OVERFLOW = 500              # a line beyond the last page
COST_CHANGE = (300, 400)         # per unit of size factor / em of letter-spacing that differs from the page before


# ---------------------------------------------------------------- block helpers
def block_style(block):
    t = block['typography']
    upper = t.get('uppercase_ratio', 0) >= 0.6
    style = 'bold' if block['role'] in ('heading',) and not upper else 'regular'
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


def line_tops(block):
    """Top y of every original line of a block, regularised to the block's line pitch (OCR line boxes
    jitter by a pixel or two with the letters in them); a line clearly off the grid keeps its own top."""
    tops = [float(l['bbox'][1]) for l in block.get('lines') or []]
    pitch = block['typography'].get('line_pitch_px')
    if not tops:
        return [float(block['bbox'][1])]
    if len(tops) < 2 or not pitch:
        return tops
    off = float(np.median([t - k * pitch for k, t in enumerate(tops)]))
    return [off + k * pitch if abs(off + k * pitch - t) <= 0.35 * pitch else t for k, t in enumerate(tops)]


def _overlap(a1, a2, b1, b2):
    return max(0.0, min(a2, b2) - max(a1, b1))


def columns_of(blocks, layout_cols):
    """Body columns of a page from its body blocks: [(left, right)], left to right, and the column index of
    every block.  Blocks are grouped by horizontal overlap; with one group the measured justified edges of
    the page (``layout.columns``) are used when present."""
    groups = []
    for b in sorted(blocks, key=lambda b: b['bbox'][0]):
        x1, _, x2, _ = b['bbox']
        for g in groups:
            if _overlap(x1, x2, g['x1'], g['x2']) >= 0.5 * min(x2 - x1, g['x2'] - g['x1']):
                g['blocks'].append(b); g['x1'] = min(g['x1'], x1); g['x2'] = max(g['x2'], x2)
                break
        else:
            groups.append({'x1': x1, 'x2': x2, 'blocks': [b]})
    groups.sort(key=lambda g: g['x1'])
    cols, index = [], {}
    for ci, g in enumerate(groups):
        if len(groups) == 1 and layout_cols:
            L, R = layout_cols[0]['left'], layout_cols[0]['right']
        else:
            multi = [b for b in g['blocks'] if b.get('line_count', len(b.get('lines') or [])) >= 2] or g['blocks']
            L = float(np.median([b['bbox'][0] for b in multi]))
            R = float(max(b['bbox'][2] for b in multi))
        cols.append((float(L), float(R)))
        for b in g['blocks']:
            index[b['block_id']] = ci
    return cols, index


# ---------------------------------------------------------------- body text
class _Flow:
    """The body text of the book: line slots per page and the page-by-page flow."""

    def __init__(self, doc, tr, fonts, settings, chains, body_units, ratios, report):
        self.doc, self.tr, self.fonts, self.settings = doc, tr, fonts, settings
        self.chains, self.ratios, self.report = chains, ratios, report
        self.slots = {}                     # page index -> [slot]
        rank = {}
        body_set = set(body_units)
        for pi, page in enumerate(doc['pages']):
            bl = [b for b in page['blocks'] if b['kind'] == 'text' and b.get('unit_id') in body_set]
            if not bl:
                continue
            cols, ci = columns_of(bl, page['layout'].get('columns') or [])
            bl.sort(key=lambda b: (ci[b['block_id']], b['bbox'][1]))
            obstacles = [o for o in page['blocks'] if o not in bl and o['kind'] in ('figure', 'graphic', 'text')]
            slots = []
            for k, b in enumerate(bl):
                rank[b['block_id']] = (pi, k)
                pitch = b['typography'].get('line_pitch_px') or 22.0
                L, R = cols[ci[b['block_id']]]
                for top in line_tops(b):
                    x1, x2 = L, R
                    band = (top + 0.15 * pitch, top + 0.75 * pitch)
                    gap = max(6.0, 0.5 * pitch)
                    for o in obstacles:                 # a figure beside the line (text wrapped around it)
                        ox1, oy1, ox2, oy2 = o['bbox']
                        if _overlap(band[0], band[1], oy1, oy2) > 0 and _overlap(x1, x2, ox1, ox2) > 0:
                            if (ox1 + ox2) / 2 < (x1 + x2) / 2:
                                x1 = max(x1, ox2 + gap)
                            else:
                                x2 = min(x2, ox1 - gap)
                    if x2 - x1 >= 0.25 * (R - L):
                        slots.append({'block': b['block_id'], 'top': top, 'x1': x1, 'x2': x2, 'pitch': pitch})
            if slots:
                self.slots[pi] = slots
        self.order = sorted(self.slots)
        self.units = sorted(body_units, key=lambda u: rank.get(chains[u][0][1]['block_id'], (chains[u][0][0], 1e9)))
        self.start = {u: chains[u][0][0] for u in body_units}
        # lines of the paragraph that crosses out of a page in the original (0 when the page ends a paragraph)
        self.orig_carry = {}
        for u in body_units:
            fr = chains[u]
            for k, (pi, _) in enumerate(fr):
                later = sum(b['line_count'] for pj, b in fr[k + 1:] if pj > pi)
                if later:
                    self.orig_carry[pi] = later

        sample = [b for u in body_units for _, b in chains[u]]
        sizes = [calibrated_size(b, fonts, 'regular', False, ratios['regular'], self.fit_up)[0] for b in sample]
        self.size0 = float(np.median(sizes)) * settings['size_scale']
        if settings['tracking'] is None:
            self.track_em = fit_track(sample, fonts.get('regular', self.size0 * M), M, self.size0) / self.size0
        else:
            self.track_em = float(settings['tracking'])
        ind = [b['typography'].get('first_line_indent_px') or 0 for b in sample]
        self.indent = float(np.median(ind)) if np.median(ind) > 6 else 0.0
        if settings['paragraph_indent_em'] is not None:
            self.indent = settings['paragraph_indent_em'] * self.size0
        if not settings['page_fit']:
            self.cands = PAGE_SHRINK
        elif settings['tracking'] is not None:
            self.cands = [c for c in PAGE_FIT if c[1] == 0.0]
        else:
            self.cands = PAGE_FIT
        self._cache = {}

    @property
    def fit_up(self):
        return FIT_UP if self.settings['tracking'] is None else 1.15

    def _typeset(self, text, size, track, widths, indent):
        if widths and all(w == widths[0] for w in widths):
            widths = widths[:1]                         # uniform slots: one cache entry wherever the text starts
        key = (text, round(size, 3), round(track, 3), widths, round(indent, 2))
        if key not in self._cache:
            font = self.fonts.get('regular', size * M)
            wf = lambda ln: (widths[ln] if ln < len(widths) else widths[-1]) * M
            self._cache[key] = typeset(text, font, None, indent * M, True, space_factor=self.settings['word_space'],
                                       track=track * M, widths=wf, fonts_for=lambda st: self.fonts.get(st, size * M))
        return self._cache[key]

    def simulate(self, pi, cand, carry, pending, prev=None):
        """Fill the slots of page ``pi`` with ``carry`` (the rest of a paragraph from the page before) and the
        paragraphs that may start here; returns (cost, placed, new carry, started units, stats)."""
        f, dtrack, vis = cand
        size = self.size0 * f
        track = max(0.0, self.track_em + dtrack) * size
        slots = self.slots[pi]; n = len(slots)
        k_next = self.order.index(pi) + 1
        after = self.slots[self.order[k_next]] if k_next < len(self.order) else slots[-1:]
        widths_all = tuple(round(s['x2'] - s['x1'], 2) for s in slots + after)
        items = ([carry] if carry else []) + [{'unit': u, 'text': self.tr[u]['pl'].strip(), 'fresh': True}
                                             for u in pending if self.start[u] <= pi]
        i, placed, new_carry, started, breaks, relaxed, orphan = 0, [], None, [], 0, False, None
        for it in items:
            ind = self.indent if it['fresh'] else 0.0
            lines, rel = self._typeset(it['text'], size, track, widths_all[i:], ind)
            relaxed |= rel
            room = n - i
            if len(lines) <= room:
                placed.append((it, lines, i)); i += len(lines)
                if it['fresh']:
                    started.append(it['unit'])
                continue
            cut = room
            if it['fresh'] and cut == 1 and len(lines) > 1:
                cut = 0; breaks += 1; orphan = it['unit']  # orphan: the first line alone at the foot
            elif len(lines) - cut == 1 and cut >= 2:
                cut -= 1; breaks += 1                      # widow: the last line alone at the next head
            if cut > 0:
                placed.append((it, lines[:cut], i)); i += cut
                if it['fresh']:
                    started.append(it['unit'])
                new_carry = {'unit': it['unit'], 'text': text_of(lines[cut:]), 'fresh': False,
                             'lines': len(lines) - cut}
            break
        delayed = sum(1 for it in items if it['fresh'] and it['unit'] not in started and it['unit'] != orphan)
        blank = (n - i) if new_carry is None else 0
        carried = new_carry['lines'] if new_carry else 0
        last_page = k_next >= len(self.order)
        change = (COST_CHANGE[0] * abs(f - prev[0]) + COST_CHANGE[1] * abs(dtrack - prev[1])) if prev else 0.0
        cost = (vis + change + COST_DELAY * delayed + COST_BLANK * blank + COST_BREAK * breaks
                + COST_CARRY * max(0, carried - self.orig_carry.get(pi, 0))
                + (COST_OVERFLOW * carried if last_page else 0))
        stats = {'size': size, 'track': track, 'size_factor': f, 'track_delta_em': dtrack, 'used': i, 'slots': n,
                 'blank': blank, 'delayed': delayed, 'breaks': breaks, 'relaxed': relaxed, 'cost': cost}
        return cost, placed, new_carry, started, stats

    def choose(self, pi, carry, pending, lookahead, prev=None):
        """The best fit for page ``pi``: (total cost, simulate result).  With ``lookahead`` a fit is judged by its
        own cost plus the best cost of the next page after it, so a page does not take a line the next page
        needs (or leave it one too many)."""
        k = self.order.index(pi)
        nxt = self.order[k + 1] if lookahead and k + 1 < len(self.order) else None
        best = None
        cands = self.cands
        adj = self.settings['pages'].get(pi) or self.settings['pages'].get(str(pi))
        if adj:                                             # a human fixed this page's fit
            cands = [(float(adj.get('size_factor', 1.0)), float(adj.get('track_delta_em', 0.0)), 0)]
        for ci, cand in enumerate(cands):
            res = self.simulate(pi, cand, carry, pending, prev)
            total = res[0]
            if nxt is not None:
                total += self.choose(nxt, res[2], [u for u in pending if u not in res[3]], False, cand[:2])[0]
            if best is None or total < best[0]:
                best = (total, res)
            # every later candidate costs at least its visual cost: stop when none can do better
            if ci + 1 < len(cands) and best[0] <= cands[ci + 1][2]:
                break
        return best

    def run(self, plan_pages, unit_info):
        r = self.ratios['regular']
        carry, pending, prev = None, list(self.units), None
        for pi in self.order:
            best = self.choose(pi, carry, pending, lookahead=True, prev=prev)[1]
            cost, placed, carry, started, st = best
            prev = (st['size_factor'], st['track_delta_em'])
            slots = self.slots[pi]
            for it, lines, i0 in placed:
                u = it['unit']
                for k, line in enumerate(lines):
                    s = slots[i0 + k]
                    plan_pages[pi]['lines'].append(_plan_line(line, u, s['block'], 'paragraph', 'regular', st['size'],
                                                              st['track'], s['x1'], s['top'] + r['asc'] * st['size']))
                info = unit_info.setdefault(u, {'unit': u, 'kind': 'body', 'lines': 0, 'size_px': round(st['size'], 2),
                                                'tracking_px': round(st['track'], 2),
                                                'tracking_em': round(st['track'] / st['size'], 3),
                                                'lines_original': sum(b['line_count'] for _, b in self.chains[u]),
                                                'hyphenated_lines': 0, 'relaxed_spacing': False,
                                                'starts_on_page': self.doc['pages'][pi]['page'],
                                                'original_page': self.doc['pages'][self.start[u]]['page']})
                info['lines'] += len(lines)
                info['hyphenated_lines'] += sum(l['hyphen'] for l in lines)
            pending = [u for u in pending if u not in started]
            plan_pages[pi]['body'] = {'slots': st['slots'], 'used': st['used'], 'blank': st['blank'],
                                      'size_px': round(st['size'], 2), 'tracking_em': round(st['track'] / st['size'], 3),
                                      'size_factor': st['size_factor'], 'track_delta_em': st['track_delta_em'],
                                      'delayed': st['delayed'], 'breaks': st['breaks']}
            page_no = self.doc['pages'][pi]['page']
            if st['delayed']:
                self.report['warnings'].append('page %d: %d paragraph(s) that started here in the original start on a later page'
                                               % (page_no, st['delayed']))
            if st['blank'] > 1:
                self.report['warnings'].append('page %d: %d empty body line(s) at the foot' % (page_no, st['blank']))
        # whatever is left: below the last frame of the last body page
        if (carry or pending) and self.order:
            pi = self.order[-1]; slot = self.slots[pi][-1]
            size = plan_pages[pi]['body']['size_px']; track = plan_pages[pi]['body']['tracking_em'] * size
            items = ([carry] if carry else []) + [{'unit': u, 'text': self.tr[u]['pl'].strip(), 'fresh': True} for u in pending]
            k = 0
            for it in items:
                lines, _ = self._typeset(it['text'], size, track, (round(slot['x2'] - slot['x1'], 2),),
                                         self.indent if it['fresh'] else 0.0)
                for line in lines:
                    k += 1
                    plan_pages[pi]['lines'].append(_plan_line(line, it['unit'], slot['block'], 'paragraph', 'regular', size,
                                                              track, slot['x1'], slot['top'] + k * slot['pitch'] + r['asc'] * size,
                                                              overflow=True))
                info = unit_info.setdefault(it['unit'], {'unit': it['unit'], 'kind': 'body', 'lines': 0, 'size_px': round(size, 2),
                                                         'tracking_px': round(track, 2), 'tracking_em': round(track / size, 3),
                                                         'lines_original': sum(b['line_count'] for _, b in self.chains[it['unit']]),
                                                         'hyphenated_lines': 0, 'relaxed_spacing': False,
                                                         'starts_on_page': self.doc['pages'][pi]['page'],
                                                         'original_page': self.doc['pages'][self.start[it['unit']]]['page']})
                info['lines'] += len(lines)
            self.report['warnings'].append('body text needs %d more lines than the pages hold: overflow below the last frame' % k)


def _word(e):
    w = [e[0], round(e[1] / M, 3)]
    if len(e) > 2:
        meta = dict(e[2])
        if 'track' in meta:
            meta['track'] = round(meta['track'] / M, 4)
        w.append(meta)
    return w


def _plan_line(line, unit, block, role, style, size, track, x, baseline, upper=False, overflow=False):
    d = {'unit': unit, 'block': block, 'role': role, 'style': style, 'size': round(size, 3), 'track': round(track, 4),
         'x': round(x + line['indent'] / M, 2), 'baseline': round(baseline, 2), 'width': round(line['width'] / M, 2),
         'gap': round(line['gap'] / M, 3), 'hyphen': bool(line['hyphen']),
         'stretch': round(line['gap'] / line['space'], 3) if line.get('space') else 1.0,
         'words': [_word(e) for e in line_words(line)]}
    if upper:
        d['upper'] = True
    if overflow:
        d['overflow'] = True
    return d


# ---------------------------------------------------------------- fixed frames
def _fixed_unit(doc, uid, frames, text, fonts, ratios, settings, report, plan_pages):
    first = frames[0][1]
    adj = settings['blocks'].get(first['block_id'], {})
    tracking, size_scale, word_space = settings['tracking'], settings['size_scale'], settings['word_space']
    if 'tracking_em' in adj:
        tracking = float(adj['tracking_em'])
    size_scale *= float(adj.get('size_scale', 1.0))
    word_space *= float(adj.get('word_space', 1.0))
    up = FIT_UP if tracking is None else 1.15
    style, upper = block_style(first)
    if first['role'] == 'author_byline_candidate':
        style = 'italic'
    if upper:
        text = upper_keep_tags(text)
    t0 = first['typography']
    size0, s_h, s_w = calibrated_size(first, fonts, style, upper, ratios.get(style, ratios['regular']), up)
    if upper:
        size0 = float(min(max(s_w, 0.7 * s_h), 1.15 * s_h))
    size0 *= size_scale
    col = doc['pages'][frames[0][0]]['layout']['columns']
    col = col[0] if col else None
    anchor = adj.get('anchor') or anchor_of(first, col)
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
    if 'max_width' in adj:                            # a human set the widest the text may be
        mw = float(adj['max_width'])
        Ws = sorted({W0 * min(k, mw) for k in (1.0, 1.06, 1.12, 1.2, 1.3)} | {W0 * mw})
    chosen = None
    for shrink in (1.0, 0.98, 0.96, 0.94, 0.92, 0.9, 0.86, 0.82):
        size = size0 * shrink
        font = fonts.get(style, size * M)
        track0 = 0.0
        if tracking is not None:
            track0 = tracking * size
        elif not upper:                               # fit the letter-spacing to the original line widths
            track0 = fit_track([first], font, M, size)
        else:                                         # letter-space capitals up to the original line widths
            gaps = []
            for ln in first['lines']:
                n = len(ln['text'].strip())
                if n > 3:
                    nat = font.getlength(ln['text'].strip()) / M
                    gaps.append(((ln['bbox'][2] - ln['bbox'][0]) - nat) / (n - 1))
            track0 = float(min(max(np.median(gaps) if gaps else 0.0, 0.0), 0.25 * size))
        for tf in ((1.0, 0.5, 0.0) if track0 > 0 else (1.0,)):
            track = track0 * tf
            for Wc in Ws:
                lines, relaxed = typeset(text, font, Wc * M, 0.0, False, space_factor=word_space, track=track * M,
                                         fonts_for=lambda st, sz=size: fonts.get(st, sz * M), style=style)
                if len(lines) <= capacity and max(l['width'] for l in lines) / M <= Wc + 0.5:
                    chosen = (shrink, Wc, lines, relaxed, size, track); break
            if chosen:
                break
        if chosen:
            break
    if not chosen:
        shrink, Wc = 0.82, Ws[-1]; size = size0 * shrink
        font = fonts.get(style, size * M)
        lines, relaxed = typeset(text, font, Wc * M, 0.0, False, space_factor=word_space, track=0.0,
                                 fonts_for=lambda st, sz=size: fonts.get(st, sz * M), style=style)
        chosen = (shrink, Wc, lines, relaxed, size, 0.0)
        report['warnings'].append('%s: needs %d lines, frame holds %d (overflow)' % (uid, len(lines), capacity))
    shrink, Wc, lines, relaxed, size, track = chosen
    pitch = t0.get('line_pitch_px') or size * 1.5
    r = ratios.get(style, ratios['regular'])
    asc = (r['cap'] if upper else r['asc']) * size
    li = 0
    for fi, (pi, b) in enumerate(frames):
        x1, x2 = b['bbox'][0], b['bbox'][2]
        tops = line_tops(b)
        n_here = b['line_count'] if fi < len(frames) - 1 else len(lines) - li
        for k in range(n_here):
            if li >= len(lines):
                break
            line = lines[li]
            width = line['width'] / M
            x = x2 - width if anchor == 'right' else ((x1 + x2) / 2 - width / 2 if anchor == 'center' else x1)
            top = tops[k] if k < len(tops) else tops[-1] + (k - len(tops) + 1) * pitch
            plan_pages[pi]['lines'].append(_plan_line(line, uid, b['block_id'], first['role'], style, size, track, x,
                                                      top + asc, upper=upper, overflow=k >= b['line_count']))
            li += 1
    return {'unit': uid, 'kind': first['role'], 'lines': len(lines), 'capacity': capacity,
            'size_px': round(size, 2), 'tracking_px': round(track, 2), 'tracking_em': round(track / size, 3),
            'lines_original': capacity, 'shrink': shrink, **({'adjusted': sorted(adj)} if adj else {})}


# ---------------------------------------------------------------- checks
LOOSE = 1.6                      # a justified line whose word space is stretched beyond this is "loose"
TIGHT = 0.8                      # ... squeezed below this is "tight"


def line_box(line, ratios, core=False):
    """Ink box of a plan line (working px).  ``core``: only the x-height band, for line-against-line tests
    (ascenders and descenders of neighbouring lines may interleave)."""
    r = ratios[line['style']]
    asc = (r['cap'] if line.get('upper') else r['asc']) * line['size']
    x1, x2 = line['x'], line['x'] + line['width']
    if core:
        return (x1, line['baseline'] - 0.5 * asc, x2, line['baseline'])
    return (x1, line['baseline'] - asc, x2, line['baseline'] + r['desc'] * line['size'])


def _hit(a, b, tol=1.0):
    return min(a[2], b[2]) - max(a[0], b[0]) > tol and min(a[3], b[3]) - max(a[1], b[1]) > tol


def check_plan(doc, plan):
    """Quality checks on a plan: ``[{page, kind, unit, block, with, detail}]`` and a count per kind per page.

    Kinds: ``collision`` (a line over a figure, a graphic, kept text, a line of another block, or off the page),
    ``overflow`` (lines outside their frame / after the last page), ``drift`` (a paragraph starts on a later page
    than in the original), ``blank`` (empty body slots at a page foot), ``loose`` / ``tight`` (justified word space
    beyond 1.6x / below 0.8x natural)."""
    ratios = plan['ratios']
    out = []
    for pi, (page, pp) in enumerate(zip(doc['pages'], plan['pages'])):
        no = page['page']
        blocks = {b['block_id']: b for b in page['blocks']}
        W = page.get('width') or 1e9
        H = page.get('height') or 1e9
        obstacles = [(b['block_id'], b['bbox']) for b in page['blocks'] if b['kind'] in ('figure', 'graphic')]
        obstacles += [(bid, blocks[bid]['bbox']) for bid in pp.get('keep', []) if bid in blocks]
        lines = pp['lines']
        boxes = [line_box(l, ratios) for l in lines]
        cores = [line_box(l, ratios, core=True) for l in lines]
        seen = set()
        for k, (ln, box) in enumerate(zip(lines, boxes)):
            for oid, ob in obstacles:
                if _hit(box, ob) and (ln['block'], oid) not in seen:
                    seen.add((ln['block'], oid))
                    out.append({'page': no, 'kind': 'collision', 'unit': ln['unit'], 'block': ln['block'], 'with': oid,
                                'detail': 'line at y %.0f over %s' % (ln['baseline'], oid)})
            if box[0] < -1 or box[2] > W + 1 or box[1] < -1 or box[3] > H + 1:
                out.append({'page': no, 'kind': 'collision', 'unit': ln['unit'], 'block': ln['block'], 'with': 'page',
                            'detail': 'line at y %.0f outside the page' % ln['baseline']})
            for j in range(k):
                o = lines[j]
                if o['block'] != ln['block'] and _hit(cores[k], cores[j]) and (ln['block'], o['block']) not in seen:
                    seen.add((ln['block'], o['block'])); seen.add((o['block'], ln['block']))
                    out.append({'page': no, 'kind': 'collision', 'unit': ln['unit'], 'block': ln['block'], 'with': o['block'],
                                'detail': 'lines of %s and %s overlap at y %.0f' % (ln['block'], o['block'], ln['baseline'])})
            if ln.get('overflow'):
                out.append({'page': no, 'kind': 'overflow', 'unit': ln['unit'], 'block': ln['block'], 'with': None,
                            'detail': 'line at y %.0f outside its frame' % ln['baseline']})
            if len(ln['words']) > 1 and ln.get('stretch', 1.0) > LOOSE:
                out.append({'page': no, 'kind': 'loose', 'unit': ln['unit'], 'block': ln['block'], 'with': None,
                            'detail': 'word space %.1fx natural at y %.0f' % (ln['stretch'], ln['baseline'])})
            elif len(ln['words']) > 1 and ln.get('stretch', 1.0) < TIGHT:
                out.append({'page': no, 'kind': 'tight', 'unit': ln['unit'], 'block': ln['block'], 'with': None,
                            'detail': 'word space %.2fx natural at y %.0f' % (ln['stretch'], ln['baseline'])})
        if pp.get('body') and pp['body']['blank']:
            out.append({'page': no, 'kind': 'blank', 'unit': None, 'block': None, 'with': None,
                        'detail': '%d empty body line(s) at the foot' % pp['body']['blank']})
    for u in plan['report']['units']:
        if u.get('kind') == 'body' and u.get('starts_on_page') != u.get('original_page'):
            out.append({'page': u['starts_on_page'], 'kind': 'drift', 'unit': u['unit'], 'block': None, 'with': None,
                        'detail': '%s starts on page %s, in the original on page %s' % (u['unit'], u['starts_on_page'],
                                                                                       u['original_page'])})
    summary = {}
    for c in out:
        summary.setdefault(str(c['page']), {}).setdefault(c['kind'], 0)
        summary[str(c['page'])][c['kind']] += 1
    return out, summary


# ---------------------------------------------------------------- the plan
def _inside(box, outer, tol=2):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return outer[0] - tol <= cx <= outer[2] + tol and outer[1] - tol <= cy <= outer[3] + tol


def make_plan(doc, trans, fonts, settings=None):
    """The layout plan of the whole book (see the module docstring)."""
    st = dict(DEFAULTS); st.update({k: v for k, v in (settings or {}).items() if k in DEFAULTS})
    tr = trans['units']
    ratios = {s: fonts.ratios(s) for s in ('regular', 'bold', 'italic')}
    report = {'units': [], 'pages': [], 'warnings': []}
    plan = {'schema': SCHEMA, 'settings': st, 'fonts': dict(fonts.paths), 'ratios': ratios,
            'pages': [{'index': pi, 'page': p['page'], 'side': p.get('side', ''), 'lines': [], 'keep': [], 'body': None}
                      for pi, p in enumerate(doc['pages'])],
            'report': report}

    chains = {}                                   # unit -> [(page index, block)] in document order
    for pi, page in enumerate(doc['pages']):
        for b in page['blocks']:
            if b['kind'] == 'text' and b.get('unit_id'):
                chains.setdefault(b['unit_id'], []).append((pi, b))

    for pi, page in enumerate(doc['pages']):        # translated text blocks: wiped on a scan background
        plan['pages'][pi]['wipe'] = [b['block_id'] for b in page['blocks']
                                     if b['kind'] == 'text' and b.get('unit_id') in tr]

    def keep(frames, why):
        if why:
            report['warnings'].append(why)
        for pi, b in frames:
            plan['pages'][pi]['keep'].append(b['block_id'])

    # text without a unit (not translatable): original pixels, unless a figure already carries them
    for pi, page in enumerate(doc['pages']):
        figs = [b['crop_bbox'] for b in page['blocks'] if b['kind'] == 'figure']
        for b in page['blocks']:
            if b['kind'] == 'text' and not b.get('unit_id') and not any(_inside(b['bbox'], f) for f in figs):
                keep([(pi, b)], None)

    for u, fr in list(chains.items()):               # a human chose to keep the original of a block: its whole unit
        if any(st['blocks'].get(b['block_id'], {}).get('keep_original') for _, b in fr):
            keep(fr, None)
            report.setdefault('kept_by_hand', []).append(u)
            del chains[u]
    body_units = [u for u, fr in chains.items() if all(b['role'] == 'paragraph' for _, b in fr)]
    fixed_units = [u for u in chains if u not in body_units]
    for u in list(body_units):
        if u not in tr:
            keep(chains[u], '%s: no translation; original pixels kept' % u)
            body_units.remove(u)

    unit_info = {}
    if body_units:
        flow = _Flow(doc, tr, fonts, st, chains, body_units, ratios, report)
        flow.run(plan['pages'], unit_info)
        report['units'].extend(unit_info[u] for u in flow.units if u in unit_info)
    for uid in fixed_units:
        if uid not in tr:
            keep(chains[uid], '%s: no translation; original pixels kept' % uid)
            continue
        report['units'].append(_fixed_unit(doc, uid, chains[uid], tr[uid]['pl'].strip(), fonts, ratios, st, report,
                                           plan['pages']))
    checks, summary = check_plan(doc, plan)
    report['checks'], report['check_summary'] = checks, summary
    for c in checks:
        if c['kind'] == 'collision':
            report['warnings'].append('page %s: %s' % (c['page'], c['detail']))
    for pg in plan['pages']:
        if pg['body']:
            b = pg['body']
            report['pages'].append({'page': pg['page'], 'body_lines_used': b['used'], 'body_lines_capacity': b['slots'],
                                    'blank': b['blank'], 'size_px': b['size_px'], 'tracking_em': b['tracking_em'],
                                    'delayed': b['delayed']})
    return plan

#!/usr/bin/env python3
"""Which unit text sits on which page and line?  (no GUI toolkit, testable alone)

The OCR correction (``ocrfix.py``) works on *units* (paragraphs: dehyphenated, possibly joined across a page
break); a human compares against a *page*.  Every correction and flag of ``ocrfix`` carries an ``offset`` into
the unit's ``text_raw``.  This module maps such a character range back to the OCR lines it came from:

1. ``replay`` rebuilds the unit text from the lines of its blocks with the very code that built it
   (``stage4.join_pair`` / ``ends_hyphen``) and records for every line the span it occupies in the unit text.
   Nothing is guessed: the unit text *is* a deterministic function of the line texts.
2. The result is checked against the stored text.  A unit whose replay differs (a different pipeline version,
   a hand-edited file) is marked ``aligned = False``; its marks are then only assigned to the unit's first page
   and carry no line positions.
3. ``UnitMap.locate`` turns a character range of the unit text into fragments ``(block, line, c0, c1)`` in the
   *raw* line text.  A word split by a hyphen at a line end (``Spitz-`` / ``bogen``) yields two fragments.

``DocIndex`` builds on that: page rows for the list, and three JSON views of a page (see docs/GUI.md):
``page_model`` (the document.json entry), ``page_review`` (what a human edits) and ``page_marks``.
"""
import sys
from collections import namedtuple
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from stage4 import ends_hyphen, join_pair   # noqa: E402  (same code that built the unit text)
import ocr_decisions                       # noqa: E402

# [start, end) of a line's stripped text in the unit text; lead = characters stripped from the line start.
Span = namedtuple('Span', 'block line start end lead')
# A character range [c0, c1) in the raw text of one OCR line.
Frag = namedtuple('Frag', 'block line c0 c1')

REVIEW_SCHEMA = '1.0'
# Mark classes: what the GUI draws.  flag severity high+medium -> 'uncertain', low -> 'watch'.
CLASSES = {
    'auto': 'corrected automatically by ocrfix.py',
    'human': 'decided by a human (review resolution)',
    'uncertain': 'flagged: no or weak candidate, a strong candidate for checking (severity high or medium)',
    'watch': 'flagged, low severity: probably a name or a rare word, or a known word that looks like a slip',
}


# ---------------------------------------------------------------- replay of stage 4
def _join(a, aspans, b, bspans):
    """Stage 4's ``text + ' ' + b`` / ``join_pair`` with the spans of both sides.  -> (text, spans)."""
    if ends_hyphen(a):
        text, rule = join_pair(a, b)
        a_s = a.rstrip()
        if rule == 'removed_hyphen':
            keep = off = len(a_s) - 1
        elif rule == 'kept_hyphen_uppercase':
            keep = off = len(a_s)
        else:                                   # suspended_compound: hyphen kept, a space inserted
            keep, off = len(a_s), len(a_s) + 1
    else:
        text = a + ' ' + b
        keep, off = len(a), len(a) + 1
    spans = [s._replace(end=min(s.end, keep)) for s in aspans if s.start < keep]
    spans += [s._replace(start=s.start + off, end=s.end + off) for s in bspans]
    return text, [s for s in spans if s.end > s.start]


def replay(parts):
    """``parts``: ``[(block_id, [raw line text, ...]), ...]`` in unit order -> (unit text, [Span]).

    Mirrors ``stage4.dehyphenate`` (lines inside a block) and ``stage4.build_units`` (blocks of one unit)."""
    full, fspans = '', []
    for k, (bid, lines) in enumerate(parts):
        text, spans = '', []
        for i, raw in enumerate(lines):
            t = raw.strip()
            ls = [Span(bid, i, 0, len(t), len(raw) - len(raw.lstrip()))]
            if not text:
                text, spans = t, ls
            else:
                text, spans = _join(text, spans, t, ls)
        if k == 0:
            full, fspans = text, spans
        else:
            full, fspans = _join(full, fspans, text, spans)
    return full, fspans


class UnitMap:
    def __init__(self, unit_id, text, spans, aligned):
        self.unit_id, self.text, self.spans, self.aligned = unit_id, text, spans, aligned

    def locate(self, start, end):
        """Character range of the unit text -> [Frag]; empty when the unit is not aligned."""
        if not self.aligned:
            return []
        out = []
        for s in self.spans:
            lo, hi = max(start, s.start), min(end, s.end)
            if lo < hi:
                out.append(Frag(s.block, s.line, lo - s.start + s.lead, hi - s.start + s.lead))
        return out


def line_words_ok(line):
    """True when the line carries word boxes (schema 1.1) that spell exactly the line text."""
    ws = line.get('words')
    return bool(ws) and ' '.join(w['text'] for w in ws) == line.get('text')


def line_box(line, c0, c1):
    """Box (page coordinates) of the characters ``[c0, c1)`` of a line's raw text -> ``(bbox, exact)``.

    ``exact``: the box is the union of the Tesseract word boxes the range touches (stage 2 stores them since
    schema 1.1).  Otherwise (older documents) it is an estimate: the line box cut proportionally to the
    character position, which is good to a few characters for a proportional font."""
    n = max(1, len(line.get('text', '')))
    if line_words_ok(line):
        hit, pos = [], 0
        for w in line['words']:
            s, e = pos, pos + len(w['text'])
            pos = e + 1
            if (c0 < e and c1 > s) or (c0 == c1 and s <= c0 <= e):
                hit.append(w['bbox'])
        if hit:
            return [min(b[0] for b in hit), min(b[1] for b in hit), max(b[2] for b in hit), max(b[3] for b in hit)], True
    x1, y1, x2, y2 = line['bbox']
    w = (x2 - x1) / n
    c0, c1 = max(0, min(c0, n)), max(0, min(c1, n))
    if c1 <= c0:
        c1 = min(n, c0 + 1)
        c0 = max(0, c1 - 1)
    return [int(round(x1 + c0 * w)), y1, int(round(x1 + c1 * w)), y2], False


def mark_class(kind, severity=None):
    if kind == 'auto':
        return 'auto'
    if kind == 'human':
        return 'human'
    return 'watch' if severity == 'low' else 'uncertain'


def _mark(kind, uid, um, pages, lo, hi, **kw):
    """One mark.  All marks have the same keys (a consumer never special-cases the kind); keys starting with
    ``_`` are internal and removed by ``DocIndex.page_marks``."""
    m = {'kind': kind, 'cls': mark_class(kind, kw.get('severity')), 'unit': uid, 'token': None, 'after': None,
         'rule': None, 'cost': None, 'key': None, 'reason': None, 'severity': None, 'candidates': [], 'llm': None, 'decision': None,
         'offset': lo, 'length': hi - lo, 'aligned': um.aligned, '_frags': um.locate(lo, hi), '_pages': pages}
    m.update(kw)
    return m


# ---------------------------------------------------------------- the index
class DocIndex:
    """Index of a ``document(.corrected).json`` and the matching ``ocr_review.json``."""

    def __init__(self, doc, review=None, base_dir=None, decisions=None):
        self.doc = doc
        self.decisions = decisions or ocr_decisions.empty()
        self.review = review or {}
        self.base_dir = Path(base_dir) if base_dir else None
        self.pages = doc.get('pages', [])
        self.page_by_no = {p['page']: p for p in self.pages}
        self.block = {}                        # block id -> (page number, block)
        for p in self.pages:
            for b in p.get('blocks', []):
                self.block[b['block_id']] = (p['page'], b)
        self.units = {u['unit_id']: u for u in doc.get('units', [])}
        self._maps = {}
        self._marks = None

    # ---- units
    def unit_pages(self, uid):
        u = self.units[uid]
        return sorted({self.block[b][0] for b in u.get('source_block_ids', []) if b in self.block})

    def unit_map(self, uid):
        if uid in self._maps:
            return self._maps[uid]
        u = self.units[uid]
        parts, missing = [], False
        for bid in u.get('source_block_ids', []):
            ent = self.block.get(bid)
            if ent is None:
                missing = True
                break
            parts.append((bid, [ln['text'] for ln in ent[1].get('lines', [])]))
        if missing:
            m = UnitMap(uid, '', [], False)
        else:
            text, spans = replay(parts)
            m = UnitMap(uid, text, spans, text == u.get('text_raw', u.get('text')))
        self._maps[uid] = m
        return m

    def set_decisions(self, decisions):
        """The human decisions about flags changed (``ocr_decisions``): flags they cover count as decided."""
        self.decisions = decisions
        self._marks = None

    def refresh(self):
        """Forget what was derived from unit texts or flags (after ``unit_edits.apply_edits`` changed the document)."""
        self._marks = None

    # ---- marks
    def _all_marks(self):
        """Every automatic correction and open flag as a dict with its line fragments (``where``)."""
        if self._marks is not None:
            return self._marks
        marks = []
        flags_by_unit = {}
        for f in self.review.get('flags', []):
            flags_by_unit.setdefault(f.get('unit'), []).append(f)
        for uid, u in self.units.items():
            um = self.unit_map(uid)
            pages = self.unit_pages(uid)
            for c in u.get('ocr_corrections', []):
                before, off = c.get('before', ''), c['offset']
                # abbrev_space inserts a blank after "B.": its offset is the end of the token it follows
                lo, hi = (off - len(before), off) if c.get('rule') == 'abbrev_space' else (off, off + len(before))
                kind = 'human' if c.get('rule') == 'human' else 'auto'
                marks.append(_mark(kind, uid, um, pages, lo, hi, token=before, after=c.get('after'),
                                   rule=c.get('rule'), cost=c.get('cost')))
            for f in flags_by_unit.get(uid, []):
                lo, hi = f['offset'], f['offset'] + len(f.get('token', ''))
                # a human read the whole paragraph and saved a text: its open flags count as reviewed (docs/GUI.md, section 6)
                decision = ocr_decisions.status(self.decisions, f.get('key'), f.get('token'))
                cls = {'cls': 'human'} if (u.get('ocr_edited') or decision) else {}      # decided: shown green until the next run
                marks.append(_mark('flag', uid, um, pages, lo, hi, token=f.get('token'), key=f.get('key'), decision=decision, **cls,
                                   reason=f.get('reason'), severity=f.get('severity'), llm=f.get('llm'),
                                   candidates=[c.get('text') for c in f.get('candidates', [])]))
        self._marks = marks
        return marks

    def page_marks(self, n):
        """The marks that touch page ``n`` (a copy per page; fragments restricted to that page).  A mark of a unit
        that could not be aligned is listed on the unit's first page, without positions."""
        out = []
        for m in self._all_marks():
            if m['aligned']:
                where = []
                for f in m['_frags']:
                    if self.block[f.block][0] != n:
                        continue
                    box, exact = line_box(self.block[f.block][1]['lines'][f.line], f.c0, f.c1)
                    where.append({'block': f.block, 'line': f.line, 'c0': f.c0, 'c1': f.c1, 'bbox': box, 'exact': exact})
                if not where:
                    continue
            else:
                if not m['_pages'] or m['_pages'][0] != n:
                    continue
                where = []
            d = {k: v for k, v in m.items() if not k.startswith('_')}
            d['where'] = where
            out.append(d)
        return out

    # ---- the list
    def page_rows(self):
        """One dict per page for the list: counts of blocks, figures and marks per class."""
        rows = []
        for p in self.pages:
            n = p['page']
            blocks = p.get('blocks', [])
            cnt = {'auto': 0, 'human': 0, 'uncertain': 0, 'watch': 0}
            for m in self.page_marks(n):
                cnt[m['cls']] += 1
            units = {b['unit_id'] for b in blocks if b.get('unit_id')}
            rows.append({'page': n, 'side': p.get('side', ''), 'source': p.get('source_tiff') or Path(self.doc.get('source_file', '')).name,
                         'text_blocks': sum(b['kind'] == 'text' for b in blocks), 'figures': sum(b['kind'] == 'figure' for b in blocks),
                         'units': len(units), 'unaligned': sum(1 for u in units if not self.unit_map(u).aligned), **cnt})
        return rows

    def image_path(self, n):
        rel = self.page_by_no[n].get('image')
        return (self.base_dir / rel) if (self.base_dir and rel) else (Path(rel) if rel else None)

    # ---- JSON views of one page
    def page_model(self, n):
        """The page exactly as stored in document.json (geometry, typography, blocks, lines)."""
        return self.page_by_no[n]

    def page_review(self, n):
        """The editable view of a page: its units with ``text_raw`` (OCR, read-only) and ``text`` (the corrected
        text that a human may change).  A unit that continues on another page appears on both pages with its
        whole text; ``blocks`` lists the blocks that are on *this* page."""
        page = self.page_by_no[n]
        seen, units = set(), []
        for b in sorted((b for b in page.get('blocks', []) if b['kind'] == 'text' and b.get('unit_id')),
                        key=lambda b: b.get('reading_order', 0)):
            uid = b['unit_id']
            if uid in seen:
                continue
            seen.add(uid)
            u = self.units[uid]
            pages = self.unit_pages(uid)
            units.append({
                'unit_id': uid, 'role': b.get('role'),
                'blocks': [bid for bid in u.get('source_block_ids', []) if self.block.get(bid, (None,))[0] == n],
                'pages': pages,
                'continues_from_previous_page': pages[0] < n, 'continues_on_next_page': pages[-1] > n,
                'text_raw': u.get('text_raw', u['text']), 'text': u['text'], 'edited': bool(u.get('ocr_edited')),
            })
        return {'schema': REVIEW_SCHEMA, 'page': n, 'side': page.get('side'), 'source_tiff': page.get('source_tiff'),
                'image': page.get('image'), 'units': units}

    def page_marks_json(self, n):
        return {'schema': REVIEW_SCHEMA, 'page': n, 'classes': CLASSES, 'marks': self.page_marks(n)}

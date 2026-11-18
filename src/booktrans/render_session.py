#!/usr/bin/env python3
"""The render preview of a project (no GUI toolkit, testable alone).

``RenderSession`` is what the GUI's render window works with: the book's ``document.json``, the project's translation,
render settings (``project.json`` -> ``render``) and human adjustments (``review/render_overrides.json``).  It keeps
the layout plan of the whole book (``layout.make_plan``) and draws any page from it at any scale
(``render.draw_page``), finds the block under a point, and stores adjustments and settings.  Layout and drawing only
need numpy, Pillow and the fonts, so they run in the GUI process (in a worker thread); the full-resolution render of
the book is a subprocess (``project.render_command``).
"""
import json
import threading
import time
from pathlib import Path

import layout as L
import render as R
import render_overrides as RO
from project import ProjectError, _font_path, check_render
from typeset import Fonts

CHECK_KINDS = ('collision', 'overflow', 'drift', 'blank', 'loose', 'tight')
FIXED_KEYS = ('size_scale', 'tracking_em', 'word_space', 'anchor', 'max_width')


def settings_for_layout(r):
    """``project.json`` render settings -> ``layout.make_plan`` settings."""
    tr = str(r.get('tracking', 'auto')).strip()
    ind = str(r.get('paragraph_indent', '')).strip()
    return {'tracking': None if tr in ('', 'auto') else float(tr), 'size_scale': float(r.get('size_scale', 1.0)),
            'word_space': float(r.get('word_space', 1.0)), 'paragraph_indent_em': float(ind) if ind else None,
            'page_fit': bool(r.get('page_fit', True))}


class RenderSession:
    def __init__(self, project):
        self.project = project
        self.doc = self.trans = self.plan = None
        self.overrides = RO.empty()
        self.stale = []
        self.ink, self.ink_info = None, {}
        self._fonts, self._fonts_key, self._ink_key = None, None, None
        self.timings = {}
        self.lock = threading.RLock()          # layout and drawing run in worker threads: one at a time

    # ------------------------------------------------------------ loading
    def load(self):
        """Read the document, the translation and the adjustments.  Raises ProjectError when one is missing."""
        with self.lock:
            return self._load()

    def _load(self):
        p = self.project
        doc_file = p.book_dir() / 'document.json'
        if not doc_file.exists():
            raise ProjectError('no document.json yet in %s: run the pipeline first' % p.book_dir())
        if not p.translations_file.exists():
            raise ProjectError('no translation yet (%s): run the translation first' % p.translations_file)
        self.doc = json.loads(doc_file.read_text(encoding='utf-8'))
        self.doc['_dir'] = str(doc_file.parent)
        self.trans = json.loads(p.translations_file.read_text(encoding='utf-8'))
        if not isinstance(self.trans.get('units'), dict):
            raise ProjectError('%s has no "units"' % p.translations_file)
        try:
            self.overrides = RO.load(p.overrides_file)
        except ValueError as e:
            raise ProjectError(str(e))
        self.plan = None
        return self

    # ------------------------------------------------------------ fonts, ink, layout
    @property
    def settings(self):
        return self.project.render_settings

    def fonts(self):
        r = self.settings
        specs = tuple(str(_font_path(str(r.get(k) or '').strip(), self.project.root)) if str(r.get(k) or '').strip() else None
                      for k in ('font', 'bold_font', 'italic_font'))
        if specs != self._fonts_key or self._fonts is None:
            self._fonts = Fonts(*specs)
            self._fonts_key = specs
        return self._fonts

    def _ink(self, fonts):
        spec = str(self.settings.get('ink', 'auto')).strip()
        if spec != 'auto':
            self.ink_info = {}
            return tuple(int(v) for v in spec.split(','))
        key = tuple(sorted(fonts.paths.items()))
        if key != self._ink_key or self.ink is None:
            from typecolour import match_ink
            ink, self.ink_info = match_ink(self.doc, fonts)
            self.ink = tuple(ink or R.measure_ink(self.doc) or R.INK)
            self._ink_key = key
        return self.ink

    def relayout(self):
        """Lay out the whole book with the current settings and adjustments -> the plan (also kept in ``plan``)."""
        with self.lock:
            return self._relayout()

    def _relayout(self):
        t0 = time.time()
        fonts = self.fonts()
        st = settings_for_layout(self.settings)
        st['blocks'], st['pages'], self.stale = RO.resolve(self.doc, self.overrides)
        plan = L.make_plan(self.doc, self.trans, fonts, st)
        t1 = time.time()
        plan['ink'] = list(self._ink(fonts))
        plan['report']['ink'] = plan['ink']
        self.timings = {'layout': t1 - t0, 'ink': time.time() - t1}
        self.plan = plan
        return plan

    def draw(self, pi, scale=None):
        """(rebuilt, original) images of page index ``pi`` at ``scale`` output px per working px (None: working size)."""
        with self.lock:
            if self.plan is None:
                self._relayout()
            r = self.settings
            return R.draw_page(self.doc, self.plan, pi, self.fonts(), r.get('background', 'paper'), tuple(self.plan['ink']),
                               'work', scale or 1.0)

    # ------------------------------------------------------------ what the window shows
    def page_count(self):
        return len(self.doc['pages']) if self.doc else 0

    def page_label(self, pi):
        p = self.doc['pages'][pi]
        return '%s%s' % (p['page'], (' ' + p['side']) if p.get('side') else '')

    def checks_for(self, pi):
        no = self.doc['pages'][pi]['page']
        return [c for c in (self.plan or {}).get('report', {}).get('checks', []) if c['page'] == no]

    def page_rows(self):
        """One row per page: page, side, body lines used / slots, size factor, letter-spacing change, check counts."""
        rows = []
        for pi, page in enumerate(self.doc['pages']):
            body = self.plan['pages'][pi].get('body') if self.plan else None
            counts = {k: 0 for k in CHECK_KINDS}
            for c in self.checks_for(pi):
                counts[c['kind']] = counts.get(c['kind'], 0) + 1
            rows.append({'index': pi, 'page': page['page'], 'side': page.get('side', ''),
                         'lines': '%d/%d' % (body['used'], body['slots']) if body else '',
                         'size': body['size_factor'] if body else None, 'track': body['track_delta_em'] if body else None,
                         'adjusted': self.page_adjustment(pi) != {}, 'checks': counts})
        return rows

    def block_at(self, pi, x, y):
        """The text block (id) under the working-pixel point (x, y) of page ``pi``: the smallest one containing it."""
        hits = [b for b in self.doc['pages'][pi]['blocks']
                if b['kind'] == 'text' and b['bbox'][0] - 3 <= x <= b['bbox'][2] + 3 and b['bbox'][1] - 3 <= y <= b['bbox'][3] + 3]
        if not hits:
            return None
        return min(hits, key=lambda b: (b['bbox'][2] - b['bbox'][0]) * (b['bbox'][3] - b['bbox'][1]))['block_id']

    def block(self, pi, bid):
        return next((b for b in self.doc['pages'][pi]['blocks'] if b['block_id'] == bid), None)

    def block_info(self, pi, bid):
        """What the window shows about a block: role, unit, how it is set, its adjustment, the Polish text."""
        b = self.block(pi, bid)
        if b is None:
            return None
        pp = self.plan['pages'][pi] if self.plan else {}
        uid = b.get('unit_id')
        unit = next((u for u in (self.plan or {}).get('report', {}).get('units', []) if u['unit'] == uid), None)
        body = unit is not None and unit.get('kind') == 'body'
        state = 'kept (original pixels)' if bid in pp.get('keep', []) else \
            'dropped (scan noise)' if bid in pp.get('dropped', []) else \
            'body text' if body else ('set in its frame' if unit else 'not translated')
        return {'block': bid, 'role': b.get('role'), 'unit': uid, 'state': state, 'body': body, 'report': unit,
                'adjustment': self.block_adjustment(bid),
                'text': (self.trans['units'].get(uid) or {}).get('pl', '') if uid else '',
                'lines_here': sum(1 for ln in pp.get('lines', []) if ln.get('block') == bid)}

    # ------------------------------------------------------------ adjustments
    def block_adjustment(self, bid):
        return {k: v for k, v in self.overrides['blocks'].get(bid, {}).items() if k != 'base'}

    def page_adjustment(self, pi):
        key = RO.page_key(self.doc['pages'][pi])
        return {k: v for k, v in self.overrides['pages'].get(key, {}).items() if k != 'base'}

    def set_block(self, pi, bid, **adj):
        """Set adjustments of a block (None removes a key) and save ``review/render_overrides.json``.  Raises ValueError
        for a bad value; nothing is changed then."""
        trial = json.loads(json.dumps(self.overrides))
        RO.set_block(trial, self.block(pi, bid), **adj)
        RO.save(self.project.overrides_file, trial)
        self.overrides = trial

    def clear_block(self, pi, bid):
        self.set_block(pi, bid, **{k: None for k in self.block_adjustment(bid)})

    def set_page(self, pi, **adj):
        trial = json.loads(json.dumps(self.overrides))
        RO.set_page(trial, self.doc['pages'][pi], **adj)
        RO.save(self.project.overrides_file, trial)
        self.overrides = trial

    def clear_page(self, pi):
        self.set_page(pi, **{k: None for k in self.page_adjustment(pi)})

    # ------------------------------------------------------------ book settings
    def save_settings(self, new):
        """Validate and store book render settings in ``project.json``.  Raises ProjectError (nothing changed)."""
        r = dict(self.settings)
        r.update(new)
        check_render(r, self.project.root)
        self.project.render_settings.update(r)
        self.project.save()
        self._fonts = None                            # fonts or ink may have changed: rebuilt on the next layout
        self.plan = None


def output_files(project):
    """The files of the last full render (for the window's "show" button): (folder, [page PNGs], pdf or None)."""
    d = Path(project.render_dir)
    if not d.exists():
        return d, [], None
    pdf = sorted(d.glob('*.pdf'))
    return d, sorted(d.glob('page_*.png')), (pdf[0] if pdf else None)

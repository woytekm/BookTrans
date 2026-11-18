"""The OCR correction window: one separate window per page.

The page image (the deskewed page the pipeline made from the TIFF; the TIFF itself is never touched) is shown fitted
to the window with every mark of the page drawn on it in the colour of its class: blue = corrected automatically,
red = uncertain, amber = watch, green = decided by a human.  Hovering over a mark shows what it is.  Positions are
exact (Tesseract word boxes) for documents of schema 1.1, and estimated for older ones; estimated marks are
drawn as outlines only.

Below the image is the *review JSON* of the page: the units (paragraphs) with ``text_raw`` (what the OCR read) and
``text`` (the corrected text).  Only ``text`` may be edited; *Save* checks the JSON, stores the changed texts as
edits (``unit_edits.py``, ``review/unit_edits.json``) and re-marks the page.  Clicking a mark puts the cursor on its word
in the text.  Prev / Next move through the pages in the same window.
"""
import json
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw

import caption_tools
import ocr_decisions
import unit_edits
from gui_widgets import (CANVAS_BG, CLASS_COLORS, CLASS_LABELS, FIND_CUR_BG, MAC, MOD, MOD_LABEL, MUTED, NOTE_FG, SEL_BG,
                         FIND_BG, SEL_FG, STATUS_COLORS, TEXT_BG, TEXT_FG, SafePhoto, mono_font, release_pending_images)

ZOOM_STEP, ZOOM_MIN, ZOOM_MAX = 1.25, 0.1, 6.0       # a click on + / - multiplies by this; limits of the scale
PAD = 2                      # px around a mark box (page pixels, before scaling)


def _rgb(hexcol):
    return tuple(int(hexcol[i:i + 2], 16) for i in (1, 3, 5))


def compose_marks(img, size, marks, show=None):
    """The page image resized to ``size`` with the marks drawn on it.  -> ``(RGB image, hits)``.

    ``marks``: ``DocIndex.page_marks`` entries; a mark is drawn once per fragment of ``where`` (a word split
    over two lines gets two boxes).  ``show``: set of classes to draw (default all).  ``hits``: a list of
    ``(x1, y1, x2, y2, mark)`` in the coordinates of the returned image, for hover tests.  The page image and the
    boxes are in the same pixels (``DocIndex`` boxes are page coordinates); ``img`` may be larger or smaller."""
    sx, sy = size[0] / img.width, size[1] / img.height
    base = img.resize(size, Image.LANCZOS).convert('RGBA')
    over = Image.new('RGBA', size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    hits = []
    order = {'auto': 0, 'human': 1, 'watch': 2, 'uncertain': 3}          # the ones to check end up on top
    for m in sorted(marks, key=lambda m: order.get(m['cls'], 0)):
        if show is not None and m['cls'] not in show:
            continue
        r, g, b = _rgb(CLASS_COLORS[m['cls']])
        for w in m['where']:
            x1, y1, x2, y2 = w['bbox']
            box = [int((x1 - PAD) * sx), int((y1 - PAD) * sy), int((x2 + PAD) * sx), int((y2 + PAD) * sy)]
            fill = 70 if w['exact'] else 0                                # an estimated position: outline only
            d.rectangle(box, fill=(r, g, b, fill), outline=(r, g, b, 235), width=2)
            hits.append((box[0], box[1], box[2], box[3], m))
    return Image.alpha_composite(base, over).convert('RGB'), hits


def describe_mark(m):
    """One line for the hover text."""
    what = CLASS_LABELS.get(m['cls'], m['cls'])
    if m['kind'] in ('auto', 'human'):
        s = '%s:  %s  \u2192  %s' % (what, m['token'], m['after'])
        if m.get('rule'):
            s += '   [%s]' % m['rule']
    else:
        s = '%s:  %s' % (what, m['token'])
        if m.get('reason'):
            s += '   (%s)' % m['reason']
        if m.get('candidates'):
            s += '   candidates: %s' % ', '.join(str(c) for c in m['candidates'][:4])
        if m.get('decision'):
            d = m['decision']
            s += '   \u2192 you decided: ' + ('accepted as it is' if d == 'accepted' else 'the word is accepted everywhere' if d == 'word accepted'
                                           else 'replace with %s' % d.split(':', 1)[1])
    return s


class CorrectionWindow(tk.Toplevel):
    def __init__(self, app, page_no):
        super().__init__(app)
        self.app, self.page_no = app, page_no
        self.geometry('860x1020')
        self.minsize(460, 520)
        self._photo = None
        self._img = None
        self._marks, self._hits, self._origin = [], [], (0, 0)
        self.zoom = None                                     # None: the page fitted to the window; else display px per page px
        self._scale = 1.0                                    # the scale in use
        self._orig = None                                    # the review JSON (dict) the editor was filled from
        self._press = None
        self.show = {c: tk.BooleanVar(value=True) for c in CLASS_COLORS}

        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill='x')
        self.prev_btn = ttk.Button(top, text='◀ Prev', width=7, command=lambda: self.go(-1))
        self.prev_btn.pack(side='left')
        self.next_btn = ttk.Button(top, text='Next ▶', width=7, command=lambda: self.go(1))
        self.next_btn.pack(side='left', padx=(4, 10))
        self.head = ttk.Label(top, text='', font=('TkDefaultFont', 12, 'bold'))
        self.head.pack(side='left')
        self.counts = ttk.Label(top, text='')
        self.counts.pack(side='right')
        bar = ttk.Frame(self)
        bar.pack(fill='x', padx=10)
        for c in ('auto', 'uncertain', 'watch', 'human'):          # the legend doubles as the show/hide switches
            tk.Label(bar, text='■', fg=CLASS_COLORS[c], font=('TkDefaultFont', 14)).pack(side='left')
            ttk.Checkbutton(bar, text=c, variable=self.show[c], command=self._draw).pack(side='left', padx=(0, 10))
        self.note = ttk.Label(bar, text='', foreground=NOTE_FG)
        self.note.pack(side='right')
        self.accept_btn = ttk.Menubutton(bar, text='Accept as is \u25be')
        am = tk.Menu(self.accept_btn, tearoff=False)
        am.add_command(label='all watch words on this page', command=lambda: self.accept_page(('watch',)))
        am.add_command(label='all uncertain and watch words on this page', command=lambda: self.accept_page(('uncertain', 'watch')))
        self.accept_btn.configure(menu=am)
        self.accept_btn.pack(side='right', padx=(0, 10))
        self.caption_btn = ttk.Button(bar, text='Captions…', command=self.open_captions)
        self.caption_btn.pack(side='right', padx=(0, 10))
        zb = ttk.Frame(self)
        zb.pack(fill='x', padx=10, pady=(6, 0))
        ttk.Button(zb, text='−', width=3, command=lambda: self.zoom_by(1 / ZOOM_STEP)).pack(side='left')
        ttk.Button(zb, text='+', width=3, command=lambda: self.zoom_by(ZOOM_STEP)).pack(side='left', padx=4)
        ttk.Button(zb, text='Fit', width=4, command=self.zoom_fit).pack(side='left')
        ttk.Button(zb, text='100%', width=5, command=lambda: self.set_zoom(1.0)).pack(side='left', padx=4)
        self.zoom_lbl = ttk.Label(zb, text='', foreground=MUTED)
        self.zoom_lbl.pack(side='left', padx=8)
        ttk.Label(zb, text='%s + wheel: zoom   drag: move   click a mark: find it in the text' % MOD_LABEL,
                  foreground=MUTED).pack(side='right')

        self.paned = ttk.PanedWindow(self, orient='vertical')
        self.paned.pack(fill='both', expand=True, padx=10, pady=8)
        imgf = ttk.Frame(self.paned)
        self.paned.add(imgf, weight=3)
        box = ttk.Frame(imgf)
        box.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(box, bg=CANVAS_BG, highlightthickness=0)
        ys = ttk.Scrollbar(box, orient='vertical', command=self.canvas.yview)
        xs = ttk.Scrollbar(box, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        xs.grid(row=1, column=0, sticky='ew')
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.info = ttk.Label(imgf, text='Hover over a mark.', foreground=MUTED, padding=(0, 4, 0, 0), wraplength=800, justify='left')
        self.info.pack(fill='x')

        ef = ttk.Frame(self.paned)
        self.paned.add(ef, weight=2)
        eb = ttk.Frame(ef)
        eb.pack(fill='x', pady=(0, 4))
        ttk.Label(eb, text='Review JSON: edit only the "text" values, compare with the page above',
                  font=('TkDefaultFont', 11, 'bold')).pack(side='left')
        self.save_btn = ttk.Button(eb, text='Save', command=self.save)
        self.save_btn.pack(side='right')
        self.revert_btn = ttk.Button(eb, text='Revert', command=self.revert)
        self.revert_btn.pack(side='right', padx=6)
        fb = ttk.Frame(ef)
        fb.pack(fill='x', pady=(0, 4))
        ttk.Label(fb, text='Find').pack(side='left', padx=(0, 4))
        self.find_var = tk.StringVar()
        self.find_entry = ttk.Entry(fb, textvariable=self.find_var, width=28)
        self.find_entry.pack(side='left')
        ttk.Button(fb, text='\u25b2', width=3, command=lambda: self.find_next(-1)).pack(side='left', padx=(6, 2))
        ttk.Button(fb, text='\u25bc', width=3, command=lambda: self.find_next(1)).pack(side='left')
        self.find_count = ttk.Label(fb, text='', foreground=MUTED)
        self.find_count.pack(side='left', padx=8)
        ttk.Label(fb, text='Enter: next   Shift+Enter: previous   %s+F: here   Esc: back to the text' % MOD_LABEL,
                  foreground=MUTED).pack(side='right')
        self._matches, self._cur = [], -1
        wrap = ttk.Frame(ef)
        wrap.pack(fill='both', expand=True)
        self.editor = tk.Text(wrap, wrap='word', undo=True, maxundo=-1, font=mono_font(), bg=TEXT_BG, fg=TEXT_FG,
                              insertbackground=TEXT_FG, selectbackground=SEL_BG, selectforeground=SEL_FG, relief='flat',
                              borderwidth=0, padx=6, pady=4, highlightthickness=0, height=8)
        ey = ttk.Scrollbar(wrap, orient='vertical', command=self.editor.yview)
        self.editor.configure(yscrollcommand=ey.set)
        self.editor.grid(row=0, column=0, sticky='nsew')
        ey.grid(row=0, column=1, sticky='ns')
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.editor.tag_configure('found', background=FIND_BG)
        self.editor.tag_configure('locate', background=FIND_CUR_BG)
        self.editor.tag_configure('current', background=FIND_CUR_BG)
        self.editor.tag_raise('sel')
        self.find_var.trace_add('write', lambda *_: self._find_update(jump=True))
        self.find_entry.bind('<Return>', lambda _e: self.find_next(1))
        self.find_entry.bind('<Shift-Return>', lambda _e: self.find_next(-1))
        self.find_entry.bind('<Escape>', lambda _e: self.editor.focus_set())
        self.bind('<%s-f>' % MOD, lambda _e: (self.find_entry.focus_set(), self.find_entry.selection_range(0, 'end'), 'break')[2])
        self.editor.bind('<<Modified>>', self._modified)
        self.status = ttk.Label(ef, text='', foreground=MUTED, wraplength=800, justify='left')
        self.status.pack(fill='x', pady=(4, 0))

        self.canvas.bind('<Configure>', lambda _e: self._draw() if self.zoom is None else self._place())
        self.canvas.bind('<Motion>', self._hover)
        self.canvas.bind('<Leave>', lambda _e: self.info.configure(text=''))
        self.canvas.bind('<ButtonPress-1>', self._press_event)
        self.canvas.bind('<B1-Motion>', lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind('<ButtonRelease-1>', self._release_event)
        for ev in (('<Button-2>', '<Control-Button-1>') if MAC else ('<Button-3>',)):      # right click: decide about a flag
            self.canvas.bind(ev, self._popup)
        self.canvas.bind('<MouseWheel>', self._wheel)                     # macOS, Windows
        self.canvas.bind('<Button-4>', lambda e: self._wheel(e, 1))       # X11
        self.canvas.bind('<Button-5>', lambda e: self._wheel(e, -1))
        # the keys must not fire while typing in the editor: they are bound to the canvas side only
        for key, fn in (('plus', lambda: self.zoom_by(ZOOM_STEP)), ('equal', lambda: self.zoom_by(ZOOM_STEP)),
                        ('minus', lambda: self.zoom_by(1 / ZOOM_STEP)), ('0', self.zoom_fit), ('1', lambda: self.set_zoom(1.0))):
            self.canvas.bind('<%s>' % key, lambda _e, fn=fn: fn())
        self.canvas.bind('<KP_Add>', lambda _e: self.zoom_by(ZOOM_STEP))
        self.canvas.bind('<KP_Subtract>', lambda _e: self.zoom_by(1 / ZOOM_STEP))
        self.canvas.bind('<Enter>', lambda _e: self.canvas.focus_set())
        self.bind('<Prior>', lambda _e: self.go(-1))                      # PageUp / PageDown: previous / next page
        self.bind('<Next>', lambda _e: self.go(1))
        self.bind('<%s-s>' % MOD, lambda _e: (self.save(), 'break')[1])
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.load()

    # ------------------------------------------------------------------ loading and pages
    @property
    def dirty(self):
        return bool(self.editor.edit_modified())

    def _pages(self):
        ix = self.app.index
        return sorted(ix.page_by_no) if ix else []

    def load(self, keep_editor=False):
        """(Re)read the page from the main window's index.  ``keep_editor``: leave what is being typed in the editor
        alone (another window saved something) when it has unsaved changes."""
        ix = self.app.index
        n = self.page_no
        page = ix.page_by_no.get(n) if ix else None
        if page is None:
            self.head.configure(text='Page %d: not in the document any more' % n)
            self.counts.configure(text='')
            self._img, self._marks, self._orig = None, [], None
            self._set_editor(None)
            self._nav_state()
            self._draw()
            return
        self.title('OCR correction – page %d – %s' % (n, self.app.project.name))
        self.head.configure(text='Page %d (%s)  ·  %s' % (n, page.get('side', ''), page.get('source_tiff') or ''))
        row = next(r for r in self.app.rows if r['page'] == n)
        self.counts.configure(text='auto %d   uncertain %d   watch %d   human %d' % (row['auto'], row['uncertain'], row['watch'], row['human']))
        self._marks = ix.page_marks(n)
        drawn = [m for m in self._marks if m['where']]
        estimated = sum(1 for m in drawn if not all(w['exact'] for w in m['where']))
        notes = []
        if len(drawn) < len(self._marks):
            notes.append('%d without a position' % (len(self._marks) - len(drawn)))
        if estimated:
            notes.append('%d positions estimated (outlines)' % estimated)
        self.note.configure(text='; '.join(notes))
        self._orig = ix.page_review(n)
        if not (keep_editor and self.dirty):
            self._set_editor(self._orig)
        path = ix.image_path(n)
        try:
            self._img = Image.open(path).convert('RGB')
            self._error = ''
        except (OSError, AttributeError) as e:
            self._img, self._error = None, 'Cannot open the page image:\n%s\n%s' % (path, e)
        self._nav_state()
        self._draw()

    def _nav_state(self):
        pages = self._pages()
        i = pages.index(self.page_no) if self.page_no in pages else -1
        self.prev_btn.configure(state='normal' if i > 0 else 'disabled')
        self.next_btn.configure(state='normal' if 0 <= i < len(pages) - 1 else 'disabled')

    def go(self, delta):
        """Show the previous (-1) / next (+1) page in this window."""
        pages = self._pages()
        if self.page_no not in pages:
            return
        i = pages.index(self.page_no) + delta
        if not 0 <= i < len(pages):
            return
        n = pages[i]
        other = self.app.windows.get(n)
        if other is not None and other is not self:             # that page already has its window: show that one
            other.deiconify()
            other.lift()
            other.focus_force()
            return
        if not self._confirm_leave():
            return
        self.app.windows.pop(self.page_no, None)
        self.page_no = n
        self.app.windows[n] = self
        self.app.select_page(n)
        self._set_editor(None)
        self.load()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    # ------------------------------------------------------------------ the editor
    def _set_editor(self, review):
        self.editor.configure(state='normal')
        self.editor.delete('1.0', 'end')
        if review is not None:
            self.editor.insert('1.0', json.dumps(review, ensure_ascii=False, indent=1))
        self.editor.edit_reset()
        self.editor.edit_modified(False)
        self.editor.tag_remove('locate', '1.0', 'end')
        self._find_update(jump=False)
        self.save_btn.configure(state='normal' if review is not None else 'disabled')
        self.revert_btn.configure(state='normal' if review is not None else 'disabled')
        self.status.configure(text='', foreground=MUTED)

    # ------------------------------------------------------------------ find in the editor text
    def _collect(self):
        """All matches of the search text (case-insensitive) as ``[(start, end)]``, tagged ``found``."""
        ed = self.editor
        ed.tag_remove('found', '1.0', 'end')
        self._matches = []
        needle = self.find_var.get()
        if not needle:
            return
        n = tk.IntVar(master=ed)
        pos = '1.0'
        while len(self._matches) < 5000:
            pos = ed.search(needle, pos, stopindex='end', nocase=True, count=n)
            if not pos or n.get() == 0:
                break
            end = '%s+%dc' % (pos, n.get())
            ed.tag_add('found', pos, end)
            self._matches.append((pos, end))
            pos = end

    def _find_update(self, jump):
        """The search text changed (or the editor was refilled): re-highlight; ``jump``: go to the first match at or after the cursor."""
        self.editor.tag_remove('current', '1.0', 'end')
        self._collect()
        if not self.find_var.get():
            self.find_count.configure(text='')
        elif not self._matches:
            self.find_count.configure(text='no match', foreground=NOTE_FG)
        elif jump:
            here = self.editor.index('insert')
            self._goto(next((i for i, (a, _b) in enumerate(self._matches) if self.editor.compare(a, '>=', here)), 0))
        else:
            self.find_count.configure(text='%d match%s' % (len(self._matches), '' if len(self._matches) == 1 else 'es'), foreground=MUTED)

    def find_next(self, direction=1):
        """Next (1) / previous (-1) match after the cursor, wrapping around.  Matches are collected again first: the text
        may have been edited since the last search."""
        self._collect()
        if not self._matches:
            self.find_count.configure(text='no match' if self.find_var.get() else '', foreground=NOTE_FG)
            return
        ed, here = self.editor, self.editor.index('insert')
        if direction > 0:
            i = next((k for k, (a, _b) in enumerate(self._matches) if ed.compare(a, '>', here)), 0)
        else:
            i = next((k for k in range(len(self._matches) - 1, -1, -1) if ed.compare(self._matches[k][0], '<', here)),
                     len(self._matches) - 1)
        self._goto(i)

    def _goto(self, i):
        a, b = self._matches[i]
        ed = self.editor
        ed.tag_remove('current', '1.0', 'end')
        ed.tag_add('current', a, b)
        ed.tag_remove('locate', '1.0', 'end')
        ed.mark_set('insert', a)
        ed.see(a)
        self.find_count.configure(text='%d of %d' % (i + 1, len(self._matches)), foreground=MUTED)

    def _modified(self, _e=None):
        if self.editor.edit_modified():
            self.status.configure(text='Unsaved changes.  Save: %s+S' % MOD_LABEL, foreground=NOTE_FG)

    def check(self):
        """Validate the editor text.  -> ``(changes, errors)`` (see ``unit_edits.validate_review``)."""
        if self._orig is None:
            return {}, ['no page loaded']
        return unit_edits.validate_review(self._orig, self.editor.get('1.0', 'end-1c'))

    def save(self):
        """Store the changed texts; -> True when the editor is in a saved state afterwards."""
        changes, errors = self.check()
        if errors:
            self.status.configure(text='Not saved: ' + '   '.join(errors[:3]), foreground=STATUS_COLORS['fail'])
            return False
        if not changes:
            self.editor.edit_modified(False)
            self.status.configure(text='Nothing to save: no "text" was changed.', foreground=MUTED)
            return True
        try:
            self.editor.edit_modified(False)                     # so that load(keep_editor=True) refreshes this editor
            n = self.app.commit_edits(changes)
        except (OSError, ValueError, KeyError) as e:
            self.editor.edit_modified(True)
            self.status.configure(text='Not saved: %s' % e, foreground=STATUS_COLORS['fail'])
            return False
        self.status.configure(text='Saved %d unit%s to review/unit_edits.json.' % (n, '' if n == 1 else 's'), foreground=STATUS_COLORS['ok'])
        return True

    def revert(self):
        """Throw away the unsaved changes in the editor."""
        if self._orig is not None:
            self._set_editor(self._orig)

    def _confirm_leave(self):
        if not self.dirty:
            return True
        a = messagebox.askyesnocancel('Unsaved changes', 'Page %d has unsaved changes.\n\nSave them?' % self.page_no, parent=self)
        if a is None:
            return False
        return self.save() if a else True

    def locate(self, mark):
        """Put the cursor on the word of ``mark`` in the editor text (best effort: the text may have been edited)."""
        if self._orig is None:
            return False
        text = self.editor.get('1.0', 'end-1c')
        at = text.find('"unit_id": %s' % json.dumps(mark['unit']))
        key = text.find('"text": "', at) if at >= 0 else -1
        if key < 0:
            return False
        start = key + len('"text": "')
        # the corrected text holds the corrected word; a flag's word is still the OCR word
        needle = mark['after'] if mark['kind'] in ('auto', 'human') and mark.get('after') else mark.get('token')
        if not needle:
            return False
        enc = json.dumps(needle, ensure_ascii=False)[1:-1]
        est = start + len(json.dumps(' ' * mark['offset'])) - 2
        end = text.find('",\n', start)                          # end of this unit's "text" value (the next key follows)
        end = len(text) if end < 0 else end
        pos = text.find(enc, max(start, est - 40), end)
        if pos < 0:
            pos = text.find(enc, start, end)
        if pos < 0:
            pos, enc = start, ''
        self.editor.tag_remove('locate', '1.0', 'end')
        a, b = '1.0+%dc' % pos, '1.0+%dc' % (pos + max(len(enc), 1))
        self.editor.tag_add('locate', a, b)
        self.editor.mark_set('insert', a)
        self.editor.see(a)
        return True

    # ------------------------------------------------------------------ zoom
    def _fit_scale(self):
        c = self.canvas
        w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        return min(w / self._img.width, h / self._img.height) if self._img is not None else 1.0

    def set_zoom(self, value, keep=True):
        """``value``: display pixels per page pixel (clamped); ``None``: fit the page to the window."""
        if self._img is None:
            return
        c = self.canvas
        fx = (c.xview()[0] + c.xview()[1]) / 2          # keep the middle of the visible part where it is
        fy = (c.yview()[0] + c.yview()[1]) / 2
        self.zoom = None if value is None else max(ZOOM_MIN, min(ZOOM_MAX, value))
        self._draw()
        if keep and self.zoom is not None:
            c.xview_moveto(fx - (c.xview()[1] - c.xview()[0]) / 2)
            c.yview_moveto(fy - (c.yview()[1] - c.yview()[0]) / 2)

    def zoom_by(self, factor):
        self.set_zoom(self._scale * factor)

    def zoom_fit(self):
        self.set_zoom(None)

    def _wheel(self, e, direction=None):
        if direction is None:
            direction = 1 if e.delta > 0 else -1
        if e.state & (0x4 | 0x8 if MAC else 0x4):                       # Ctrl (Ctrl or Cmd on macOS): zoom
            self.zoom_by(ZOOM_STEP if direction > 0 else 1 / ZOOM_STEP)
        else:
            self.canvas.yview_scroll(-direction * 3, 'units')

    # ------------------------------------------------------------------ drawing
    def _draw(self):
        """Render the page with its marks at the current scale and put it on the canvas."""
        c = self.canvas
        if self._img is None:
            c.delete('all')
            w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
            c.create_text(w // 2, h // 2, text=getattr(self, '_error', '') or 'No image', fill=STATUS_COLORS['fail'], justify='center')
            self.zoom_lbl.configure(text='')
            return
        self._scale = self._fit_scale() if self.zoom is None else self.zoom
        size = (max(1, int(self._img.width * self._scale)), max(1, int(self._img.height * self._scale)))
        shown = {k for k, v in self.show.items() if v.get()}
        img, self._hits = compose_marks(self._img, size, self._marks, shown)
        release_pending_images()
        self._photo = SafePhoto(img, master=self.canvas)      # explicit master: tkinter's default root may be another
        self.zoom_lbl.configure(text='%d%%%s' % (round(self._scale * 100), '  (fit)' if self.zoom is None else ''))
        self._place()

    def _place(self):
        """Position the rendered image: centred when smaller than the window, scrollable when larger."""
        c = self.canvas
        if self._img is None or self._photo is None:
            return
        iw, ih = self._photo.width(), self._photo.height()
        w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        self._origin = (max(0, (w - iw) // 2), max(0, (h - ih) // 2))
        c.delete('all')
        c.create_image(self._origin[0], self._origin[1], image=self._photo, anchor='nw')
        c.configure(scrollregion=(0, 0, max(w, iw), max(h, ih)))

    def _mark_at(self, e):
        x, y = self.canvas.canvasx(e.x) - self._origin[0], self.canvas.canvasy(e.y) - self._origin[1]
        found = [m for (x1, y1, x2, y2, m) in self._hits if x1 <= x <= x2 and y1 <= y <= y2]
        return found[-1] if found else None          # the last one is drawn on top

    # ------------------------------------------------------------------ decisions about flags
    def menu_for(self, m):
        """The actions for a mark: ``[(label, callable)]`` (``None`` is a separator)."""
        items = [('Find in the text', lambda: self.locate(m))]
        if m['kind'] != 'flag':
            return items
        items.append(None)
        if m.get('decision'):
            items.append(('Undo my decision', lambda: self.decide(m, None)))
            if m['decision'] == 'word accepted':
                items.append(("Stop accepting \u201e%s\u201d everywhere" % m['token'], lambda: self.accept_everywhere(m, False)))
            return items
        items.append(('Accept as it is', lambda: self.decide(m, ocr_decisions.ACCEPT)))
        for c in m.get('candidates', [])[:4]:
            if c and c != m['token']:
                items.append(('Replace with \u201e%s\u201d' % c, lambda c=c: self.decide(m, c)))
        items.append(('Accept \u201e%s\u201d everywhere' % m['token'], lambda: self.accept_everywhere(m, True)))
        return items

    def _popup(self, e):
        m = self._mark_at(e)
        if m is None:
            return
        menu = tk.Menu(self, tearoff=False)
        for it in self.menu_for(m):
            if it is None:
                menu.add_separator()
            else:
                menu.add_command(label=it[0], command=it[1])
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _decided(self, text):
        self.status.configure(text=text + '  Stored in review/ocr_decisions.json; the next pipeline run (Run pipeline with --resume) '
                                          'applies it to the corrected document.', foreground=STATUS_COLORS['ok'])

    def _update(self, mutate, message):
        try:
            changed = self.app.update_decisions(mutate)
        except (OSError, ValueError) as e:
            self.status.configure(text='Not saved: %s' % e, foreground=STATUS_COLORS['fail'])
            return False
        if changed:
            self._decided(message)
        return changed

    def decide(self, m, resolution):
        """Accept the flag as it is (``ocr_decisions.ACCEPT``), replace its word, or (``None``) take the decision back."""
        key = m['key']
        if resolution is None:
            return self._update(lambda d: ocr_decisions.unresolve(d, key), 'Decision taken back.')
        what = 'accepted as it is' if resolution == ocr_decisions.ACCEPT else 'to be replaced with \u201e%s\u201d' % resolution
        return self._update(lambda d: ocr_decisions.resolve(d, key, resolution), '\u201e%s\u201d %s.' % (m['token'], what))

    def accept_everywhere(self, m, on=True):
        w = m['token']
        if on:
            return self._update(lambda d: ocr_decisions.accept_word(d, w), '\u201e%s\u201d is accepted everywhere.' % w)
        return self._update(lambda d: ocr_decisions.forget_word(d, w), '\u201e%s\u201d is no longer accepted everywhere.' % w)

    def accept_page(self, classes):
        """Accept every still open flag of this page whose class is in ``classes`` as it is."""
        flags = {m['key']: m for m in self._marks if m['kind'] == 'flag' and m['cls'] in classes and not m.get('decision')}
        if not flags:
            self.status.configure(text='No open %s words on this page.' % ' or '.join(classes), foreground=MUTED)
            return False
        if 'uncertain' in classes and not messagebox.askyesno(
                'Accept uncertain words', 'Accept %d flagged words (%d uncertain) on page %d as they are?\n\n'
                'Uncertain words are the strong candidates for a mistake; accept them only after comparing with the page.' % (
                    len(flags), sum(m['cls'] == 'uncertain' for m in flags.values()), self.page_no), icon='warning', parent=self):
            return False
        def mutate(d):
            for k in flags:
                ocr_decisions.resolve(d, k, ocr_decisions.ACCEPT)
        return self._update(mutate, '%d words accepted as they are.' % len(flags))

    def _hover(self, e):
        m = self._mark_at(e)
        self.info.configure(text=describe_mark(m) if m else '')

    def _press_event(self, e):
        self._press = (e.x, e.y)
        self.canvas.focus_set()
        self.canvas.scan_mark(e.x, e.y)

    def _release_event(self, e):
        """A click (the pointer hardly moved) on a mark finds the mark in the text; a drag only moved the page."""
        if self._press is None or abs(e.x - self._press[0]) > 4 or abs(e.y - self._press[1]) > 4:
            return
        m = self._mark_at(e)
        if m is not None and not self.locate(m):
            self.status.configure(text='The word could not be found in the text (it was edited).', foreground=NOTE_FG)

    # ------------------------------------------------------------------ captions
    def open_captions(self):
        """The caption helper for this page (``CaptionDialog``)."""
        if self._img is None or self._orig is None:
            return
        caps = caption_tools.caption_units(self._orig, self.app.index.page_by_no[self.page_no])
        if not caps:
            messagebox.showinfo('Captions', 'Page %d has no captions.' % self.page_no, parent=self)
            return
        CaptionDialog(self, caps)

    def apply_caption_texts(self, changes):
        """Put caption texts (unit id -> text) into the review JSON in the editor; Save stores them."""
        try:
            new = caption_tools.apply_to_review(self.editor.get('1.0', 'end-1c'), changes)
        except ValueError as e:
            messagebox.showerror('Captions', str(e), parent=self)
            return False
        top = self.editor.yview()[0]
        self.editor.delete('1.0', 'end')
        self.editor.insert('1.0', new)
        self.editor.yview_moveto(top)
        self.editor.edit_modified(True)
        self._modified()
        self.status.configure(text='%d caption text(s) changed in the review JSON: Save to keep them.' % len(changes),
                              foreground=NOTE_FG)
        return True

    def close(self):
        if not self._confirm_leave():
            return
        self.app.correction_closed(self.page_no)
        self.destroy()

    def destroy(self):
        self._photo = None                           # freed here, in the main thread, not by a later garbage collection
        self._img = None
        super().destroy()


class CaptionDialog(tk.Toplevel):
    """Captions of one page, one at a time: the caption enlarged, its text, *Read again* (the caption strip alone,
    enlarged, as one text line), *Join with next* (for a caption the OCR cut into pieces: the next piece's unit is
    emptied) and *Apply* (the texts go into the page's review JSON; the window's Save keeps them)."""

    def __init__(self, win, caps):
        super().__init__(win)
        self.win, self.caps = win, caps
        self.texts = [c['text'] for c in caps]
        self.i = 0
        self._photo = None
        self.title('Captions – page %d' % win.page_no)
        self.geometry('820x560')
        self.transient(win)
        lst = ttk.Frame(self, padding=(10, 8))
        lst.pack(fill='x')
        self.listbox = tk.Listbox(lst, height=min(6, len(caps)), exportselection=False, font=mono_font())
        self.listbox.pack(fill='x')
        self.listbox.bind('<<ListboxSelect>>', lambda _e: self._select())
        self.canvas = tk.Canvas(self, bg=CANVAS_BG, highlightthickness=0, height=160)
        self.canvas.pack(fill='x', padx=10)
        self.raw = ttk.Label(self, text='', foreground=MUTED, wraplength=780, justify='left', padding=(10, 6, 10, 0))
        self.raw.pack(fill='x')
        ttk.Label(self, text='Caption text (as printed, German):', padding=(10, 6, 10, 2)).pack(fill='x')
        self.text = tk.Text(self, height=3, wrap='word', font=mono_font(), bg=TEXT_BG, fg=TEXT_FG,
                            insertbackground=TEXT_FG, relief='flat', padx=6, pady=4)
        self.text.pack(fill='x', padx=10)
        self.text.bind('<KeyRelease>', lambda _e: self._store())
        bb = ttk.Frame(self, padding=(10, 8))
        bb.pack(fill='x')
        ttk.Button(bb, text='Read again', command=self.read_again).pack(side='left')
        self.join_btn = ttk.Button(bb, text='Join with next', command=self.join_next)
        self.join_btn.pack(side='left', padx=6)
        ttk.Button(bb, text='OCR text', command=self.restore).pack(side='left')
        ttk.Button(bb, text='Close', command=self.destroy).pack(side='right')
        ttk.Button(bb, text='Apply', command=self.apply).pack(side='right', padx=6)
        self.status = ttk.Label(self, text='Read again: OCR of this caption alone, enlarged.  Join with next: for a '
                                           'caption cut into pieces (the next piece is emptied and not translated).',
                                foreground=MUTED, wraplength=780, justify='left', padding=(10, 0, 10, 8))
        self.status.pack(fill='x')
        self._fill_list()
        self.listbox.selection_set(0)
        self._select()

    def _fill_list(self):
        self.listbox.delete(0, 'end')
        for c, t in zip(self.caps, self.texts):
            mark = '*' if t != c['text'] else ' '
            self.listbox.insert('end', '%s %s  %s' % (mark, c['unit_id'], t or '(empty)'))

    def _select(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        self.i = sel[0]
        c = self.caps[self.i]
        img = self.win._img
        box = caption_tools.row_box(c, self.caps, img.width)
        z_img, _ = caption_tools.zoomed(img, box, width=max(200, self.canvas.winfo_width() or 780))
        if z_img.height > 300:
            z_img = z_img.resize((int(z_img.width * 300 / z_img.height), 300))
        self._photo = SafePhoto(z_img, master=self.canvas)
        self.canvas.configure(height=z_img.height)
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, image=self._photo, anchor='nw')
        self.raw.configure(text='OCR read: %s' % c['text_raw'])
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', self.texts[self.i])
        self.join_btn.configure(state='normal' if self.i < len(self.caps) - 1 else 'disabled')

    def _store(self):
        self.texts[self.i] = self.text.get('1.0', 'end-1c').strip()
        self._refresh_row()

    def _refresh_row(self):
        c, t = self.caps[self.i], self.texts[self.i]
        self.listbox.delete(self.i)
        self.listbox.insert(self.i, '%s %s  %s' % ('*' if t != c['text'] else ' ', c['unit_id'], t or '(empty)'))
        self.listbox.selection_set(self.i)

    def read_again(self):
        c = self.caps[self.i]
        self.configure(cursor='watch'); self.update_idletasks()
        try:
            text, conf = caption_tools.read_again(self.win._img, c['bbox'], self.win.app.project.params.get('lang', 'deu'))
        except Exception as e:                       # tesseract missing or failing: say so, keep the text
            self.status.configure(text='Read again failed: %s' % e, foreground=NOTE_FG)
            return
        finally:
            self.configure(cursor='')
        if not text:
            self.status.configure(text='Read again found no text.', foreground=NOTE_FG)
            return
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', text)
        self._store()
        self.status.configure(text='Read again (confidence %.0f): check it against the picture, then Apply.' % conf,
                              foreground=MUTED)

    def join_next(self):
        try:
            self.texts = caption_tools.join_texts(self.texts, self.i)
        except ValueError as e:
            self.status.configure(text=str(e), foreground=NOTE_FG)
            return
        self._fill_list()
        self.listbox.selection_set(self.i)
        self._select()
        self.status.configure(text='Joined with %s (now empty): Read again often fixes the whole line.'
                                   % self.caps[self.i + 1]['unit_id'], foreground=MUTED)

    def restore(self):
        self.texts[self.i] = self.caps[self.i]['text']
        self._select(); self._refresh_row()

    def apply(self):
        changes = {c['unit_id']: t for c, t in zip(self.caps, self.texts) if t != c['text']}
        if not changes:
            self.status.configure(text='Nothing changed.', foreground=MUTED)
            return
        if self.win.apply_caption_texts(changes):
            self.destroy()

    def destroy(self):
        self._photo = None
        super().destroy()

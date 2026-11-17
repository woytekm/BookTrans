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

import unit_edits
from gui_widgets import (CANVAS_BG, CLASS_COLORS, CLASS_LABELS, FIND_CUR_BG, MAC, MOD, MOD_LABEL, MUTED, NOTE_FG, SEL_BG,
                         SEL_FG, STATUS_COLORS, TEXT_BG, TEXT_FG, SafePhoto, mono_font, release_pending_images)

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
        self.editor.tag_configure('locate', background=FIND_CUR_BG)
        self.editor.bind('<<Modified>>', self._modified)
        self.status = ttk.Label(ef, text='', foreground=MUTED, wraplength=800, justify='left')
        self.status.pack(fill='x', pady=(4, 0))

        self.canvas.bind('<Configure>', lambda _e: self._draw() if self.zoom is None else self._place())
        self.canvas.bind('<Motion>', self._hover)
        self.canvas.bind('<Leave>', lambda _e: self.info.configure(text=''))
        self.canvas.bind('<ButtonPress-1>', self._press_event)
        self.canvas.bind('<B1-Motion>', lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind('<ButtonRelease-1>', self._release_event)
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
        self.save_btn.configure(state='normal' if review is not None else 'disabled')
        self.revert_btn.configure(state='normal' if review is not None else 'disabled')
        self.status.configure(text='', foreground=MUTED)

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

    def close(self):
        if not self._confirm_leave():
            return
        self.app.correction_closed(self.page_no)
        self.destroy()

    def destroy(self):
        self._photo = None                           # freed here, in the main thread, not by a later garbage collection
        self._img = None
        super().destroy()

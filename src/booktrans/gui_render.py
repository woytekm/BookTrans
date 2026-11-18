"""The render window: the rebuilt pages next to the scan, the layout checks, book settings and adjustments.

One window per project (Project > Render pages…).  It works on a ``render_session.RenderSession``: the layout of the
whole book is computed in the GUI process, in a worker thread (a few seconds for a book), and a page is drawn at the
zoom on screen, so the text stays sharp.  Every change is saved at once: book settings in ``project.json``
(``render``), block and page adjustments in ``review/render_overrides.json``; then the book is laid out again and the
page redrawn.  *Render book…* runs ``render.py`` as a subprocess for the full-resolution pages and the PDF
(``project.render_command``) -> ``<project>/render/``.

Layout of the window: the page list (with the check counts of every page) on the left; the page in the middle
(rebuilt, original, or both side by side; click a text block to select it); on the right three tabs: *Book*
(fonts, letter-spacing, size, word space, indent, page fit, background, ink), *Page* (the page's fit, its checks)
and *Block* (the selected block: how it is set, its adjustments, the Polish text).
"""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageColor

from gui_widgets import (BLOCK_OUTLINE, CANVAS_BG, CHECK_COLORS, MOD, MOD_LABEL, MUTED, NOTE_FG, SELECT_OUTLINE,
                         ADJUSTED_OUTLINE, SEL_BG, SEL_FG, STATUS_COLORS, TEXT_BG, TEXT_FG, SafePhoto,
                         release_pending_images, reveal_in_file_manager, run_async)
from project import BACKGROUNDS, RESOLUTIONS, ProjectError
from render_session import CHECK_KINDS, RenderSession, output_files
from typeset import default_fonts, default_fonts_file, font_spec

ZOOM_STEP, ZOOM_MIN, ZOOM_MAX = 1.25, 0.15, 4.0
GAP = 16                                             # px between original and rebuilt in the side-by-side view
VIEWS = (('both', 'Side by side'), ('rebuilt', 'Rebuilt'), ('original', 'Original'))
PAGE_COLUMNS = (('page', 'Page', 44, 'e'), ('side', '', 22, 'center'), ('lines', 'Lines', 52, 'e'),
                ('fit', 'Fit', 70, 'w'), ('checks', 'Checks', 110, 'w'))
LEFT_W, RIGHT_W = 300, 390                           # initial widths of the page list and the tabs
CHECK_SHORT = {'collision': 'hit', 'overflow': 'over', 'drift': 'drift', 'blank': 'empty', 'loose': 'loose', 'tight': 'tight'}
ANCHORS = ('', 'left', 'right', 'center')


def _num(text, what):
    t = str(text).strip().replace(',', '.')
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        raise ValueError('%s must be a number (or empty)' % what)


def fit_text(body):
    if not body:
        return ''
    parts = []
    if body['size_factor'] != 1.0:
        parts.append('size %.3g' % body['size_factor'])
    if body['track_delta_em']:
        parts.append('ls %+.2f' % body['track_delta_em'])
    return ', '.join(parts) or 'base'


def checks_text(counts):
    return ' '.join('%s %d' % (CHECK_SHORT[k], counts[k]) for k in CHECK_KINDS if counts.get(k))


class RenderWindow(tk.Toplevel):
    def __init__(self, app, project, page=None):
        super().__init__(app)
        self.app, self.project = app, project
        self.title('Render: %s' % project.name)
        self.geometry('1500x940')
        self.minsize(900, 560)
        self.session = RenderSession(project)
        self.pi = 0
        self._want_page = page                 # printed page number to open at (the selected page of the main window)
        self.view = tk.StringVar(value='both')
        self.show_blocks = tk.BooleanVar(value=False)
        self.show_checks = tk.BooleanVar(value=True)
        self.zoom = None                       # None: fit the window; else display px per working px
        self._scale = 1.0
        self._img = self._photo = None
        self._panels = []                      # [(kind, x offset in the image)]
        self._origin = (0, 0)
        self.sel = None                        # selected block id
        self._gen = 0                          # bumps with every layout / draw request: stale results are ignored
        self._busy = False
        self._redraw_job = None
        self._press = None
        self._build()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(80, self._initial_sashes)
        self.bind('<Prior>', lambda _e: self.go(-1))
        self.bind('<Next>', lambda _e: self.go(1))
        self.reload()

    # ------------------------------------------------------------------ construction
    def _build(self):
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill='x')
        self.prev_btn = ttk.Button(top, text='◀ Prev', width=7, command=lambda: self.go(-1))
        self.prev_btn.pack(side='left')
        self.next_btn = ttk.Button(top, text='Next ▶', width=7, command=lambda: self.go(1))
        self.next_btn.pack(side='left', padx=(4, 10))
        self.head = ttk.Label(top, text='', font=('TkDefaultFont', 12, 'bold'))
        self.head.pack(side='left')
        self.render_btn = ttk.Button(top, text='Render book…', command=self.render_book)
        self.render_btn.pack(side='right')
        ttk.Button(top, text='Output folder', command=self.show_output).pack(side='right', padx=6)
        ttk.Button(top, text='Reload', command=self.reload).pack(side='right')

        bar = ttk.Frame(self, padding=(10, 0, 10, 4))
        bar.pack(fill='x')
        for key, label in VIEWS:
            ttk.Radiobutton(bar, text=label, value=key, variable=self.view, command=self.redraw).pack(side='left', padx=(0, 8))
        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y', padx=8)
        ttk.Button(bar, text='−', width=3, command=lambda: self.zoom_by(1 / ZOOM_STEP)).pack(side='left')
        ttk.Button(bar, text='+', width=3, command=lambda: self.zoom_by(ZOOM_STEP)).pack(side='left', padx=4)
        ttk.Button(bar, text='Fit', width=4, command=lambda: self.set_zoom(None)).pack(side='left')
        ttk.Button(bar, text='100%', width=5, command=lambda: self.set_zoom(1.0)).pack(side='left', padx=4)
        self.zoom_lbl = ttk.Label(bar, text='', foreground=MUTED, width=10)
        self.zoom_lbl.pack(side='left', padx=6)
        ttk.Checkbutton(bar, text='blocks', variable=self.show_blocks, command=self._overlay).pack(side='left', padx=(8, 0))
        ttk.Checkbutton(bar, text='checks', variable=self.show_checks, command=self._overlay).pack(side='left', padx=(8, 0))
        ttk.Label(bar, text='click: select a block   drag: move   %s + wheel: zoom   PageUp/Down: pages' % MOD_LABEL,
                  foreground=MUTED).pack(side='right')

        pan = ttk.PanedWindow(self, orient='horizontal')
        pan.pack(fill='both', expand=True, padx=10, pady=(0, 4))
        self.pan = pan
        lf = ttk.Frame(pan)
        pan.add(lf, weight=0)
        self.tree = ttk.Treeview(lf, columns=[c[0] for c in PAGE_COLUMNS], show='headings', selectmode='browse', height=20)
        for cid, label, width, anchor in PAGE_COLUMNS:
            self.tree.heading(cid, text=label)
            self.tree.column(cid, width=width, anchor=anchor, stretch=(cid == 'checks'))
        ys = ttk.Scrollbar(lf, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)
        self.tree.tag_configure('warn', foreground=STATUS_COLORS['warn'])
        self.tree.tag_configure('fail', foreground=STATUS_COLORS['fail'])
        self.tree.bind('<<TreeviewSelect>>', lambda _e: self._tree_select())

        mid = ttk.Frame(pan)
        pan.add(mid, weight=1)
        self.canvas = tk.Canvas(mid, bg=CANVAS_BG, highlightthickness=0)
        cy = ttk.Scrollbar(mid, orient='vertical', command=self.canvas.yview)
        cx = ttk.Scrollbar(mid, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=cy.set, xscrollcommand=cx.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        cy.grid(row=0, column=1, sticky='ns')
        cx.grid(row=1, column=0, sticky='ew')
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        c = self.canvas
        c.bind('<Configure>', lambda _e: self._schedule_redraw() if self.zoom is None else self._place())
        c.bind('<ButtonPress-1>', self._press_event)
        c.bind('<B1-Motion>', lambda e: c.scan_dragto(e.x, e.y, gain=1))
        c.bind('<ButtonRelease-1>', self._release_event)
        c.bind('<MouseWheel>', self._wheel)
        c.bind('<Button-4>', lambda e: self._wheel(e, 1))
        c.bind('<Button-5>', lambda e: self._wheel(e, -1))
        c.bind('<Enter>', lambda _e: c.focus_set())
        for key, fn in (('plus', lambda: self.zoom_by(ZOOM_STEP)), ('equal', lambda: self.zoom_by(ZOOM_STEP)),
                        ('minus', lambda: self.zoom_by(1 / ZOOM_STEP)), ('0', lambda: self.set_zoom(None)),
                        ('1', lambda: self.set_zoom(1.0))):
            c.bind('<%s>' % key, lambda _e, fn=fn: fn())

        rf = ttk.Frame(pan, width=380)
        pan.add(rf, weight=0)
        self.nb = ttk.Notebook(rf)
        self.nb.pack(fill='both', expand=True)
        self._build_book_tab()
        self._build_page_tab()
        self._build_block_tab()

        sb = ttk.Frame(self, padding=(10, 2, 10, 6))
        sb.pack(fill='x')
        self.status_lbl = ttk.Label(sb, text='', foreground=MUTED)
        self.status_lbl.pack(side='left')

    def _initial_sashes(self):
        """The page list and the tabs at their initial widths; the page gets the rest."""
        try:
            w = self.pan.winfo_width()
            if w > LEFT_W + RIGHT_W + 200:
                self.pan.sashpos(0, LEFT_W)
                self.pan.sashpos(1, w - RIGHT_W)
        except tk.TclError:
            pass

    def _row(self, f, r, label, widget, hint='', span=1):
        ttk.Label(f, text=label).grid(row=r, column=0, sticky='w', padx=(0, 8), pady=2)
        widget.grid(row=r, column=1, columnspan=span, sticky='we', pady=2)
        if hint:
            ttk.Label(f, text=hint, foreground=MUTED).grid(row=r, column=2, sticky='w', padx=(6, 0))

    def _build_book_tab(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text='Book')
        r = self.project.render_settings
        self.bv = {k: tk.StringVar(value=str(r.get(k, ''))) for k in
                   ('font', 'bold_font', 'italic_font', 'tracking', 'size_scale', 'word_space', 'paragraph_indent',
                    'background', 'ink', 'resolution', 'output_dpi')}
        self.bv_fit = tk.BooleanVar(value=bool(r.get('page_fit', True)))
        f.columnconfigure(1, weight=1)
        row = 0
        for key, label in (('font', 'Font'), ('bold_font', 'Bold'), ('italic_font', 'Italic')):
            box = ttk.Frame(f)
            ttk.Entry(box, textvariable=self.bv[key], width=22).pack(side='left', fill='x', expand=True)
            ttk.Button(box, text='…', width=3, command=lambda k=key: self._browse_font(k)).pack(side='left', padx=(4, 0))
            self._row(f, row, label, box, span=2)
            row += 1
        try:
            d = default_fonts()
            dname = font_spec(d['regular'])[0].rsplit('/', 1)[-1]
        except ValueError as e:
            dname = 'unreadable: %s' % e
        ttk.Label(f, text='blank = the default font (%s, set in %s); bold / italic blank = the same family'
                          % (dname, default_fonts_file().name), foreground=MUTED, wraplength=330, justify='left'
                  ).grid(row=row, column=0, columnspan=3, sticky='w', pady=(0, 6))
        row += 1
        self._row(f, row, 'Letter-spacing', ttk.Entry(f, textvariable=self.bv['tracking'], width=10), 'auto or em'); row += 1
        self._row(f, row, 'Size ×', ttk.Entry(f, textvariable=self.bv['size_scale'], width=10), '0.5 … 1.5'); row += 1
        self._row(f, row, 'Word space ×', ttk.Entry(f, textvariable=self.bv['word_space'], width=10), '0.5 … 3'); row += 1
        self._row(f, row, 'Paragraph indent', ttk.Entry(f, textvariable=self.bv['paragraph_indent'], width=10),
                  'em; blank = as original'); row += 1
        ttk.Checkbutton(f, text='fit size and letter-spacing per page', variable=self.bv_fit).grid(
            row=row, column=0, columnspan=3, sticky='w', pady=2); row += 1
        self._row(f, row, 'Background', ttk.Combobox(f, textvariable=self.bv['background'], values=BACKGROUNDS,
                                                     state='readonly', width=8), 'paper | scan'); row += 1
        self._row(f, row, 'Ink', ttk.Entry(f, textvariable=self.bv['ink'], width=12), 'auto or R,G,B'); row += 1
        self._row(f, row, 'Final resolution', ttk.Combobox(f, textvariable=self.bv['resolution'], values=RESOLUTIONS,
                                                           state='readonly', width=8), 'Render book'); row += 1
        self._row(f, row, 'Output DPI', ttk.Entry(f, textvariable=self.bv['output_dpi'], width=10),
                  'final pages and PDF; 300'); row += 1
        bb = ttk.Frame(f)
        bb.grid(row=row, column=0, columnspan=3, sticky='w', pady=(10, 4)); row += 1
        self.book_apply = ttk.Button(bb, text='Apply', command=self.apply_book)
        self.book_apply.pack(side='left')
        ttk.Button(bb, text='Revert', command=self._fill_book).pack(side='left', padx=6)
        self.book_info = ttk.Label(f, text='', foreground=MUTED, wraplength=330, justify='left')
        self.book_info.grid(row=row, column=0, columnspan=3, sticky='w', pady=(6, 0))

    def _build_page_tab(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text='Page')
        f.columnconfigure(1, weight=1)
        self.page_info = ttk.Label(f, text='', wraplength=330, justify='left')
        self.page_info.grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 8))
        self.pv_size = tk.StringVar()
        self.pv_track = tk.StringVar()
        self._row(f, 1, 'Size factor', ttk.Entry(f, textvariable=self.pv_size, width=10), 'blank = automatic')
        self._row(f, 2, 'Letter-spacing ±', ttk.Entry(f, textvariable=self.pv_track, width=10), 'em; blank = automatic')
        pb = ttk.Frame(f)
        pb.grid(row=3, column=0, columnspan=3, sticky='w', pady=(8, 10))
        ttk.Button(pb, text='Apply', command=self.apply_page).pack(side='left')
        ttk.Button(pb, text='Automatic', command=self.clear_page).pack(side='left', padx=6)
        ttk.Label(f, text='Checks on this page', font=('TkDefaultFont', 11, 'bold')).grid(row=4, column=0, columnspan=3, sticky='w')
        box = ttk.Frame(f)
        box.grid(row=5, column=0, columnspan=3, sticky='nsew', pady=(4, 0))
        f.rowconfigure(5, weight=1)
        self.checks = ttk.Treeview(box, columns=('kind', 'detail'), show='headings', selectmode='browse', height=10)
        self.checks.heading('kind', text='Kind')
        self.checks.heading('detail', text='Detail')
        self.checks.column('kind', width=74, stretch=False)
        self.checks.column('detail', width=260)
        ys = ttk.Scrollbar(box, orient='vertical', command=self.checks.yview)
        self.checks.configure(yscrollcommand=ys.set)
        self.checks.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        for k, col in CHECK_COLORS.items():
            self.checks.tag_configure(k, foreground=col)
        self.checks.bind('<<TreeviewSelect>>', lambda _e: self._check_select())

    def _build_block_tab(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text='Block')
        f.columnconfigure(1, weight=1)
        self.block_info = ttk.Label(f, text='Click a text block on the page.', wraplength=330, justify='left')
        self.block_info.grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 8))
        self.kv = {k: tk.StringVar() for k in ('size_scale', 'tracking_em', 'word_space', 'anchor', 'max_width')}
        self.kv_keep = tk.BooleanVar(value=False)
        self.block_entries = []
        for r, (key, label, hint) in enumerate((('size_scale', 'Size ×', '0.5 … 1.5'),
                                                ('tracking_em', 'Letter-spacing', 'em, fixed'),
                                                ('word_space', 'Word space ×', '0.5 … 3'),
                                                ('max_width', 'Max width ×', 'of the frame, 0.5 … 2')), 1):
            e = ttk.Entry(f, textvariable=self.kv[key], width=10)
            self.block_entries.append(e)
            self._row(f, r, label, e, hint)
        cb = ttk.Combobox(f, textvariable=self.kv['anchor'], values=ANCHORS, state='readonly', width=8)
        self.block_entries.append(cb)
        self._row(f, 5, 'Alignment', cb, 'blank = as detected')
        self.keep_btn = ttk.Checkbutton(f, text='keep the original (do not set the translation)', variable=self.kv_keep)
        self.keep_btn.grid(row=6, column=0, columnspan=3, sticky='w', pady=(4, 0))
        self.block_note = ttk.Label(f, text='', foreground=NOTE_FG, wraplength=330, justify='left')
        self.block_note.grid(row=7, column=0, columnspan=3, sticky='w', pady=(4, 0))
        kb = ttk.Frame(f)
        kb.grid(row=8, column=0, columnspan=3, sticky='w', pady=(8, 8))
        self.block_apply = ttk.Button(kb, text='Apply', command=self.apply_block)
        self.block_apply.pack(side='left')
        self.block_clear = ttk.Button(kb, text='Clear', command=self.clear_block)
        self.block_clear.pack(side='left', padx=6)
        ttk.Label(f, text='Translation', font=('TkDefaultFont', 11, 'bold')).grid(row=9, column=0, columnspan=3, sticky='w')
        self.block_text = tk.Text(f, wrap='word', height=8, bg=TEXT_BG, fg=TEXT_FG, selectbackground=SEL_BG,
                                  selectforeground=SEL_FG, relief='flat', state='disabled', padx=6, pady=4)
        self.block_text.grid(row=10, column=0, columnspan=3, sticky='nsew', pady=(4, 0))
        f.rowconfigure(10, weight=1)
        self._block_state()

    # ------------------------------------------------------------------ status, loading, layout
    def status(self, text, level='info'):
        self.status_lbl.configure(text=text, foreground=STATUS_COLORS.get(level, MUTED))

    def reload(self):
        """Read the document, translation and adjustments again and lay out the book."""
        self._gen += 1
        gen = self._gen
        self.status('Loading and laying out the book…')
        self._busy = True

        def work():
            self.session.load()
            return self.session.relayout()

        def done(_res, err):
            if gen != self._gen or not self.winfo_exists():
                return
            self._busy = False
            if err is not None:
                self._error(err)
                return
            if self._want_page is not None:
                self.pi = next((i for i, p in enumerate(self.session.doc['pages']) if p['page'] == self._want_page), 0)
                self._want_page = None
            self.pi = min(self.pi, self.session.page_count() - 1)
            self._after_layout()
        run_async(self, work, done)

    def _error(self, err):
        msg = str(err) if isinstance(err, (ProjectError, ValueError, OSError)) else '%s: %s' % (type(err).__name__, err)
        self.status(msg, 'fail')
        self.canvas.delete('all')
        w, h = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        self.canvas.create_text(w // 2, h // 2, text=msg, fill=STATUS_COLORS['fail'], width=w - 40, justify='center')

    def relayout(self, message='Laying out the book…'):
        self._gen += 1
        gen = self._gen
        self.status(message)
        self._busy = True

        def done(_res, err):
            if gen != self._gen or not self.winfo_exists():
                return
            self._busy = False
            if err is not None:
                self._error(err)
                return
            self._after_layout()
        run_async(self, self.session.relayout, done)

    def _after_layout(self):
        s = self.session
        rep = s.plan['report']
        n = len(rep.get('checks', []))
        t = s.timings
        msg = 'Laid out %d pages in %.1f s' % (s.page_count(), t.get('layout', 0) + t.get('ink', 0))
        if s.stale:
            msg += '; %d adjustment(s) not applied (the OCR text changed): %s' % (len(s.stale), '; '.join(s.stale[:3]))
        self.status(msg + '; %d check(s) in the book' % n, 'warn' if s.stale else 'info')
        self._fill_tree()
        self._fill_book()
        self.show_page(self.pi)

    def _fill_tree(self):
        rows = self.session.page_rows()
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            body = self.session.plan['pages'][r['index']].get('body')
            c = r['checks']
            tag = 'fail' if c.get('collision') or c.get('overflow') else ('warn' if c.get('drift') or c.get('blank') else '')
            fit = fit_text(body) + (' *' if r['adjusted'] else '')
            self.tree.insert('', 'end', iid=str(r['index']), values=(r['page'], r['side'], r['lines'], fit,
                                                                      checks_text(c)), tags=(tag,) if tag else ())

    def _fill_book(self):
        r = self.project.render_settings
        for k, v in self.bv.items():
            v.set(str(r.get(k, '')))
        self.bv_fit.set(bool(r.get('page_fit', True)))
        s = self.session
        if s.plan is None:
            return
        f = s.fonts()
        info = ['Fonts: %s' % ', '.join('%s %s' % (k, font_spec(v)[0].rsplit('/', 1)[-1]) for k, v in f.paths.items())]
        if s.ink_info:
            ii = s.ink_info
            info.append('Ink %s: shade %.2f of the scan’s ink %s, darkness %.3f (scan %.3f)%s' % (
                ','.join(map(str, s.plan['ink'])), ii.get('k', 0), ','.join(map(str, ii.get('hue', []))),
                ii.get('darkness_set', 0), ii.get('darkness_scan', 0), ('; ' + ii['note']) if ii.get('note') else ''))
        else:
            info.append('Ink %s (fixed)' % ','.join(map(str, s.plan['ink'])))
        bodies = [p['body'] for p in s.plan['pages'] if p.get('body')]
        if bodies:
            base = bodies[0]['size_px'] / bodies[0]['size_factor']
            info.append('Body: base size %.1f px; per-page fit used on %d of %d pages' % (
                base, sum(1 for b in bodies if b['size_factor'] != 1.0 or b['track_delta_em']), len(bodies)))
        self.book_info.configure(text='\n'.join(info))

    def _browse_font(self, key):
        fn = filedialog.askopenfilename(parent=self, title='Font file', filetypes=[('Fonts', '*.ttf *.otf'), ('All files', '*')])
        if fn:
            self.bv[key].set(fn)

    # ------------------------------------------------------------------ pages
    def go(self, delta):
        if self.session.doc is None:
            return
        n = self.pi + delta
        if 0 <= n < self.session.page_count():
            self.show_page(n)

    def _tree_select(self):
        sel = self.tree.selection()
        if sel and int(sel[0]) != self.pi:
            self.show_page(int(sel[0]), from_tree=True)

    def show_page(self, pi, from_tree=False):
        s = self.session
        if s.plan is None:
            return
        if pi != self.pi:
            self.sel = None
        self.pi = pi
        if not from_tree and self.tree.exists(str(pi)):
            self.tree.selection_set(str(pi))
            self.tree.see(str(pi))
        self.prev_btn.configure(state='normal' if pi > 0 else 'disabled')
        self.next_btn.configure(state='normal' if pi < s.page_count() - 1 else 'disabled')
        page = s.doc['pages'][pi]
        self.head.configure(text='Page %s   (%d of %d)%s' % (s.page_label(pi), pi + 1, s.page_count(),
                                                          '   source %s' % page['source_tiff'] if page.get('source_tiff') else ''))
        self._fill_page_tab()
        self._fill_block_tab()
        self.redraw()

    def _fill_page_tab(self):
        s = self.session
        body = s.plan['pages'][self.pi].get('body')
        adj = s.page_adjustment(self.pi)
        if body:
            txt = ('Body text: %d of %d lines, %d empty at the foot%s.\nSize %.1f px (factor %.3g), letter-spacing %.3f em '
                   '(%+.2f).%s' % (body['used'], body['slots'], body['blank'],
                                   ', %d paragraph(s) pushed to a later page' % body['delayed'] if body['delayed'] else '',
                                   body['size_px'], body['size_factor'], body['tracking_em'], body['track_delta_em'],
                                   '\nAdjusted by hand.' if adj else '\nChosen automatically.'))
        else:
            txt = 'No body text on this page (plates, captions only).'
        self.page_info.configure(text=txt)
        self.pv_size.set('' if 'size_factor' not in adj else str(adj['size_factor']))
        self.pv_track.set('' if 'track_delta_em' not in adj else str(adj['track_delta_em']))
        self.checks.delete(*self.checks.get_children())
        for i, c in enumerate(s.checks_for(self.pi)):
            self.checks.insert('', 'end', iid=str(i), values=(c['kind'], c['detail']), tags=(c['kind'],))

    def _selected_check(self):
        sel = self.checks.selection()
        checks = self.session.checks_for(self.pi) if self.session.plan else []
        return checks[int(sel[0])] if sel and int(sel[0]) < len(checks) else None

    def _check_select(self):
        c = self._selected_check()
        if c is None:
            return
        if c.get('block'):
            self.sel = c['block']
            self._fill_block_tab()
        self._overlay()

    # ------------------------------------------------------------------ drawing
    def _work_size(self):
        p = self.session.doc['pages'][self.pi]
        if p.get('width') and p.get('height'):
            return p['width'], p['height']
        with Image.open('%s/%s' % (self.session.doc['_dir'], p['image'])) as im:
            return im.size

    def _fit_scale(self):
        w, h = self._work_size()
        n = 2 if self.view.get() == 'both' else 1
        cw, ch = max(self.canvas.winfo_width(), 100), max(self.canvas.winfo_height(), 100)
        return max(ZOOM_MIN, min((cw - 8 - GAP * (n - 1)) / (w * n), (ch - 8) / h))

    def _schedule_redraw(self, delay=120):
        if self._redraw_job is not None:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(delay, self.redraw)

    def redraw(self):
        """Draw the current page at the current zoom (in a worker thread) and show it."""
        self._redraw_job = None
        s = self.session
        if s.plan is None:
            return
        scale = self._fit_scale() if self.zoom is None else self.zoom
        view = self.view.get()
        pi = self.pi
        self._gen += 1
        gen = self._gen

        def work():
            rebuilt, orig = s.draw(pi, scale)
            if view == 'rebuilt':
                return rebuilt, [('rebuilt', 0)]
            if view == 'original':
                return orig, [('original', 0)]
            img = Image.new('RGB', (orig.width + GAP + rebuilt.width, max(orig.height, rebuilt.height)),
                            ImageColor.getrgb(CANVAS_BG))
            img.paste(orig, (0, 0))
            img.paste(rebuilt, (orig.width + GAP, 0))
            return img, [('original', 0), ('rebuilt', orig.width + GAP)]

        def done(res, err):
            if gen != self._gen or not self.winfo_exists():
                return
            if err is not None:
                self._error(err)
                return
            self._img, self._panels = res
            self._scale = scale
            release_pending_images()
            self._photo = SafePhoto(self._img, master=self.canvas)
            self.zoom_lbl.configure(text='%d%%%s' % (round(scale * 100), ' (fit)' if self.zoom is None else ''))
            self._place()
        run_async(self, work, done)

    def _place(self):
        c = self.canvas
        if self._photo is None:
            return
        iw, ih = self._photo.width(), self._photo.height()
        w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        self._origin = (max(0, (w - iw) // 2), max(0, (h - ih) // 2))
        c.delete('all')
        c.create_image(self._origin[0], self._origin[1], image=self._photo, anchor='nw', tags=('page',))
        c.configure(scrollregion=(0, 0, max(w, iw), max(h, ih)))
        self._overlay()

    def _overlay(self):
        """Outlines over the page: all text blocks (optional), blocks with checks, adjusted blocks, the selection."""
        c = self.canvas
        c.delete('overlay')
        s = self.session
        if self._photo is None or s.plan is None:
            return
        page = s.doc['pages'][self.pi]
        boxes = {b['block_id']: b['bbox'] for b in page['blocks']}
        bad = {}
        if self.show_checks.get():
            for ch in s.checks_for(self.pi):          # loose / tight lines are listed (Page tab), not outlined
                if ch.get('block') and ch['kind'] in ('collision', 'overflow'):
                    bad.setdefault(ch['block'], ch['kind'])
            if ch_sel := self._selected_check():
                if ch_sel.get('block'):
                    bad[ch_sel['block']] = ch_sel['kind']
        adjusted = {b for b in boxes if s.block_adjustment(b)}
        for kind, off in self._panels:
            for bid, box in boxes.items():
                blk = s.block(self.pi, bid)
                col, width = None, 1
                if bid == self.sel:
                    col, width = SELECT_OUTLINE, 2
                elif bid in bad:
                    col, width = CHECK_COLORS[bad[bid]], 2
                elif bid in adjusted:
                    col = ADJUSTED_OUTLINE
                elif self.show_blocks.get() and blk['kind'] == 'text':
                    col = BLOCK_OUTLINE
                if col is None:
                    continue
                x1, y1, x2, y2 = [v * self._scale for v in box]
                ox, oy = self._origin[0] + off, self._origin[1]
                c.create_rectangle(ox + x1 - 2, oy + y1 - 2, ox + x2 + 2, oy + y2 + 2, outline=col, width=width,
                                   tags=('overlay',))

    # ------------------------------------------------------------------ zoom and mouse
    def set_zoom(self, value):
        self.zoom = None if value is None else max(ZOOM_MIN, min(ZOOM_MAX, value))
        self._schedule_redraw(30)

    def zoom_by(self, factor):
        self.set_zoom(self._scale * factor)

    def _wheel(self, e, direction=None):
        if direction is None:
            direction = 1 if e.delta > 0 else -1
        if e.state & (0x4 | 0x8 if MOD == 'Command' else 0x4):
            self.zoom_by(ZOOM_STEP if direction > 0 else 1 / ZOOM_STEP)
        else:
            self.canvas.yview_scroll(-direction * 3, 'units')

    def _press_event(self, e):
        self._press = (e.x, e.y)
        self.canvas.focus_set()
        self.canvas.scan_mark(e.x, e.y)

    def _release_event(self, e):
        if self._press is None or abs(e.x - self._press[0]) > 4 or abs(e.y - self._press[1]) > 4:
            return
        pt = self.to_work(self.canvas.canvasx(e.x), self.canvas.canvasy(e.y))
        if pt is None:
            return
        bid = self.session.block_at(self.pi, *pt)
        self.select_block(bid)

    def to_work(self, x, y):
        """Canvas point -> working-pixel point of the page (None outside the page images)."""
        if self._img is None:
            return None
        x -= self._origin[0]
        y -= self._origin[1]
        for kind, off in reversed(self._panels):
            if x >= off:
                wx, wy = (x - off) / self._scale, y / self._scale
                w, h = self._work_size()
                return (wx, wy) if 0 <= wx <= w and 0 <= wy <= h else None
        return None

    # ------------------------------------------------------------------ blocks
    def select_block(self, bid):
        self.sel = bid
        self._fill_block_tab()
        self._overlay()
        if bid:
            self.nb.select(2)

    def _block_state(self, info=None):
        fixed = info is not None and info['unit'] and not info['body']
        for e in self.block_entries:
            e.configure(state=('readonly' if isinstance(e, ttk.Combobox) else 'normal') if fixed else 'disabled')
        ok = info is not None and bool(info['unit'])
        self.keep_btn.configure(state='normal' if ok else 'disabled')
        self.block_apply.configure(state='normal' if ok else 'disabled')
        self.block_clear.configure(state='normal' if ok and info['adjustment'] else 'disabled')

    def _fill_block_tab(self):
        info = self.session.block_info(self.pi, self.sel) if self.sel else None
        for v in self.kv.values():
            v.set('')
        self.kv_keep.set(False)
        self.block_note.configure(text='')
        self.block_text.configure(state='normal')
        self.block_text.delete('1.0', 'end')
        if info is None:
            self.block_info.configure(text='Click a text block on the page.')
            self.block_text.configure(state='disabled')
            self._block_state(None)
            return
        rep = info['report'] or {}
        lines = ['%s   (%s)' % (info['block'], info['role']), 'Unit %s: %s' % (info['unit'] or '–', info['state'])]
        if rep:
            lines.append('Size %.1f px, letter-spacing %.3f em; %d line(s) of %d%s' % (
                rep.get('size_px', 0), rep.get('tracking_em', 0), rep.get('lines', 0),
                rep.get('capacity', rep.get('lines_original', 0)), ' in the original' if info['body'] else ' in the frame'))
            if info['body'] and rep.get('starts_on_page') != rep.get('original_page'):
                lines.append('Starts on page %s (page %s in the original)' % (rep['starts_on_page'], rep['original_page']))
        self.block_info.configure(text='\n'.join(lines))
        adj = info['adjustment']
        for k in self.kv:
            if k in adj:
                self.kv[k].set(str(adj[k]))
        self.kv_keep.set(bool(adj.get('keep_original')))
        if info['body']:
            self.block_note.configure(text='Body text flows through the page: adjust the page fit (Page tab). Here only '
                                           '“keep the original” applies.')
        elif not info['unit']:
            self.block_note.configure(text='Not translated (no unit): its pixels are kept or dropped as scan noise.')
        self.block_text.insert('1.0', info['text'])
        self.block_text.configure(state='disabled')
        self._block_state(info)

    # ------------------------------------------------------------------ apply
    def _saved(self, what):
        self.relayout('%s saved; laying out the book…' % what)

    def apply_block(self):
        if not self.sel:
            return
        info = self.session.block_info(self.pi, self.sel)
        try:
            adj = {'keep_original': True if self.kv_keep.get() else None}
            if not info['body']:
                for k, what in (('size_scale', 'Size'), ('tracking_em', 'Letter-spacing'), ('word_space', 'Word space'),
                                ('max_width', 'Max width')):
                    adj[k] = _num(self.kv[k].get(), what)
                adj['anchor'] = self.kv['anchor'].get() or None
            self.session.set_block(self.pi, self.sel, **adj)
        except (ValueError, OSError) as e:
            messagebox.showerror('Block adjustment', str(e), parent=self)
            return
        self._saved('Block %s' % self.sel)

    def clear_block(self):
        if not self.sel:
            return
        try:
            self.session.clear_block(self.pi, self.sel)
        except (ValueError, OSError) as e:
            messagebox.showerror('Block adjustment', str(e), parent=self)
            return
        self._saved('Block %s reset' % self.sel)

    def apply_page(self):
        try:
            size = _num(self.pv_size.get(), 'Size factor')
            track = _num(self.pv_track.get(), 'Letter-spacing')
            if size is None and track is not None:
                size = 1.0
            if track is None and size is not None:
                track = 0.0
            self.session.set_page(self.pi, size_factor=size, track_delta_em=track)
        except (ValueError, OSError) as e:
            messagebox.showerror('Page adjustment', str(e), parent=self)
            return
        self._saved('Page %s' % self.session.page_label(self.pi))

    def clear_page(self):
        try:
            self.session.clear_page(self.pi)
        except (ValueError, OSError) as e:
            messagebox.showerror('Page adjustment', str(e), parent=self)
            return
        self._saved('Page %s automatic' % self.session.page_label(self.pi))

    def book_values(self):
        v = {k: var.get().strip() for k, var in self.bv.items()}
        v['page_fit'] = self.bv_fit.get()
        v['tracking'] = v['tracking'] or 'auto'
        v['ink'] = v['ink'] or 'auto'
        for k in ('size_scale', 'word_space', 'output_dpi'):   # numbers in project.json, not strings
            try:
                v[k] = float(v[k].replace(',', '.'))
            except ValueError:
                pass                                       # check_render names the field
        return v

    def apply_book(self):
        try:
            self.session.save_settings(self.book_values())
        except (ProjectError, ValueError) as e:
            messagebox.showerror('Book settings', str(e), parent=self)
            return
        self._saved('Book settings')

    # ------------------------------------------------------------------ full render
    def render_book(self):
        from gui_run import RenderRunWindow
        w = getattr(self, 'run_window', None)
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            return
        self.run_window = RenderRunWindow(self, self.project, pages=str(self.session.doc['pages'][self.pi]['page'])
                                          if self.session.doc else '')

    def show_output(self):
        d, pngs, pdf = output_files(self.project)
        if not d.exists():
            messagebox.showinfo('No output yet', 'Nothing rendered yet: use Render book… (writes %s).' % d, parent=self)
            return
        reveal_in_file_manager(d)

    def close(self):
        self._gen += 1
        if getattr(self.app, 'render_window', None) is self:
            self.app.render_window = None
        self.destroy()

    def destroy(self):
        self._photo = None
        self._img = None
        super().destroy()

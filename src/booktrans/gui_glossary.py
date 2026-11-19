"""The glossary window: the central glossary shared by all books, this project's own layer, and the candidates harvested
from the translation.  Storage and decisions are in the toolkit-free ``glossary_store`` / ``glossary_harvest``.

*Entries*: every row of every layer (the project's ``review/glossary.csv`` first, then the central files), searchable;
a row hidden by an earlier one with the same German word and kind is grey.  Add, edit, delete, *Promote to central*
(a project entry becomes a central one), *Import name list…* (``german,polish`` CSV into the central ``places.csv``),
and the central folder with *Change…* / *Show*.

*Candidates*: names and terms the translation used that the glossary lacks (``translate.py --harvest``, started with
*Collect candidates…*): accept into the central glossary or the project, edit and accept, or reject (never proposed
again).
"""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import glossary_harvest as H
import glossary_store as GS
from gui_widgets import MUTED, STATUS_COLORS, reveal_in_file_manager

COLS = ('de', 'pl', 'kind', 'status', 'district', 'variants', 'note', 'layer')
WIDTHS = {'de': 170, 'pl': 170, 'kind': 60, 'status': 80, 'district': 110, 'variants': 120, 'note': 180, 'layer': 110}
CCOLS = ('de', 'pl', 'kind', 'count', 'alternatives', 'units')
CWIDTHS = {'de': 180, 'pl': 180, 'kind': 60, 'count': 50, 'alternatives': 200, 'units': 220}


class EntryDialog(tk.Toplevel):
    """Add or edit one glossary entry.  ``on_ok(entry, layer)`` stores it and returns an error message or None."""

    def __init__(self, master, title, entry=None, layer='central', layers=('central', 'project'), on_ok=None):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.on_ok = on_ok
        e = entry or {}
        self.v = {k: tk.StringVar(value=e.get(k, '')) for k in ('de', 'pl', 'kind', 'status', 'district', 'variants', 'note', 'source')}
        self.v['kind'].set(e.get('kind') or 'term')
        self.v['status'].set(e.get('status') or 'confirmed')
        self.layer = tk.StringVar(value=layer)
        f = ttk.Frame(self, padding=12)
        f.pack(fill='both', expand=True)
        rows = [('German', ttk.Entry(f, textvariable=self.v['de'], width=40)),
                ('Polish', ttk.Entry(f, textvariable=self.v['pl'], width=40)),
                ('Kind', ttk.Combobox(f, textvariable=self.v['kind'], values=GS.KINDS, state='readonly', width=10)),
                ('Status', ttk.Combobox(f, textvariable=self.v['status'], values=GS.STATUSES, state='readonly', width=12)),
                ('District', ttk.Entry(f, textvariable=self.v['district'], width=40)),
                ('Variants', ttk.Entry(f, textvariable=self.v['variants'], width=40)),
                ('Note', ttk.Entry(f, textvariable=self.v['note'], width=40)),
                ('Source', ttk.Entry(f, textvariable=self.v['source'], width=40))]
        if len(layers) > 1:
            rows.append(('Glossary', ttk.Combobox(f, textvariable=self.layer, values=layers, state='readonly', width=10)))
        for i, (label, w) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky='w', padx=(0, 8), pady=2)
            w.grid(row=i, column=1, sticky='we', pady=2)
        ttk.Label(f, text='Polish in the nominative.  Variants: other German spellings, separated by ";".  Only confirmed '
                          'and proposed entries are given to the model.', foreground=MUTED, wraplength=380,
                  justify='left').grid(row=len(rows), column=0, columnspan=2, sticky='w', pady=(6, 0))
        self.err = ttk.Label(f, text='', foreground=STATUS_COLORS['fail'], wraplength=380, justify='left')
        self.err.grid(row=len(rows) + 1, column=0, columnspan=2, sticky='w')
        b = ttk.Frame(f)
        b.grid(row=len(rows) + 2, column=0, columnspan=2, sticky='e', pady=(10, 0))
        ttk.Button(b, text='Cancel', command=self.destroy).pack(side='right')
        ttk.Button(b, text='OK', command=self.ok).pack(side='right', padx=6)
        self.bind('<Return>', lambda _e: self.ok())
        self.bind('<Escape>', lambda _e: self.destroy())
        rows[0][1].focus_set()

    def values(self):
        return {k: v.get().strip() for k, v in self.v.items()}

    def ok(self):
        msg = self.on_ok(self.values(), self.layer.get()) if self.on_ok else None
        if msg:
            self.err.configure(text=msg)
            return
        self.destroy()


class GlossaryWindow(tk.Toplevel):
    def __init__(self, app, project):
        super().__init__(app)
        self.app, self.project = app, project
        self.title('Glossary – %s' % project.name)
        self.geometry('1180x680')
        self.minsize(760, 420)
        self.rows, self.cands = [], []
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill='x')
        ttk.Label(top, text='Central glossary (all books):').pack(side='left')
        self.dir_lbl = ttk.Label(top, text='', foreground=MUTED)
        self.dir_lbl.pack(side='left', padx=6)
        ttk.Button(top, text='Change…', command=self.change_dir).pack(side='left')
        ttk.Button(top, text='Show', command=lambda: reveal_in_file_manager(GS.ensure_central())).pack(side='left', padx=4)
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill='both', expand=True, padx=10, pady=(0, 8))
        self._build_entries()
        self._build_candidates()
        self.status = ttk.Label(self, text='', foreground=MUTED, padding=(10, 0, 10, 8))
        self.status.pack(fill='x')
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.reload()

    # ------------------------------------------------------------------ entries
    def _build_entries(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text='Entries')
        bar = ttk.Frame(f)
        bar.pack(fill='x', pady=(0, 6))
        ttk.Label(bar, text='Find').pack(side='left')
        self.find = tk.StringVar()
        ttk.Entry(bar, textvariable=self.find, width=24).pack(side='left', padx=(4, 10))
        self.find.trace_add('write', lambda *_: self._fill_entries())
        self.layer_filter = tk.StringVar(value='all')
        ttk.Combobox(bar, textvariable=self.layer_filter, values=('all', 'central', 'project'), state='readonly',
                     width=8).pack(side='left')
        self.layer_filter.trace_add('write', lambda *_: self._fill_entries())
        for text, cmd in (('Import name list…', self.import_list), ('Promote to central', self.promote),
                          ('Delete', self.delete), ('Edit…', self.edit), ('Add…', self.add)):
            ttk.Button(bar, text=text, command=cmd).pack(side='right', padx=(4, 0))
        box = ttk.Frame(f)
        box.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(box, columns=COLS, show='headings', selectmode='browse')
        for c in COLS:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=WIDTHS[c], stretch=c in ('note', 'variants'))
        ys = ttk.Scrollbar(box, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side='left', fill='both', expand=True)
        ys.pack(side='left', fill='y')
        self.tree.tag_configure('shadowed', foreground=MUTED)
        self.tree.bind('<Double-1>', lambda _e: self.edit())

    def _fill_entries(self):
        q = self.find.get().strip().lower()
        lay = self.layer_filter.get()
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rows):
            if lay != 'all' and r['_layer'] != lay:
                continue
            if q and not any(q in str(r.get(k, '')).lower() for k in ('de', 'pl', 'variants', 'note', 'district')):
                continue
            where = r['_layer'] if r['_layer'] == 'project' else 'central: ' + r['_file'].rsplit('/', 1)[-1]
            self.tree.insert('', 'end', iid=str(i), values=[r.get(c, '') for c in COLS[:-1]] + [where],
                             tags=('shadowed',) if r['_shadowed'] else ())

    def _selected(self):
        sel = self.tree.selection()
        return self.rows[int(sel[0])] if sel else None

    def _store(self, entry, layer, old=None):
        try:
            if old is not None and old['_layer'] == layer:
                GS.upsert(old['_file'], entry, old_key=GS.key_of(old))
            else:
                (GS.add_project(self.project.root, entry) if layer == 'project' else GS.add_central(entry))
                if old is not None:                            # moved to the other layer
                    GS.delete(old['_file'], GS.key_of(old))
        except (ValueError, OSError) as e:
            return str(e)
        self.reload()
        self.status.configure(text='Saved %s = %s (%s).' % (entry['de'], entry['pl'], layer))
        return None

    def add(self):
        EntryDialog(self, 'Add glossary entry', on_ok=lambda e, layer: self._store(e, layer))

    def edit(self):
        r = self._selected()
        if r is None:
            return
        EntryDialog(self, 'Edit glossary entry', entry=r, layer=r['_layer'], on_ok=lambda e, layer: self._store(e, layer, r))

    def delete(self):
        r = self._selected()
        if r is None or not messagebox.askyesno('Delete', 'Delete %s = %s from the %s glossary?' % (r['de'], r['pl'], r['_layer']),
                                                parent=self):
            return
        GS.delete(r['_file'], GS.key_of(r))
        self.reload()

    def promote(self):
        r = self._selected()
        if r is None or r['_layer'] != 'project':
            self.status.configure(text='Select an entry of this project\'s glossary to promote it to the central one.')
            return
        GS.promote(self.project.root, GS.key_of(r))
        self.reload()
        self.status.configure(text='%s is now in the central glossary (all books).' % r['de'])

    def import_list(self):
        files = filedialog.askopenfilenames(parent=self, title='German,Polish name lists (CSV)',
                                            filetypes=[('CSV', '*.csv'), ('All files', '*')])
        if not files:
            return
        try:
            added, skipped = GS.import_pairs_central(files)
        except (OSError, ValueError, UnicodeDecodeError) as e:
            messagebox.showerror('Import', str(e), parent=self)
            return
        self.reload()
        self.status.configure(text='Imported %d names into %s (proposed; %d the glossary already had were left alone).'
                                   % (added, GS.central_dir() / GS.PLACES_FILE, skipped))

    def change_dir(self):
        d = filedialog.askdirectory(parent=self, title='Central glossary folder (shared by all books)',
                                    initialdir=str(GS.central_dir()))
        if d:
            GS.set_central_dir(d)
            self.reload()

    # ------------------------------------------------------------------ candidates
    def _build_candidates(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text='Candidates')
        bar = ttk.Frame(f)
        bar.pack(fill='x', pady=(0, 6))
        ttk.Button(bar, text='Collect candidates…', command=self.collect).pack(side='left')
        self.cand_info = ttk.Label(bar, text='', foreground=MUTED)
        self.cand_info.pack(side='left', padx=8)
        for text, cmd in (('Reject', lambda: self.decide('reject')), ('Edit and accept…', self.edit_accept),
                          ('Accept → this book', lambda: self.decide('project')),
                          ('Accept → central', lambda: self.decide('central'))):
            ttk.Button(bar, text=text, command=cmd).pack(side='right', padx=(4, 0))
        box = ttk.Frame(f)
        box.pack(fill='both', expand=True)
        self.ctree = ttk.Treeview(box, columns=CCOLS, show='headings', selectmode='extended')
        for c in CCOLS:
            self.ctree.heading(c, text=c)
            self.ctree.column(c, width=CWIDTHS[c], stretch=c in ('alternatives', 'units'))
        ys = ttk.Scrollbar(box, orient='vertical', command=self.ctree.yview)
        self.ctree.configure(yscrollcommand=ys.set)
        self.ctree.pack(side='left', fill='both', expand=True)
        ys.pack(side='left', fill='y')
        self.ctree.bind('<Double-1>', lambda _e: self.edit_accept())
        ttk.Label(f, text='Names and terms the translation used that the glossary does not have yet.  Accept → central: '
                          'for every book; → this book: only here; Reject: never proposed again.  Several rows can be '
                          'selected.', foreground=MUTED, wraplength=1100, justify='left').pack(anchor='w', pady=(6, 0))

    def _fill_candidates(self):
        self.ctree.delete(*self.ctree.get_children())
        for i, c in enumerate(self.cands):
            self.ctree.insert('', 'end', iid=str(i), values=(c['de'], c['pl'], c['kind'], c.get('count', 1),
                                                            ', '.join(c.get('alternatives', [])), ', '.join(c.get('units', []))))
        self.cand_info.configure(text='%d candidate(s) in %s' % (len(self.cands), self.project.candidates_file.name)
                                 if self.cands else 'none yet: translate, then Collect candidates')

    def collect(self):
        """The translation window with "collect glossary candidates" ticked."""
        w = getattr(self.app, 'run_window', None)
        if w is not None and w.winfo_exists() and w.run is not None and w.run.running:
            messagebox.showinfo('A run is in progress', 'Wait until the running %s has finished.' % w.WHAT, parent=self)
            return
        self.app.run_translation(harvest=True)

    def _selected_cands(self):
        return [self.cands[int(i)] for i in self.ctree.selection()]

    def decide(self, action, entry=None):
        sel = self._selected_cands()
        if not sel:
            return
        try:
            for c in sel:
                H.decide(self.project.candidates_file, GS.key_of(c), action, self.project.root, entry if len(sel) == 1 else None)
        except (ValueError, OSError) as e:
            messagebox.showerror('Glossary', str(e), parent=self)
        self.reload()
        what = {'central': 'added to the central glossary', 'project': 'added to this book\'s glossary', 'reject': 'rejected'}[action]
        self.status.configure(text='%d candidate(s) %s.' % (len(sel), what))

    def edit_accept(self):
        sel = self._selected_cands()
        if len(sel) != 1:
            return
        c = sel[0]

        def ok(e, layer):
            try:
                GS.check_entry(e)
            except ValueError as err:
                return str(err)
            self.decide(layer, e)
            return None
        EntryDialog(self, 'Accept glossary candidate', entry=dict(c, status='confirmed',
                                                                  note=('also seen: ' + ', '.join(c['alternatives'])) if c.get('alternatives') else ''),
                    on_ok=ok)

    # ------------------------------------------------------------------ both
    def reload(self):
        self.dir_lbl.configure(text=str(GS.ensure_central()))
        self.rows = GS.all_entries(self.project.root)
        self.cands = H.read_candidates(self.project.candidates_file)
        self._fill_entries()
        self._fill_candidates()
        n_proj = sum(1 for r in self.rows if r['_layer'] == 'project')
        self.nb.tab(0, text='Entries (%d; this book %d)' % (len(self.rows), n_proj))
        self.nb.tab(1, text='Candidates (%d)' % len(self.cands))

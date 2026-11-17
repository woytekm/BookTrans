#!/usr/bin/env python3
"""BookTrans GUI: projects, environment check, the list of processed pages and their JSON.

    python gui.py                    # start empty: File > New project / Open project
    python gui.py PROJECT            # open a project (its folder or project.json)
    python gui.py --demo /tmp/demo   # make (or reuse) a synthetic demo project and open it

Stage A of docs/GUI.md.  The window is a viewer over the files the pipeline wrote: it never changes them.
The page list shows, per processed page, how many words ``ocrfix.py`` corrected (auto) or flagged (uncertain,
watch) and how many a human decided; the tabs show the page JSON in three views.  Double-click a page (or Return)
opens its OCR correction window.

Needs tkinter and Pillow's ImageTk (``python envcheck.py`` says what is missing).
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from PIL import ImageTk   # noqa: F401
except ImportError as e:
    sys.exit('The GUI needs tkinter and Pillow with ImageTk (%s).\n'
             '  MacPorts:  sudo port install py%d%d-tkinter\n  Pillow:    pip install Pillow\n'
             'Check everything with:  python envcheck.py' % (e, sys.version_info[0], sys.version_info[1]))

from gui_correction import CorrectionWindow          # noqa: E402
from gui_dialogs import CommandDialog, EnvWindow, NewProjectDialog   # noqa: E402
from gui_run import RunWindow                          # noqa: E402
from gui_widgets import (MAC, MOD, MOD_LABEL, MUTED, ROW_TINT, STATUS_COLORS, TREE_FG, JsonView, apply_dark_theme, Legend, copy_to_clipboard,   # noqa: E402
                         human_bytes, reveal_in_file_manager, run_async)
from pageindex import DocIndex                       # noqa: E402
from project import Project, ProjectError, write_json   # noqa: E402

RECENT_FILE = Path.home() / '.booktrans_gui.json'
# (tree column id, heading, width, anchor, key in the page row)
COLUMNS = [('page', 'Page', 58, 'e', 'page'), ('side', 'Side', 48, 'center', 'side'), ('source', 'Source', 120, 'w', 'source'),
           ('blocks', 'Blocks', 62, 'e', 'text_blocks'), ('figs', 'Figs', 50, 'e', 'figures'), ('auto', 'Auto', 54, 'e', 'auto'),
           ('uncertain', 'Uncertain', 86, 'e', 'uncertain'), ('watch', 'Watch', 62, 'e', 'watch'), ('human', 'Human', 66, 'e', 'human')]
TABS = [('model', 'Page model'), ('review', 'Review JSON'), ('marks', 'Marks')]
TAB_HELP = {
    'model': 'The page as stored in document.json: geometry, typography, blocks and OCR lines (raw OCR, never edited).',
    'review': 'What the OCR correction window will let you edit: the units on this page; change "text", keep "text_raw" as the OCR evidence.',
    'marks': 'Every automatic correction and open flag on this page, with the line and character range it was found at.',
}


class MainWindow(tk.Tk):
    def __init__(self, project_path=None):
        super().__init__()
        self.title('BookTrans')
        self.geometry('1280x780')
        self.minsize(960, 540)
        self.project = None
        self.index = None
        self.rows = []
        self.windows = {}                        # page number -> CorrectionWindow
        self._sort = ('page', False)
        self._build_style()
        self._build_menu()
        self._build_ui()
        self._menu_state()
        self.protocol('WM_DELETE_WINDOW', self.quit_app)
        if MAC:
            self.createcommand('tk::mac::Quit', self.quit_app)
        self.show_welcome()
        if project_path:
            self.after(50, lambda: self.open_project(project_path))

    # ------------------------------------------------------------------ construction
    def _build_style(self):
        apply_dark_theme(self)

    def _build_menu(self):
        m = tk.Menu(self)
        self.config(menu=m)
        f = tk.Menu(m, tearoff=False)
        m.add_cascade(label='File', menu=f)
        f.add_command(label='New project…', accelerator='%s+N' % MOD_LABEL, command=self.new_project)
        f.add_command(label='Open project…', accelerator='%s+O' % MOD_LABEL, command=self.open_dialog)
        self.recent_menu = tk.Menu(f, tearoff=False, postcommand=self._fill_recent)
        f.add_cascade(label='Open recent', menu=self.recent_menu)
        f.add_separator()
        f.add_command(label='Close project', command=self.close_project)
        if not MAC:
            f.add_separator()
            f.add_command(label='Quit', accelerator='%s+Q' % MOD_LABEL, command=self.quit_app)
        p = tk.Menu(m, tearoff=False)
        m.add_cascade(label='Project', menu=p)
        p.add_command(label='Check environment…', accelerator='%s+E' % MOD_LABEL, command=self.check_env)
        p.add_command(label='Run pipeline…', accelerator='%s+Shift+R' % MOD_LABEL, command=self.run_pipeline)
        p.add_command(label='Pipeline command…', command=self.show_command)
        p.add_command(label='Reload from disk', accelerator='%s+R' % MOD_LABEL, command=self.reload)
        p.add_command(label='Show project folder', command=lambda: self.project and reveal_in_file_manager(self.project.root))
        self.project_menu = p
        g = tk.Menu(m, tearoff=False)
        m.add_cascade(label='Page', menu=g)
        g.add_command(label='OCR correction for the selected page', accelerator='Return', command=self.open_correction)
        g.add_command(label='Find in JSON', accelerator='%s+F' % MOD_LABEL, command=lambda: (self.find_entry.focus_set(), self.find_entry.selection_range(0, 'end')))
        self.page_menu = g
        self.bind_all('<%s-R>' % MOD, lambda _e: self.run_pipeline())
        for key, fn in (('n', self.new_project), ('o', self.open_dialog), ('e', self.check_env), ('r', self.reload),
                        ('f', lambda: (self.find_entry.focus_set(), self.find_entry.selection_range(0, 'end')))):
            self.bind_all('<%s-%s>' % (MOD, key), lambda _e, fn=fn: fn())
        if not MAC:
            self.bind_all('<Control-q>', lambda _e: self.quit_app())

    def _build_ui(self):
        head = ttk.Frame(self, padding=(14, 10, 14, 6))
        head.pack(fill='x')
        left = ttk.Frame(head)
        left.pack(side='left', fill='x', expand=True)
        self.name_lbl = ttk.Label(left, text='', font=('TkDefaultFont', 16, 'bold'))
        self.name_lbl.pack(anchor='w')
        self.path_lbl = ttk.Label(left, text='', foreground=MUTED)
        self.path_lbl.pack(anchor='w')
        self.info_lbl = ttk.Label(left, text='', wraplength=900, justify='left')
        self.run_btn = ttk.Button(left, text='Run pipeline\u2026', command=self.run_pipeline)
        self.run_btn.pack(anchor='w', pady=(6, 0))
        self.info_lbl.pack(anchor='w', pady=(4, 0))
        left.bind('<Configure>', lambda e: self.info_lbl.configure(wraplength=max(300, e.width - 10)))
        Legend(head, short=True).pack(side='right', anchor='ne')
        ttk.Separator(self).pack(fill='x')

        pan = ttk.PanedWindow(self, orient='horizontal')
        pan.pack(fill='both', expand=True)
        lf = ttk.Frame(pan)
        pan.add(lf, weight=0)
        cols = [c[0] for c in COLUMNS]
        self.tree = ttk.Treeview(lf, columns=cols, show='headings', selectmode='browse')
        for cid, label, width, anchor, _k in COLUMNS:
            self.tree.heading(cid, text=label, command=lambda c=cid: self._sort_by(c))
            self.tree.column(cid, width=width, anchor=anchor, stretch=(cid == 'source'))
        ys = ttk.Scrollbar(lf, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        self.totals = ttk.Label(lf, text='', padding=(6, 4), wraplength=600, justify='left')
        self.totals.grid(row=1, column=0, columnspan=2, sticky='ew')
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)
        self.tree.tag_configure('attention', background=ROW_TINT['attention'], foreground=TREE_FG)
        self.tree.tag_configure('watch', background=ROW_TINT['watch'], foreground=TREE_FG)
        self.tree.bind('<<TreeviewSelect>>', lambda _e: self._on_select())
        self.tree.bind('<Double-1>', lambda _e: self.open_correction())
        self.tree.bind('<Return>', lambda _e: self.open_correction())

        rf = ttk.Frame(pan)
        pan.add(rf, weight=1)
        bar = ttk.Frame(rf, padding=(8, 6, 8, 2))
        bar.pack(fill='x')
        # the buttons are packed first: when the window is narrow it is the page title that gets cut, not a button
        self.open_btn = ttk.Button(bar, text='OCR correction\u2026', command=self.open_correction)
        self.open_btn.pack(side='right')
        ttk.Button(bar, text='Copy JSON', command=self.copy_json).pack(side='right', padx=6)
        self.page_lbl = ttk.Label(bar, text='', font=('TkDefaultFont', 12, 'bold'))
        self.page_lbl.pack(side='left')
        bar2 = ttk.Frame(rf, padding=(8, 2, 8, 2))
        bar2.pack(fill='x')
        ttk.Label(bar2, text='Find').pack(side='left', padx=(0, 4))
        self.find_var = tk.StringVar()
        self.find_entry = ttk.Entry(bar2, textvariable=self.find_var, width=26)
        self.find_entry.pack(side='left')
        self.find_count = ttk.Label(bar2, text='', foreground=MUTED)
        self.find_count.pack(side='left', padx=8)
        self.find_var.trace_add('write', lambda *_a: self._find(True, keep=True))
        self.find_entry.bind('<Return>', lambda _e: self._find(True))
        self.find_entry.bind('<Shift-Return>', lambda _e: self._find(False))
        self.help_lbl = ttk.Label(rf, text='', foreground=MUTED, padding=(10, 2, 10, 4), wraplength=700, justify='left')
        self.help_lbl.pack(fill='x')
        rf.bind('<Configure>', lambda e: self.help_lbl.configure(wraplength=max(200, e.width - 30)))
        self.nb = ttk.Notebook(rf)
        self.nb.pack(fill='both', expand=True, padx=6, pady=(0, 6))
        self.views = {}
        for key, label in TABS:
            v = JsonView(self.nb)
            self.views[key] = v
            self.nb.add(v, text=label)
        self.nb.bind('<<NotebookTabChanged>>', lambda _e: self._tab_changed())

        sb = ttk.Frame(self, padding=(10, 3))
        sb.pack(fill='x', side='bottom')
        self.status_lbl = ttk.Label(sb, text='', foreground=MUTED)
        self.status_lbl.pack(side='left')
        self.source_lbl = ttk.Label(sb, text='', foreground=MUTED)
        self.source_lbl.pack(side='right')

    # ------------------------------------------------------------------ small helpers
    def status(self, text, level=None):
        self.status_lbl.configure(text=text, foreground=STATUS_COLORS.get(level, MUTED))

    def _menu_state(self):
        on = 'normal' if self.project else 'disabled'
        for i in range(self.project_menu.index('end') + 1):
            if i != 0:                                # "Check environment" works without a project
                self.project_menu.entryconfigure(i, state=on)
        for i in range(self.page_menu.index('end') + 1):
            self.page_menu.entryconfigure(i, state=on)
        self.open_btn.configure(state='normal' if self.project and self.index else 'disabled')
        self.run_btn.configure(state=on)

    def _recent(self):
        try:
            return [p for p in json.loads(RECENT_FILE.read_text(encoding='utf-8')).get('recent', []) if (Path(p) / 'project.json').exists()]
        except (OSError, ValueError, AttributeError):
            return []

    def _remember(self, path):
        rec = [str(path)] + [p for p in self._recent() if p != str(path)]
        try:
            write_json(RECENT_FILE, {'recent': rec[:8]})
        except OSError:
            pass

    def _fill_recent(self):
        self.recent_menu.delete(0, 'end')
        rec = self._recent()
        for p in rec:
            self.recent_menu.add_command(label=p, command=lambda p=p: self.open_project(p))
        if not rec:
            self.recent_menu.add_command(label='(none)', state='disabled')

    # ------------------------------------------------------------------ projects
    def show_welcome(self):
        self.name_lbl.configure(text='BookTrans')
        self.path_lbl.configure(text='No project open')
        self.info_lbl.configure(text='File › New project… sets up a book (input folder, output directory, parameters) and checks the environment.\n'
                                     'File › Open project… opens an existing one; the page list then shows what the pipeline produced.')
        for v in self.views.values():
            v.set_text('')
        self.help_lbl.configure(text='')

    def new_project(self):
        d = NewProjectDialog(self, initial_dir=str(self.project.root.parent) if self.project else str(Path.home()))
        self.wait_window(d)
        if d.project:
            self.set_project(d.project)

    def open_dialog(self):
        d = filedialog.askdirectory(parent=self, title='Open project folder (the one with project.json)',
                                    initialdir=str(self.project.root.parent) if self.project else str(Path.home()))
        if d:
            self.open_project(d)

    def open_project(self, path):
        try:
            self.set_project(Project.load(path))
        except ProjectError as e:
            messagebox.showerror('Cannot open project', str(e), parent=self)

    def close_project(self):
        self._close_windows()
        self.project, self.index, self.rows = None, None, []
        self.tree.delete(*self.tree.get_children())
        self.totals.configure(text='')
        self.page_lbl.configure(text='')
        self.source_lbl.configure(text='')
        self.show_welcome()
        self.status('')
        self._menu_state()

    def set_project(self, proj):
        self._close_windows()
        self.project, self.index, self.rows = proj, None, []
        self.tree.delete(*self.tree.get_children())
        self._remember(proj.root)
        self._menu_state()
        self._header()
        self.reload()

    def _header(self):
        p = self.project
        st = p.status()
        self.name_lbl.configure(text=p.name)
        self.path_lbl.configure(text=str(p.root))
        parts = ['Input: %d TIFF file%s (%s)' % (st['tiffs'], '' if st['tiffs'] == 1 else 's', human_bytes(st['tiff_bytes']))]
        if st['processed']:
            parts.append('pipeline result: %s' % ('with OCR correction' if st['corrected'] else 'without OCR correction (document.json only)'))
        else:
            parts.append('pipeline not run yet (Project › Run pipeline…)')
        self.info_lbl.configure(text='   ·   '.join(parts))

    def reload(self):
        """Read document.json / document.corrected.json and ocr_review.json again (also after the pipeline ran)."""
        proj = self.project
        if proj is None:
            return
        keep = self._selected_page()
        self.status('Loading…')

        def work():
            if not proj.status()['processed']:
                return None
            doc, which = proj.load_document()
            ix = DocIndex(doc, proj.load_review(), proj.book_dir())
            return which, ix, ix.page_rows()

        def done(res, err):
            if proj is not self.project:         # another project was opened while this one loaded
                return
            self._header()
            if err:
                self.index, self.rows = None, []
                self._fill_tree([])
                self.status('Cannot load the result: %s' % err, 'fail')
                for v in self.views.values():
                    v.set_text('Cannot load the result:\n%s' % err)
                self._menu_state()
                return
            if res is None:
                self.index, self.rows = None, []
                self._fill_tree([])
                self.source_lbl.configure(text='')
                msg = 'No document.json yet in\n%s\n\nRun the pipeline first: Project › Run pipeline…' % proj.book_dir()
                for v in self.views.values():
                    v.set_text(msg)
                self.page_lbl.configure(text='')
                self.help_lbl.configure(text='')
                self.status('Nothing to show yet.')
                self._menu_state()
                return
            which, self.index, self.rows = res
            self.source_lbl.configure(text='showing document.%sjson' % ('corrected.' if which == 'corrected' else ''))
            self._fill_tree(self.rows, keep)
            for w in list(self.windows.values()):
                w.load()
            self._menu_state()
            self.status('%d pages loaded.' % len(self.rows))

        run_async(self, work, done)

    def _close_windows(self):
        for w in list(self.windows.values()):
            w.destroy()
        self.windows.clear()
        w = getattr(self, 'run_window', None)
        if w is not None and w.winfo_exists():
            if w.run is not None:
                w.run.stop()                       # a run belongs to the project that is being closed
            w.destroy()
        self.run_window = None

    # ------------------------------------------------------------------ the list
    def _fill_tree(self, rows, keep=None):
        self.tree.delete(*self.tree.get_children())
        col, rev = self._sort
        key = next(c[4] for c in COLUMNS if c[0] == col)
        for r in sorted(rows, key=lambda r: (r[key] if not isinstance(r[key], str) else r[key].lower()), reverse=rev):
            tag = ('attention',) if r['uncertain'] else (('watch',) if r['watch'] else ())
            self.tree.insert('', 'end', iid=str(r['page']), tags=tag, values=[r[c[4]] for c in COLUMNS])
        tot = {k: sum(r[k] for r in rows) for k in ('auto', 'uncertain', 'watch', 'human', 'unaligned')}
        self.totals.configure(text=('%d pages · corrected automatically %d · uncertain %d · watch %d · decided by a human %d' %
                                    (len(rows), tot['auto'], tot['uncertain'], tot['watch'], tot['human'])) if rows else '')
        if tot['unaligned']:
            self.totals.configure(text=self.totals.cget('text') + '\n%d units could not be traced to their lines (shown on their first page without positions).' % tot['unaligned'])
        want = str(keep) if keep and self.tree.exists(str(keep)) else (self.tree.get_children() or [None])[0]
        if want:
            self.tree.selection_set(want)
            self.tree.see(want)
        else:
            self._on_select()

    def _sort_by(self, col):
        c, rev = self._sort
        self._sort = (col, not rev if c == col else False)
        self._fill_tree(self.rows, self._selected_page())

    def _selected_page(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _on_select(self):
        n = self._selected_page()
        if n is None or self.index is None:
            self.page_lbl.configure(text='')
            return
        ix = self.index
        page = ix.page_by_no[n]
        self.page_lbl.configure(text='Page %d (%s)  ·  %s' % (n, page.get('side', ''), page.get('source_tiff') or ''))
        self.views['model'].set_json(ix.page_model(n))
        self.views['review'].set_json(ix.page_review(n))
        self.views['marks'].set_json(ix.page_marks_json(n))
        self.find_count.configure(text='')
        self._tab_changed()
        if self.find_var.get():
            self._find(True, keep=True)

    # ------------------------------------------------------------------ JSON tabs
    def _current_key(self):
        return TABS[self.nb.index(self.nb.select())][0]

    def _tab_changed(self):
        self.help_lbl.configure(text=TAB_HELP[self._current_key()] if self.index else '')

    def _find(self, forward, keep=False):
        needle = self.find_var.get()
        if not self.index:
            return
        n = self.views[self._current_key()].find(needle, forward)
        self.find_count.configure(text=('%d matches' % n if n != 1 else '1 match') if needle and n else ('no match' if needle else ''))

    def copy_json(self):
        copy_to_clipboard(self, self.views[self._current_key()].get_text())
        self.status('JSON of the %s tab copied.' % dict(TABS)[self._current_key()])

    # ------------------------------------------------------------------ other windows
    def open_correction(self):
        n = self._selected_page()
        if n is None or self.index is None:
            return
        w = self.windows.get(n)
        if w is not None:
            w.deiconify()
            w.lift()
            w.focus_force()
            return
        self.windows[n] = CorrectionWindow(self, n)

    def correction_closed(self, n):
        self.windows.pop(n, None)

    def check_env(self):
        EnvWindow(self, project=self.project)

    def run_pipeline(self):
        if not self.project:
            return
        w = getattr(self, 'run_window', None)
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            return
        self.run_window = RunWindow(self, self.project, on_done=self._pipeline_done)

    def _pipeline_done(self):
        self._header()
        self.reload()

    def show_command(self):
        if self.project:
            CommandDialog(self, self.project)

    def quit_app(self):
        self._close_windows()
        self.destroy()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('project', nargs='?', help='project folder or project.json')
    ap.add_argument('--demo', metavar='DIR', help='create a synthetic demo project in DIR (reused when it already is a project) and open it')
    ap.add_argument('--force', action='store_true', help='with --demo: replace DIR')
    a = ap.parse_args(argv)
    path = a.project
    if a.demo:
        d = Path(a.demo)
        if a.force or not (d / 'project.json').exists():
            from gui_demo import make_demo
            make_demo(d, force=a.force)
        path = d
    MainWindow(path).mainloop()


if __name__ == '__main__':
    main()

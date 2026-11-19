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
import os
import gc
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
from gui_dialogs import ClearDataDialog, CommandDialog, EnvWindow, NewProjectDialog, change_input_dir   # noqa: E402
from gui_run import RunWindow, TranslateWindow                          # noqa: E402
from gui_widgets import (MAC, MOD, MOD_LABEL, MUTED, ROW_TINT, STATUS_COLORS, TREE_FG, JsonView, apply_dark_theme, Legend, copy_to_clipboard,   # noqa: E402
                         human_bytes, reveal_in_file_manager, run_async)
import ocr_decisions                                   # noqa: E402
import unit_edits                                      # noqa: E402
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


ICON = HERE.parent.parent / 'assets' / 'booktrans_icon.png'


def _mac_app_name(name='BookTrans'):
    """macOS: the menu bar shows the bundle name, "Python" for a script.  With PyObjC installed (optional) the name is
    changed before Tk starts the application; without it nothing happens."""
    if not MAC:
        return
    try:
        from Foundation import NSBundle
        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info['CFBundleName'] = name
    except Exception:                                # no PyObjC, or a bundle that refuses: keep "Python"
        pass


def set_app_icon(root):
    """The BookTrans icon for every window (Tk 8.6 reads PNG; on macOS it is also the Dock icon).  -> the image or None."""
    try:
        img = tk.PhotoImage(master=root, file=str(ICON))
        root.iconphoto(True, img)
        root._app_icon = img                          # keep a reference: Tk forgets an image Python dropped
        return img
    except (tk.TclError, OSError):                   # no file or an old Tk: the default icon
        return None


class MainWindow(tk.Tk):
    def __init__(self, project_path=None):
        _mac_app_name()
        super().__init__()
        set_app_icon(self)
        self.title('BookTrans')
        self.geometry('1280x780')
        self.minsize(960, 540)
        self.project = None
        self.index = None
        self.rows = []
        self.windows = {}                        # page number -> CorrectionWindow
        # Cyclic garbage (a closed window with its Tk variables and images) must be freed by the main thread: finalising
        # a tkinter Variable or image in the loading thread calls Tk from that thread and hangs.  So the collector is
        # off and the main loop runs it (also when a window is closed).
        gc.disable()
        self._gc_tick()
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
        p.add_command(label='Change input folder…', command=self.change_input)
        p.add_command(label='Run pipeline…', accelerator='%s+Shift+R' % MOD_LABEL, command=self.run_pipeline)
        p.add_command(label='Re-run OCR correction…', accelerator='%s+Shift+O' % MOD_LABEL, command=self.rerun_ocrfix)
        p.add_command(label='Run translation…', accelerator='%s+Shift+T' % MOD_LABEL, command=self.run_translation)
        p.add_command(label='Render pages…', accelerator='%s+Shift+P' % MOD_LABEL, command=self.open_render)
        p.add_command(label='Glossary…', accelerator='%s+Shift+G' % MOD_LABEL, command=self.open_glossary)
        p.add_command(label='Pipeline command…', command=self.show_command)
        p.add_command(label='Reload from disk', accelerator='%s+R' % MOD_LABEL, command=self.reload)
        p.add_command(label='Show project folder', command=lambda: self.project and reveal_in_file_manager(self.project.root))
        p.add_separator()
        p.add_command(label='Clear project data…', command=self.clear_project)
        self.project_menu = p
        g = tk.Menu(m, tearoff=False)
        m.add_cascade(label='Page', menu=g)
        g.add_command(label='OCR correction for the selected page', accelerator='Return', command=self.open_correction)
        g.add_command(label='Find in JSON', accelerator='%s+F' % MOD_LABEL, command=lambda: (self.find_entry.focus_set(), self.find_entry.selection_range(0, 'end')))
        self.page_menu = g
        self.bind_all('<%s-R>' % MOD, lambda _e: self.run_pipeline())
        self.bind_all('<%s-O>' % MOD, lambda _e: self.rerun_ocrfix())
        self.bind_all('<%s-T>' % MOD, lambda _e: self.run_translation())
        self.bind_all('<%s-P>' % MOD, lambda _e: self.open_render())
        self.bind_all('<%s-G>' % MOD, lambda _e: self.open_glossary())
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
        btns = ttk.Frame(left)
        btns.pack(anchor='w', pady=(6, 0))
        self.run_btn = ttk.Button(btns, text='Run pipeline\u2026', command=self.run_pipeline)
        self.run_btn.pack(side='left')
        self.ocrfix_btn = ttk.Button(btns, text='Re-run OCR correction\u2026', command=self.rerun_ocrfix)
        self.ocrfix_btn.pack(side='left', padx=(6, 0))
        self.translate_btn = ttk.Button(btns, text='Run translation\u2026', command=self.run_translation)
        self.translate_btn.pack(side='left', padx=(6, 0))
        self.render_btn = ttk.Button(btns, text='Render pages\u2026', command=self.open_render)
        self.render_btn.pack(side='left', padx=(6, 0))
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
            if i != 0 and self.project_menu.type(i) != 'separator':   # "Check environment" works without a project
                self.project_menu.entryconfigure(i, state=on)
        for i in range(self.page_menu.index('end') + 1):
            self.page_menu.entryconfigure(i, state=on)
        self.open_btn.configure(state='normal' if self.project and self.index else 'disabled')
        self.run_btn.configure(state=on)
        self.ocrfix_btn.configure(state=on)
        self.translate_btn.configure(state=on)
        self.render_btn.configure(state=on)

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
        if d.project and self._can_close_windows():
            self.set_project(d.project)

    def open_dialog(self):
        d = filedialog.askdirectory(parent=self, title='Open project folder (the one with project.json)',
                                    initialdir=str(self.project.root.parent) if self.project else str(Path.home()))
        if d:
            self.open_project(d)

    def open_project(self, path):
        if not self._can_close_windows():
            return
        try:
            self.set_project(Project.load(path))
        except ProjectError as e:
            messagebox.showerror('Cannot open project', str(e), parent=self)

    def close_project(self):
        if not self._can_close_windows():
            return
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
        self.edits, self.stale_edits = unit_edits.empty(), []
        self.decisions = ocr_decisions.empty()
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
        if os.path.isdir(p.input_dir):
            parts = ['Input: %s  (%d TIFF file%s, %s)' % (p.input_dir, st['tiffs'], '' if st['tiffs'] == 1 else 's',
                                                        human_bytes(st['tiff_bytes']))]
        else:
            parts = ['Input: %s  (folder not found: Project \u203a Change input folder\u2026)' % p.input_dir]
        if st['processed']:
            parts.append('pipeline result: %s' % ('with OCR correction' if st['corrected'] else 'without OCR correction (document.json only)'))
        else:
            parts.append('pipeline not run yet (Project › Run pipeline…)')
        tr = p.load_translations()
        if tr:
            done = sum(1 for u in tr.values() if str(u.get('pl', '')).strip())
            flagged = sum(1 for u in tr.values() if u.get('flags'))
            total = self._translatable()
            parts.append('translation: %d%s units%s' % (done, ' of %d' % total if total else '', ', %d flagged' % flagged if flagged else ''))
        self.info_lbl.configure(text='   ·   '.join(parts))

    def _translatable(self):
        """Units translate.py would translate (translation_status translate), from the loaded document; 0 = unknown."""
        ix = getattr(self, 'index', None)
        if not ix:
            return 0
        import translate
        return sum(1 for u in translate.collect(ix.doc) if u[4] == 'translate')

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
            try:
                edits = unit_edits.load_edits(proj.edits_file)
            except ValueError as e:
                raise ProjectError(str(e))
            try:
                decisions = ocr_decisions.load_decisions(proj.decisions_file)
            except ValueError as e:
                raise ProjectError(str(e))
            result = unit_edits.apply_edits(doc, edits)
            decisions, moved, stale_dec = ocr_decisions.remap(decisions, doc)    # unit numbers may have changed
            if moved or stale_dec:
                ocr_decisions.save_decisions(proj.decisions_file, decisions)
            ix = DocIndex(doc, proj.load_review(), proj.book_dir(), decisions=decisions)
            return which, ix, ix.page_rows(), edits, result['stale'], decisions

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
                self.status(*getattr(self, '_pending_status', None) or ('Nothing to show yet.',))
                self._pending_status = None
                self._menu_state()
                return
            which, self.index, self.rows, self.edits, self.stale_edits, self.decisions = res
            self._header()                       # again: the translation summary needs the loaded document
            self.source_lbl.configure(text='showing document.%sjson' % ('corrected.' if which == 'corrected' else ''))
            self._fill_tree(self.rows, keep)
            for w in list(self.windows.values()):
                w.load()
            self._menu_state()
            if self.stale_edits:
                self.status('%d pages loaded. %d saved edit%s (%s) not applied: the OCR text changed under %s.' % (
                    len(self.rows), len(self.stale_edits), '' if len(self.stale_edits) == 1 else 's',
                    ', '.join(self.stale_edits[:5]), 'it' if len(self.stale_edits) == 1 else 'them'), 'warn')
            else:
                self.status('%d pages loaded.' % len(self.rows))

        run_async(self, work, done)

    def commit_edits(self, changes):
        """Store human texts (``{unit id: text}``) in ``review/unit_edits.json``, apply them to the loaded document and
        re-mark.  The file is written first: when that fails nothing has changed in memory.  -> number of units changed."""
        ix = self.index
        edits = json.loads(json.dumps(self.edits))
        for uid, text in changes.items():
            unit_edits.set_edit(edits, ix.units[uid], text)
        unit_edits.save_edits(self.project.edits_file, edits)
        self.edits = edits
        for uid in changes:
            unit_edits.revert(ix.units[uid])
        unit_edits.apply_edits(ix.doc, {'units': {k: v for k, v in edits['units'].items() if k in changes}})
        ix.refresh()
        self.rows = ix.page_rows()
        self._fill_tree(self.rows, self._selected_page())
        self._on_select()
        for w in list(self.windows.values()):
            w.load(keep_editor=True)
        return len(changes)

    def update_decisions(self, mutate):
        """Change the human decisions about OCR flags: ``mutate(decisions)`` edits a copy, the copy is written to
        ``review/ocr_decisions.json`` first, then the index re-marks.  The next pipeline run (``--resume``) applies them."""
        new = json.loads(json.dumps(self.decisions))
        mutate(new)
        ocr_decisions.stamp(new, {u['unit_id']: u for u in self.index.doc.get('units', [])})   # tie them to the OCR text
        if new == self.decisions:
            return False
        ocr_decisions.save_decisions(self.project.decisions_file, new)
        self.decisions = new
        self.index.set_decisions(new)
        self.rows = self.index.page_rows()
        self._fill_tree(self.rows, self._selected_page())
        self._on_select()
        for w in list(self.windows.values()):
            w.load(keep_editor=True)
        return True

    def _can_close_windows(self):
        """False when the user cancels because a correction window has unsaved changes."""
        dirty = [w for w in self.windows.values() if w.dirty]
        if not dirty:
            return True
        pages = ', '.join(str(w.page_no) for w in dirty)
        return messagebox.askyesno('Unsaved changes', 'Unsaved changes on page %s will be lost.\n\nContinue?' % pages,
                                   icon='warning', parent=self)

    def _close_windows(self):
        for w in list(self.windows.values()):
            w.destroy()
        self.windows.clear()
        gc.collect()
        w = getattr(self, 'run_window', None)
        if w is not None and w.winfo_exists():
            if w.run is not None:
                w.run.stop()                       # a run belongs to the project that is being closed
            w.destroy()
        self.run_window = None
        w = getattr(self, 'render_window', None)
        if w is not None and w.winfo_exists():
            rw = getattr(w, 'run_window', None)
            if rw is not None and rw.winfo_exists() and rw.run is not None:
                rw.run.stop()
            w.destroy()
        self.render_window = None
        gw = getattr(self, 'glossary_window', None)
        if gw is not None and gw.winfo_exists():
            gw.destroy()
        self.glossary_window = None

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

    def select_page(self, n):
        """Select page ``n`` in the list (the correction windows follow when they move through the pages)."""
        if self.tree.exists(str(n)):
            self.tree.selection_set(str(n))
            self.tree.see(str(n))

    def correction_closed(self, n):
        self.windows.pop(n, None)

    def check_env(self):
        EnvWindow(self, project=self.project)

    def run_pipeline(self):
        self._open_run_window('pipeline')

    def run_translation(self, harvest=False):
        """translate.py with the project's translation settings -> translation/translations.json.  ``harvest``: the
        window opens with "collect glossary candidates" ticked."""
        self._open_run_window('translate', harvest=harvest)

    def open_glossary(self):
        """The glossary window (one per project): central glossary, this book's layer, harvested candidates."""
        if not self.project:
            return
        w = getattr(self, 'glossary_window', None)
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            w.reload()
            return
        from gui_glossary import GlossaryWindow
        self.glossary_window = GlossaryWindow(self, self.project)

    def rerun_ocrfix(self):
        """Stage 5 only (``ocrfix.py run`` with the saved edits and decisions) -> a fresh ``document.corrected.json``."""
        self._open_run_window('ocrfix')

    def _open_run_window(self, mode, harvest=False):
        """One run window per project: the pipeline and the OCR correction write document.corrected.json, the
        translation reads it, so they never run at the same time.  A window of another mode is replaced unless it is
        running."""
        if not self.project:
            return
        w = getattr(self, 'run_window', None)
        if w is not None and w.winfo_exists():
            if (w.mode == mode and not harvest) or (w.run is not None and w.run.running):
                if w.mode != mode:
                    messagebox.showinfo('A run is in progress', 'Wait until the running %s has finished.' % w.WHAT, parent=w)
                w.deiconify()
                w.lift()
                return
            w.destroy()
        unsaved = lambda: sorted(n for n, cw in self.windows.items() if cw.dirty)
        if mode == 'translate':
            self.run_window = TranslateWindow(self, self.project, on_done=self._pipeline_done, unsaved=unsaved, harvest=harvest)
        else:
            self.run_window = RunWindow(self, self.project, on_done=self._pipeline_done, mode=mode, unsaved=unsaved)

    def _pipeline_done(self):
        self.reload()
        w = getattr(self, 'glossary_window', None)
        if w is not None and w.winfo_exists():
            w.reload()
        self._header()
        w = getattr(self, 'render_window', None)
        if w is not None and w.winfo_exists():
            w.reload()                       # a new document or translation: lay the book out again

    def open_render(self):
        """The render window (one per project): rebuilt pages, layout checks, book settings and adjustments."""
        if not self.project:
            return
        w = getattr(self, 'render_window', None)
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            return
        from gui_render import RenderWindow
        self.render_window = RenderWindow(self, self.project, page=self._selected_page())

    def change_input(self):
        """Show / change the folder with the TIFF scans (saved in project.json)."""
        if not self.project:
            return
        w = getattr(self, 'run_window', None)
        if w is not None and w.winfo_exists() and w.run is not None and w.run.running:
            messagebox.showinfo('A run is in progress', 'Wait until the running %s has finished.' % w.WHAT, parent=self)
            return
        if change_input_dir(self, self.project):
            self._header()
            if w is not None and w.winfo_exists() and hasattr(w, 'refresh_input'):
                w.refresh_input()
            self.status('Input folder: %s' % self.project.input_dir, 'ok')

    def clear_project(self):
        """Remove the results of processing (pipeline output, translation, rendered pages; optionally the translation
        cache and the review work) for a fresh start; the settings stay.  Not while a run is going."""
        if not self.project:
            return
        runs = [w for w in (getattr(self, 'run_window', None),
                            getattr(getattr(self, 'render_window', None), 'run_window', None))
                if w is not None and w.winfo_exists() and w.run is not None and w.run.running]
        if runs:
            messagebox.showinfo('A run is in progress', 'Wait until the running %s has finished (or stop it).' % runs[0].WHAT,
                                parent=self)
            return
        d = ClearDataDialog(self, self.project)
        self.wait_window(d)
        if not d.result or not self._can_close_windows():
            return
        self._close_windows()
        try:
            removed = self.project.clear_data(d.result)
        except (OSError, ProjectError) as e:
            messagebox.showerror('Clear project data', str(e), parent=self)
            return
        msg = ('Cleared %s: %d item(s) removed. Settings kept; Run pipeline starts afresh.'
               % (', '.join(d.result), len(removed)), 'ok')
        self._pending_status = msg if 'pipeline' in d.result else None
        self.reload()
        self._header()
        if 'pipeline' not in d.result:
            self.status(*msg)

    def show_command(self):
        if self.project:
            CommandDialog(self, self.project)

    def _gc_tick(self):
        if not self.winfo_exists():
            return
        gc.collect()
        self.after(2000, self._gc_tick)

    def quit_app(self):
        if not self._can_close_windows():
            return
        self._close_windows()
        gc.collect()
        gc.enable()
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
            make_demo(d, force=a.force, translate=True)
        path = d
    MainWindow(path).mainloop()


if __name__ == '__main__':
    main()

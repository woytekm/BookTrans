"""Dialogs of the main window: new project, environment check, pipeline command, clear project data."""
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import envcheck
from gui_widgets import MUTED, STATUS_COLORS, copy_to_clipboard, human_bytes, mono_font, run_async
from project import DEFAULT_PARAMS, LAYOUTS, OCRFIX_MODES, Project, ProjectError, tiffs_in, valid_name


class NewProjectDialog(tk.Toplevel):
    """Collects name, input folder, output directory and the pipeline parameters.  ``self.project`` is the created
    ``Project`` after OK, otherwise None."""

    def __init__(self, master, initial_dir=''):
        super().__init__(master)
        self.title('New project')
        self.transient(master)
        self.resizable(False, False)
        self.project = None
        self._auto_name = ''                     # the name we filled in from the input folder; replaced only while unchanged
        v = self.v = {k: tk.StringVar() for k in ('name', 'input', 'output', 'lang', 'scale', 'ocr_scale')}
        v['lang'].set(DEFAULT_PARAMS['lang'])
        v['scale'].set(DEFAULT_PARAMS['scale'])
        v['ocr_scale'].set(str(DEFAULT_PARAMS['ocr_scale']))
        v['output'].set(initial_dir)
        self.layout = tk.StringVar(value=DEFAULT_PARAMS['layout'])
        self.ocrfix = tk.StringVar(value=DEFAULT_PARAMS['ocrfix'])
        self.suggest = tk.BooleanVar(value=DEFAULT_PARAMS['ocr_suggest'])

        f = ttk.Frame(self, padding=14)
        f.grid(sticky='nsew')
        r = 0
        ttk.Label(f, text='Project', font=('TkDefaultFont', 13, 'bold')).grid(row=r, column=0, columnspan=3, sticky='w')
        r += 1
        ttk.Label(f, text='Name').grid(row=r, column=0, sticky='w', pady=3)
        e = ttk.Entry(f, textvariable=v['name'], width=44)
        e.grid(row=r, column=1, sticky='ew', pady=3)
        r += 1
        ttk.Label(f, text='Input folder (TIFF scans)').grid(row=r, column=0, sticky='w', pady=3)
        ttk.Entry(f, textvariable=v['input'], width=44).grid(row=r, column=1, sticky='ew', pady=3)
        ttk.Button(f, text='Browse…', command=lambda: self._browse('input', 'Folder with the TIFF scans')).grid(row=r, column=2, padx=(6, 0))
        r += 1
        self.input_info = ttk.Label(f, text='', foreground=MUTED, wraplength=470, justify='left')
        self.input_info.grid(row=r, column=1, columnspan=2, sticky='w')
        r += 1
        ttk.Label(f, text='Output directory').grid(row=r, column=0, sticky='w', pady=3)
        ttk.Entry(f, textvariable=v['output'], width=44).grid(row=r, column=1, sticky='ew', pady=3)
        ttk.Button(f, text='Browse…', command=lambda: self._browse('output', 'Where the project folder is created')).grid(row=r, column=2, padx=(6, 0))
        r += 1
        self.out_info = ttk.Label(f, text='', foreground=MUTED, wraplength=470, justify='left')
        self.out_info.grid(row=r, column=1, columnspan=2, sticky='w')
        r += 1
        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky='ew', pady=10)
        r += 1
        ttk.Label(f, text='Pipeline parameters (booktrans.py)', font=('TkDefaultFont', 11, 'bold')).grid(row=r, column=0, columnspan=3, sticky='w')
        r += 1
        grid = ttk.Frame(f)
        grid.grid(row=r, column=0, columnspan=3, sticky='ew', pady=(4, 0))
        rows = [('OCR language', ttk.Entry(grid, textvariable=v['lang'], width=12), 'Tesseract language, e.g. deu or deu+eng'),
                ('Page layout', ttk.Combobox(grid, textvariable=self.layout, values=LAYOUTS, state='readonly', width=10),
                 'spread: open book split at the gutter; page: one page per scan; crop: part of a page'),
                ('Scale', ttk.Entry(grid, textvariable=v['scale'], width=12), 'auto = measured from the line pitch; or full px per working px'),
                ('OCR scale', ttk.Entry(grid, textvariable=v['ocr_scale'], width=12), 'upscaling of the crops given to Tesseract (2.0 default)'),
                ('OCR correction', ttk.Combobox(grid, textvariable=self.ocrfix, values=OCRFIX_MODES, state='readonly', width=10),
                 'strict: change only what is safe; aggressive: change more, flag less; off: skip stage 5')]
        for i, (label, widget, hint) in enumerate(rows):
            ttk.Label(grid, text=label).grid(row=i, column=0, sticky='w', pady=2)
            widget.grid(row=i, column=1, sticky='w', padx=(8, 10))
            ttk.Label(grid, text=hint, foreground=MUTED).grid(row=i, column=2, sticky='w')
        ttk.Checkbutton(grid, text='Hunspell suggestions (slow: 3-8 s per hopeless word)', variable=self.suggest).grid(
            row=len(rows), column=1, columnspan=2, sticky='w', pady=(4, 0), padx=(8, 0))
        r += 1
        self.error = ttk.Label(f, text='', foreground=STATUS_COLORS['fail'], wraplength=560, justify='left')
        self.error.grid(row=r, column=0, columnspan=3, sticky='w', pady=(10, 0))
        r += 1
        bar = ttk.Frame(f)
        bar.grid(row=r, column=0, columnspan=3, sticky='ew', pady=(12, 0))
        ttk.Button(bar, text='Check environment…', command=self._check_env).pack(side='left')
        ttk.Button(bar, text='Cancel', command=self.destroy).pack(side='right')
        self.ok = ttk.Button(bar, text='Create project', command=self._create, default='active')
        self.ok.pack(side='right', padx=(0, 8))
        f.columnconfigure(1, weight=1)
        for key in ('name', 'input', 'output'):
            v[key].trace_add('write', lambda *_a: self._refresh())
        self.bind('<Return>', lambda _e: self._create())
        self.bind('<Escape>', lambda _e: self.destroy())
        self._refresh()
        self.update_idletasks()
        self.grab_set()
        e.focus_set()

    # ---------------------------------------------------------------- helpers
    def _browse(self, key, title):
        d = filedialog.askdirectory(parent=self, title=title, initialdir=self.v[key].get() or str(Path.home()))
        if d:
            self.v[key].set(d)

    def _params(self):
        return {'lang': self.v['lang'].get().strip(), 'layout': self.layout.get(), 'scale': self.v['scale'].get().strip(),
                'ocr_scale': self.v['ocr_scale'].get().strip(), 'ocrfix': self.ocrfix.get(), 'ocr_suggest': self.suggest.get()}

    def project_root(self):
        out, name = self.v['output'].get().strip(), self.v['name'].get().strip()
        return Path(out).expanduser() / name if out and name else None

    def _refresh(self):
        inp = self.v['input'].get().strip()
        nm = Path(inp).name if inp else ''
        if nm and self.v['name'].get() in ('', self._auto_name) and nm != self.v['name'].get():
            self._auto_name = nm
            self.v['name'].set(nm)               # triggers _refresh again; the name now equals nm, so it stops there
            return
        if not inp:
            self.input_info.configure(text='', foreground=MUTED)
        elif not Path(inp).expanduser().is_dir():
            self.input_info.configure(text='This folder does not exist.', foreground=STATUS_COLORS['fail'])
        else:
            t = tiffs_in(Path(inp).expanduser())
            if not t:
                self.input_info.configure(text='No .tif/.tiff files in this folder.', foreground=STATUS_COLORS['fail'])
            else:
                note = '%d TIFF files, %s: processed as one book.' % (len(t), human_bytes(sum(p.stat().st_size for p in t)))
                if len(t) == 1:
                    note = '1 TIFF file (%s): the pipeline names its result folder after the file.' % human_bytes(t[0].stat().st_size)
                self.input_info.configure(text=note, foreground=MUTED)
        root = self.project_root()
        if root is None:
            self.out_info.configure(text='', foreground=MUTED)
        elif (root / 'project.json').exists():
            self.out_info.configure(text='There is already a project in %s.' % root, foreground=STATUS_COLORS['fail'])
        else:
            self.out_info.configure(text='Project folder: %s' % root, foreground=MUTED)

    def _check_env(self):
        EnvWindow(self, params=self._params())

    def _create(self):
        self.error.configure(text='')
        name, inp, out = self.v['name'].get().strip(), self.v['input'].get().strip(), self.v['output'].get().strip()
        if not valid_name(name):
            return self._fail('Give the project a name (it becomes a folder name: no slashes, no leading dot).')
        if not inp or not Path(inp).expanduser().is_dir():
            return self._fail('Choose the folder that holds the TIFF scans.')
        if not tiffs_in(Path(inp).expanduser()):
            return self._fail('There are no .tif/.tiff files in the input folder.')
        if not out:
            return self._fail('Choose an output directory.')
        try:
            self.project = Project.create(self.project_root(), name, Path(inp).expanduser(), self._params())
        except ProjectError as e:
            return self._fail(str(e))
        except OSError as e:
            return self._fail('Cannot create the project folder: %s' % e)
        self.destroy()

    def _fail(self, msg):
        self.error.configure(text=msg)


class EnvWindow(tk.Toplevel):
    """The environment check as a table; selecting a row shows what to do about it."""

    def __init__(self, master, project=None, params=None):
        super().__init__(master)
        self.title('Environment check')
        self.geometry('900x560')
        self.project, self.params = project, params
        self.checks, self._by_item = [], {}
        top = ttk.Frame(self, padding=(12, 10, 12, 4))
        top.pack(fill='x')
        self.summary = ttk.Label(top, text='Checking…', font=('TkDefaultFont', 12, 'bold'))
        self.summary.pack(side='left')
        ttk.Button(top, text='Copy report', command=self._copy).pack(side='right')
        ttk.Button(top, text='Re-check', command=self.run).pack(side='right', padx=6)
        body = ttk.Frame(self, padding=(12, 0))
        body.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(body, columns=('status', 'detail'), selectmode='browse')
        self.tree.heading('#0', text='Item')
        self.tree.heading('status', text='Status')
        self.tree.heading('detail', text='Detail')
        self.tree.column('#0', width=300, stretch=False)
        self.tree.column('status', width=60, anchor='center', stretch=False)
        self.tree.column('detail', width=480)
        ys = ttk.Scrollbar(body, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side='left', fill='both', expand=True)
        ys.pack(side='left', fill='y')
        for st, col in STATUS_COLORS.items():
            self.tree.tag_configure(st, foreground=col)
        self.tree.tag_configure('group', font=('TkDefaultFont', 11, 'bold'))
        self.hint = ttk.Label(self, text='', wraplength=860, justify='left', padding=(12, 8))
        self.hint.pack(fill='x')
        self.tree.bind('<<TreeviewSelect>>', self._select)
        self.run()

    def run(self):
        self.summary.configure(text='Checking…', foreground=MUTED)
        self.tree.delete(*self.tree.get_children())
        self.hint.configure(text='')
        run_async(self, lambda: envcheck.run_checks(project=self.project, params=self.params), self._done)

    def _done(self, checks, err):
        if not self.winfo_exists():
            return
        if err:
            self.summary.configure(text='The check itself failed: %s' % err, foreground=STATUS_COLORS['fail'])
            return
        self.checks = checks
        self._by_item, groups = {}, {}
        label = {'ok': 'OK', 'warn': 'warn', 'fail': 'FAIL', 'info': 'info'}
        for c in checks:
            if c.group not in groups:
                groups[c.group] = self.tree.insert('', 'end', text=c.group, open=True, tags=('group',))
            iid = self.tree.insert(groups[c.group], 'end', text=c.name, values=(label[c.status], c.detail), tags=(c.status,))
            self._by_item[iid] = c
        w = envcheck.worst(checks)
        self.summary.configure(text={'ok': 'Everything needed is in place', 'warn': 'Works, with warnings', 'fail': 'Something is missing'}[w],
                               foreground=STATUS_COLORS[w])

    def _select(self, _e=None):
        sel = self.tree.selection()
        c = self._by_item.get(sel[0]) if sel else None
        self.hint.configure(text=('%s\n→ %s' % (c.detail, c.hint) if c.hint else c.detail) if c else '')

    def _copy(self):
        copy_to_clipboard(self, envcheck.format_report(self.checks))


class CommandDialog(tk.Toplevel):
    """Shows the booktrans.py command line of the project (running it from the GUI is a later stage)."""

    def __init__(self, master, project):
        super().__init__(master)
        self.title('Pipeline command')
        self.geometry('860x330')
        self.project = project
        f = ttk.Frame(self, padding=12)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text='Run this in a terminal (any folder). The GUI shows what it produces; running it from here comes in a later stage.',
                  wraplength=820, justify='left').pack(anchor='w')
        self.resume = tk.BooleanVar(value=project.status()['processed'])
        ttk.Checkbutton(f, text='--resume: keep finished spreads (stages 1-3), redo stage 4 onwards', variable=self.resume,
                        command=self._fill).pack(anchor='w', pady=(8, 4))
        self.text = tk.Text(f, height=5, wrap='word', font=mono_font(), padx=8, pady=6)
        self.text.pack(fill='x')
        self.warn = ttk.Label(f, text='', foreground=STATUS_COLORS['warn'], wraplength=820, justify='left')
        self.warn.pack(anchor='w', pady=8)
        bar = ttk.Frame(f)
        bar.pack(fill='x', side='bottom')
        ttk.Button(bar, text='Close', command=self.destroy).pack(side='right')
        ttk.Button(bar, text='Copy', command=lambda: copy_to_clipboard(self, self.project.command_line(self.resume.get()))).pack(side='right', padx=8)
        self._fill()

    def _fill(self):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', self.project.command_line(self.resume.get()))
        self.text.configure(state='disabled')
        b = self.project.book_dir()
        if b.exists() and not self.resume.get():
            self.warn.configure(text='Without --resume the pipeline first DELETES %s, including its ocr_review.json (decisions made there). '
                                     'Files in %s are not touched.' % (b, self.project.review_dir))
        else:
            self.warn.configure(text='')


class ClearDataDialog(tk.Toplevel):
    """Clear project data: choose what to remove (``Project.data_parts``); the settings are always kept.
    ``result`` is the list of chosen keys after *Clear*, None after Cancel."""

    def __init__(self, master, project):
        super().__init__(master)
        self.title('Clear project data')
        self.transient(master)
        self.project = project
        self.result = None
        self.parts = project.data_parts()
        f = ttk.Frame(self, padding=14)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text='Remove the results of processing, for a fresh start. The project settings (input folder, pipeline, '
                          'translation and render settings in project.json) are kept.', wraplength=620, justify='left').pack(anchor='w')
        self.vars = {}
        box = ttk.Frame(f)
        box.pack(fill='x', pady=(10, 6))
        if not self.parts:
            ttk.Label(box, text='There is nothing to remove: the project has no results yet.', foreground=MUTED).pack(anchor='w')
        for i, p in enumerate(self.parts):
            v = tk.BooleanVar(value=p['default'])
            self.vars[p['key']] = v
            ttk.Checkbutton(box, text='%s   (%s)' % (p['label'], human_bytes(p['bytes'])), variable=v,
                            command=self._update).grid(row=2 * i, column=0, sticky='w', pady=(4, 0))
            if p['note']:
                note = p['note'] if p['default'] else 'unticked: ' + p['note']
                ttk.Label(box, text=note, foreground=MUTED, wraplength=580, justify='left').grid(
                    row=2 * i + 1, column=0, sticky='w', padx=(26, 0))
        self.warn = ttk.Label(f, text='', foreground=STATUS_COLORS['warn'], wraplength=620, justify='left')
        self.warn.pack(anchor='w', pady=(6, 6))
        bar = ttk.Frame(f)
        bar.pack(fill='x', side='bottom')
        ttk.Button(bar, text='Cancel', command=self.destroy).pack(side='right')
        self.clear_btn = ttk.Button(bar, text='Clear', command=self._clear)
        self.clear_btn.pack(side='right', padx=8)
        self._update()
        self.bind('<Escape>', lambda _e: self.destroy())

    def chosen(self):
        return [k for k, v in self.vars.items() if v.get()]

    def _update(self):
        keys = self.chosen()
        notes = []
        if 'pipeline' in keys:
            notes.append('The next Run pipeline starts from the scans (OCR included).')
        if 'review' in keys:
            notes.append('Your corrections and decisions of the review are deleted; this cannot be undone.')
        if 'cache' in keys:
            notes.append('Every unit will be sent to the translation engine again.')
        self.warn.configure(text=' '.join(notes))
        self.clear_btn.configure(state='normal' if keys else 'disabled')

    def _clear(self):
        keys = self.chosen()
        if 'review' in keys and not messagebox.askyesno(
                'Delete your review work?', 'Unit edits, OCR decisions and render adjustments will be deleted for good.\n\n'
                                            'Delete them?', icon='warning', parent=self):
            return
        self.result = keys
        self.destroy()


def change_input_dir(master, project):
    """Ask for a new input folder, show what is found there and what it means for existing results, and store it.
    -> True when the folder was changed."""
    new = filedialog.askdirectory(parent=master, title='Input folder with the TIFF scans', initialdir=project.input_dir,
                                  mustexist=True)
    if not new:
        return False
    new = str(Path(new))
    if new == project.input_dir:
        return False
    t = tiffs_in(new)
    msg = ['New input folder:\n%s\n\n%d TIFF file%s found%s.' % (new, len(t), '' if len(t) == 1 else 's',
                                                                ' (%s \u2026)' % ', '.join(x.name for x in t[:3]) if t else '')]
    warnings = project.input_change_warnings(new)
    msg += warnings
    if not messagebox.askyesno('Change the input folder?', '\n\n'.join(msg + ['Change it?']),
                               icon='warning' if warnings else 'question', parent=master):
        return False
    try:
        project.set_input_dir(new)
    except ProjectError as e:
        messagebox.showerror('Input folder', str(e), parent=master)
        return False
    return True

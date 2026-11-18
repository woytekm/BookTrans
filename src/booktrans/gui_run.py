"""Run windows: start a pipeline program as a subprocess and show its output live.

``RunWindow`` sets the pipeline parameters and runs ``booktrans.py``; its mode ``ocrfix`` runs only stage 5
(``ocrfix.py run``, ``project.ocrfix_command``): the saved edits and decisions of the GUI go into
``document.corrected.json`` in seconds, without redoing stage 4.

``TranslateWindow`` sets the translation settings of the project (engine, model, glossary ...; saved in
``project.json``) and runs ``translate.py`` -> ``translation/translations.json``, for the whole book or some pages.

Both share ``ProcessWindow``: Start / Stop, live log, elapsed time, ``on_done`` when the run ends."""
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from gui_widgets import MUTED, STATUS_COLORS, TEXT_BG, TEXT_FG, SEL_BG, SEL_FG, mono_font
from project import (LAYOUTS, OCRFIX_MODES, PROVIDERS, TR_SOURCES, PipelineRun, ProjectError, check_params,
                     check_translation)


class ProcessWindow(tk.Toplevel):
    """Base: a settings area (``_build_settings``), Start / Stop / Close, a log.  ``_prepare()`` validates, saves the
    settings and returns ``(argument list, command line)`` or None to cancel.  ``unsaved()`` returns the pages whose
    correction window has unsaved changes: they are not in ``review/`` yet, so a run would not see them."""

    WHAT = 'run'                # "Stop the <WHAT> and close this window?"

    def __init__(self, master, project, title, on_done=None, unsaved=None):
        super().__init__(master)
        self.project = project
        self.on_done = on_done
        self.unsaved = unsaved or (lambda: [])
        self.run = None
        self.started = 0.0
        self.title(title)
        self.geometry('900x660')
        self.controls = []

        f = ttk.Frame(self, padding=10)
        f.pack(fill='both', expand=True)
        self._build_settings(f)
        self.warn = ttk.Label(f, text='', foreground=STATUS_COLORS['warn'], wraplength=860, justify='left')
        self.warn.pack(anchor='w', pady=(2, 6))

        bar = ttk.Frame(f)
        bar.pack(fill='x')
        self.start_btn = ttk.Button(bar, text='Start', command=self.start)
        self.start_btn.pack(side='left')
        self.stop_btn = ttk.Button(bar, text='Stop', command=self.stop, state='disabled')
        self.stop_btn.pack(side='left', padx=6)
        self.state = ttk.Label(bar, text='not started')
        self.state.pack(side='left', padx=10)
        ttk.Button(bar, text='Close', command=self.close).pack(side='right')

        self.last = ttk.Label(f, text='', foreground=MUTED)
        self.last.pack(anchor='w', pady=(6, 2))
        box = ttk.Frame(f)
        box.pack(fill='both', expand=True)
        self.log = tk.Text(box, wrap='none', font=mono_font(-1), bg=TEXT_BG, fg=TEXT_FG, insertbackground=TEXT_FG,
                           selectbackground=SEL_BG, selectforeground=SEL_FG, relief='flat', state='disabled', padx=6, pady=4)
        ys = ttk.Scrollbar(box, orient='vertical', command=self.log.yview)
        xs = ttk.Scrollbar(box, orient='horizontal', command=self.log.xview)
        self.log.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.log.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        xs.grid(row=1, column=0, sticky='ew')
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self._warn()

    # ---- for subclasses
    def _build_settings(self, f):
        pass

    def _prepare(self):
        raise NotImplementedError

    def _warn(self):
        self.warn.configure(text='')

    def _control_state(self, w):
        """State of a settings control when no run is going (subclasses disable what does not apply)."""
        return 'readonly' if isinstance(w, ttk.Combobox) else 'normal'

    # ---- common
    def _set_running(self, on):
        for w in self.controls:
            w.configure(state='disabled' if on else self._control_state(w))
        self.start_btn.configure(state='disabled' if on else 'normal')
        self.stop_btn.configure(state='normal' if on else 'disabled')

    def _append(self, lines):
        if not lines:
            return
        at_end = self.log.yview()[1] >= 0.999
        self.log.configure(state='normal')
        self.log.insert('end', '\n'.join(lines) + '\n')
        self.log.configure(state='disabled')
        if at_end:
            self.log.see('end')
        self.last.configure(text=lines[-1].strip()[:140])

    def _check_unsaved(self):
        dirty = self.unsaved()
        return not dirty or messagebox.askyesno(
            'Unsaved changes', 'Page %s has unsaved changes in its correction window. They are not saved yet, so '
                               'this run will not include them.\n\nRun anyway?' % ', '.join(str(n) for n in dirty),
            icon='warning', parent=self)

    def start(self):
        if self.run is not None and self.run.running:
            return
        prepared = self._prepare()
        if not prepared:
            return
        cmd, line = prepared
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')
        self._append(['$ ' + line, ''])
        try:
            self.run = PipelineRun(cmd)
        except OSError as e:
            messagebox.showerror('Cannot start', str(e), parent=self)
            return
        self.started = time.time()
        self._set_running(True)
        self.after(150, self._poll)

    def _poll(self):
        run = self.run
        if run is None:
            return
        self._append(run.drain())
        elapsed = int(time.time() - self.started)
        if run.running:
            self.state.configure(text='running  %d:%02d' % divmod(elapsed, 60), foreground=STATUS_COLORS['info'])
            self.after(200, self._poll)
            return
        self._append(run.drain())
        rc = run.returncode
        if run.stopped:
            text, col = 'stopped after %d:%02d' % divmod(elapsed, 60), STATUS_COLORS['warn']
        elif rc == 0:
            text, col = 'finished in %d:%02d' % divmod(elapsed, 60), STATUS_COLORS['ok']
        else:
            text, col = 'FAILED (exit %s) after %d:%02d, see the end of the log' % ((rc,) + divmod(elapsed, 60)), STATUS_COLORS['fail']
        self.state.configure(text=text, foreground=col)
        self._set_running(False)
        self._warn()
        if self.on_done:
            self.on_done()

    def stop(self):
        if self.run is not None and self.run.running:
            self.run.stop()
            self.state.configure(text='stopping…')

    def close(self):
        if self.run is not None and self.run.running:
            if not messagebox.askyesno('%s is running' % self.WHAT.capitalize(),
                                       'Stop the %s and close this window?' % self.WHAT, parent=self):
                return
            self.run.stop()
        self.destroy()


class RunWindow(ProcessWindow):
    """The pipeline (``mode='pipeline'``, booktrans.py) or stage 5 alone (``mode='ocrfix'``)."""

    def __init__(self, master, project, on_done=None, mode='pipeline', unsaved=None):
        self.mode = mode
        self.WHAT = 'OCR correction' if mode == 'ocrfix' else 'pipeline'
        super().__init__(master, project, ('Re-run OCR correction: %s' if mode == 'ocrfix' else 'Run pipeline: %s') % project.name,
                         on_done=on_done, unsaved=unsaved)

    def _build_settings(self, f):
        p = self.project.params
        self.v = {'lang': tk.StringVar(value=p['lang']), 'layout': tk.StringVar(value=p['layout']),
                  'scale': tk.StringVar(value=str(p['scale'])), 'ocr_scale': tk.StringVar(value=str(p['ocr_scale'])),
                  'ocrfix': tk.StringVar(value=p['ocrfix']), 'ocr_suggest': tk.BooleanVar(value=bool(p['ocr_suggest']))}
        self.resume = tk.BooleanVar(value=self.project.status()['processed'])
        g = ttk.Frame(f)
        g.pack(fill='x')
        cells = [('OCR language', ttk.Entry(g, textvariable=self.v['lang'], width=10)),
                 ('Page layout', ttk.Combobox(g, textvariable=self.v['layout'], values=LAYOUTS, state='readonly', width=8)),
                 ('Scale', ttk.Entry(g, textvariable=self.v['scale'], width=7)),
                 ('OCR scale', ttk.Entry(g, textvariable=self.v['ocr_scale'], width=7)),
                 ('OCR correction', ttk.Combobox(g, textvariable=self.v['ocrfix'], values=OCRFIX_MODES, state='readonly', width=10))]
        if self.mode == 'ocrfix':                     # only what stage 5 uses
            for _label, w in cells[:-1]:
                w.destroy()
            cells = cells[-1:]
        for i, (label, w) in enumerate(cells):
            ttk.Label(g, text=label).grid(row=0, column=i, sticky='w', padx=(0, 10))
            w.grid(row=1, column=i, sticky='w', padx=(0, 10), pady=(0, 4))
            self.controls.append(w)
        c = ttk.Checkbutton(g, text='suggest OCR fixes', variable=self.v['ocr_suggest'])
        c.grid(row=1, column=len(cells), sticky='w')
        self.controls.append(c)
        if self.mode == 'ocrfix':
            ttk.Label(f, text='Runs only the OCR correction (stage 5) on %s, with your saved edits and decisions '
                              '-> document.corrected.json (the file translate.py reads). Stages 1-4 are not touched.'
                              % (self.project.book_dir() / 'document.json'), foreground=MUTED, wraplength=860,
                      justify='left').pack(anchor='w', pady=(4, 0))
        else:
            r = ttk.Checkbutton(f, text='--resume: keep finished spreads (stages 1-3), redo stage 4 onwards', variable=self.resume,
                                command=self._warn)
            r.pack(anchor='w', pady=(4, 0))
            self.controls.append(r)

    def _params(self):
        v = self.v
        return {'lang': v['lang'].get().strip(), 'layout': v['layout'].get(), 'scale': v['scale'].get().strip(),
                'ocr_scale': v['ocr_scale'].get().strip(), 'ocrfix': v['ocrfix'].get(), 'ocr_suggest': v['ocr_suggest'].get()}

    def _warn(self):
        b = self.project.book_dir()
        if self.mode == 'pipeline' and b.exists() and not self.resume.get():
            self.warn.configure(text='Without --resume the pipeline first DELETES %s, including its ocr_review.json. '
                                     'Your own work in %s is not touched.' % (b, self.project.review_dir))
        else:
            self.warn.configure(text='')

    def _prepare(self):
        try:
            params = check_params(self._params())
        except ProjectError as e:
            messagebox.showerror('Parameters', str(e), parent=self)
            return None
        if not self._check_unsaved():
            return None
        b = self.project.book_dir()
        if self.mode == 'pipeline' and b.exists() and not self.resume.get():
            if not messagebox.askyesno('Delete the previous result?',
                                       'A run without --resume deletes\n%s\nincluding its ocr_review.json.\n\nContinue?' % b,
                                       icon='warning', parent=self):
                return None
        self.project.data['params'] = params
        self.project.save()
        if self.mode == 'ocrfix':
            try:
                return self.project.ocrfix_command(), self.project.ocrfix_command_line()
            except ProjectError as e:
                messagebox.showerror('OCR correction', str(e), parent=self)
                return None
        return self.project.command(self.resume.get()), self.project.command_line(self.resume.get())


MODEL_HINT = {'claude-code': 'blank = opus (or sonnet, or a full model id)',
              'openai': 'blank = the first model LM Studio reports',
              'anthropic': 'blank = claude-sonnet-5-5'}
ENDPOINT_HINT = {'claude-code': 'not used (the claude program on this computer)',
                 'openai': 'blank = $LMSTUDIO_ENDPOINT or http://localhost:1234/v1',
                 'anthropic': 'blank = https://api.anthropic.com (key in $ANTHROPIC_API_KEY)'}


class TranslateWindow(ProcessWindow):
    """translate.py with the project's translation settings.  ``pages`` (e.g. the selected page) preselects a partial
    run; pages and dry run are per run and not saved."""

    WHAT = 'translation'
    mode = 'translate'

    def __init__(self, master, project, on_done=None, unsaved=None, pages=''):
        self._pages0 = pages
        super().__init__(master, project, 'Run translation: %s' % project.name, on_done=on_done, unsaved=unsaved)

    def _build_settings(self, f):
        t = self.project.translation
        self.v = {'provider': tk.StringVar(value=t['provider']), 'model': tk.StringVar(value=t['model']),
                  'endpoint': tk.StringVar(value=t['endpoint']), 'glossary': tk.StringVar(value=t['glossary']),
                  'source': tk.StringVar(value=t['source']), 'timeout': tk.StringVar(value=str(t['timeout'])),
                  'retries': tk.StringVar(value=str(t['retries'])), 'no_think': tk.BooleanVar(value=bool(t['no_think'])),
                  'pages': tk.StringVar(value=self._pages0 or ''), 'dry_run': tk.BooleanVar(value=False),
                  'force': tk.BooleanVar(value=False)}
        g = ttk.Frame(f)
        g.pack(fill='x')
        self.provider_box = ttk.Combobox(g, textvariable=self.v['provider'], values=PROVIDERS, state='readonly', width=12)
        self.model_entry = ttk.Entry(g, textvariable=self.v['model'], width=22)
        cells = [('Engine', self.provider_box), ('Model', self.model_entry),
                 ('Source text', ttk.Combobox(g, textvariable=self.v['source'], values=TR_SOURCES, state='readonly', width=10)),
                 ('Timeout (s)', ttk.Entry(g, textvariable=self.v['timeout'], width=7)),
                 ('Retries', ttk.Entry(g, textvariable=self.v['retries'], width=5))]
        for i, (label, w) in enumerate(cells):
            ttk.Label(g, text=label).grid(row=0, column=i, sticky='w', padx=(0, 10))
            w.grid(row=1, column=i, sticky='w', padx=(0, 10), pady=(0, 2))
            self.controls.append(w)
        self.model_hint = ttk.Label(g, text='', foreground=MUTED)
        self.model_hint.grid(row=2, column=1, columnspan=4, sticky='w', pady=(0, 6))

        e = ttk.Frame(f)
        e.pack(fill='x')
        ttk.Label(e, text='Endpoint').grid(row=0, column=0, sticky='w', padx=(0, 10))
        self.endpoint_entry = ttk.Entry(e, textvariable=self.v['endpoint'], width=40)
        self.endpoint_entry.grid(row=0, column=1, sticky='w')
        self.no_think_btn = ttk.Checkbutton(e, text='no thinking (reasoning models)', variable=self.v['no_think'])
        self.no_think_btn.grid(row=0, column=2, sticky='w', padx=(12, 0))
        self.endpoint_hint = ttk.Label(e, text='', foreground=MUTED)
        self.endpoint_hint.grid(row=1, column=1, columnspan=2, sticky='w', pady=(0, 6))
        ttk.Label(e, text='Glossary').grid(row=2, column=0, sticky='w', padx=(0, 10))
        ge = ttk.Entry(e, textvariable=self.v['glossary'], width=70)
        ge.grid(row=2, column=1, columnspan=2, sticky='we')
        gb = ttk.Button(e, text='Add…', command=self._add_glossary)
        gb.grid(row=2, column=3, sticky='w', padx=(6, 0))
        ttk.Label(e, text="CSV files separated by ';'  (blank = the repository's glossary/*.csv)",
                  foreground=MUTED).grid(row=3, column=1, columnspan=3, sticky='w', pady=(0, 6))
        self.controls += [self.endpoint_entry, self.no_think_btn, ge, gb]

        r = ttk.Frame(f)
        r.pack(fill='x', pady=(2, 0))
        ttk.Label(r, text='This run:').pack(side='left')
        ttk.Label(r, text='pages').pack(side='left', padx=(10, 4))
        pe = ttk.Entry(r, textvariable=self.v['pages'], width=12)
        pe.pack(side='left')
        ttk.Label(r, text='(e.g. 3 or 1,4-6; blank = the whole book)', foreground=MUTED).pack(side='left', padx=(6, 12))
        dr = ttk.Checkbutton(r, text='dry run (write the prompts, no model calls)', variable=self.v['dry_run'])
        dr.pack(side='left')
        r2 = ttk.Frame(f)
        r2.pack(fill='x', pady=(2, 0))
        fo = ttk.Checkbutton(r2, text='re-translate: ignore cached answers and ask the model again for these pages',
                             variable=self.v['force'])
        fo.pack(side='left', padx=(62, 0))
        self.controls += [pe, dr, fo]
        ttk.Label(f, text='Result: %s  (answers are cached beside it, so a repeated or interrupted run only translates '
                          'what changed; a page run keeps the other pages).' % self.project.translations_file,
                  foreground=MUTED, wraplength=860, justify='left').pack(anchor='w', pady=(6, 0))
        self.v['provider'].trace_add('write', lambda *_: self._provider_changed())
        self.v['source'].trace_add('write', lambda *_: self._warn())
        self._provider_changed()

    def _provider_changed(self):
        pr = self.v['provider'].get()
        self.model_hint.configure(text=MODEL_HINT.get(pr, ''))
        self.endpoint_hint.configure(text=ENDPOINT_HINT.get(pr, ''))
        if self.run is None or not self.run.running:
            self.endpoint_entry.configure(state=self._control_state(self.endpoint_entry))
            self.no_think_btn.configure(state=self._control_state(self.no_think_btn))

    def _control_state(self, w):
        pr = self.v['provider'].get()
        if w is getattr(self, 'endpoint_entry', None) and pr == 'claude-code':
            return 'disabled'
        if w is getattr(self, 'no_think_btn', None) and pr != 'openai':
            return 'disabled'
        return super()._control_state(w)

    def _add_glossary(self):
        files = filedialog.askopenfilenames(parent=self, title='Glossary CSV files',
                                            filetypes=[('CSV', '*.csv'), ('All files', '*')])
        if files:
            cur = [x.strip() for x in self.v['glossary'].get().split(';') if x.strip()]
            self.v['glossary'].set('; '.join(cur + [x for x in files if x not in cur]))

    def _settings(self):
        v = self.v
        return {'provider': v['provider'].get(), 'model': v['model'].get().strip(), 'endpoint': v['endpoint'].get().strip(),
                'glossary': v['glossary'].get().strip(), 'source': v['source'].get(), 'timeout': v['timeout'].get().strip(),
                'retries': v['retries'].get().strip(), 'no_think': v['no_think'].get()}

    def _warn(self):
        saved = dict(self.project.translation)
        self.project.translation['source'] = self.v['source'].get()          # warnings for what is chosen, not yet saved
        try:
            self.warn.configure(text='\n'.join(self.project.translation_warnings()))
        finally:
            self.project.translation.clear()
            self.project.translation.update(saved)

    def _prepare(self):
        try:
            t = check_translation(self._settings(), self.project.root)
            t['timeout'] = float(t['timeout'])
            if t['timeout'].is_integer():
                t['timeout'] = int(t['timeout'])
            t['retries'] = int(t['retries'])
        except ProjectError as e:
            messagebox.showerror('Translation settings', str(e), parent=self)
            return None
        pages = self.v['pages'].get().strip()
        if pages:
            import translate
            try:
                translate.parse_pages(pages)
            except ValueError as e:
                messagebox.showerror('Pages', str(e), parent=self)
                return None
        force = self.v['force'].get()
        if force and not pages and not self.v['dry_run'].get() and not messagebox.askyesno(
                'Re-translate the whole book?', 'No pages are given, so every unit of the book is sent to the model again '
                                                 '(cached answers are ignored and replaced).\n\nContinue?', icon='warning', parent=self):
            return None
        if not self._check_unsaved():
            return None
        self.project.translation.update(t)
        self.project.save()
        warnings = self.project.translation_warnings()
        if warnings and not self.v['dry_run'].get() and not messagebox.askyesno(
                'Translate anyway?', '\n\n'.join(warnings) + '\n\nTranslate anyway?', icon='warning', parent=self):
            return None
        try:
            dry = self.v['dry_run'].get()
            return (self.project.translate_command(pages, dry, force),
                    self.project.translate_command_line(pages, dry, force))
        except ProjectError as e:
            messagebox.showerror('Translation', str(e), parent=self)
            return None

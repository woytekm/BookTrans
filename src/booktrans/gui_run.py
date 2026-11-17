"""Run window: sets the pipeline parameters, starts ``booktrans.py`` as a subprocess and shows its output live."""
import time
import tkinter as tk
from tkinter import messagebox, ttk

from gui_widgets import MUTED, STATUS_COLORS, TEXT_BG, TEXT_FG, SEL_BG, SEL_FG, mono_font
from project import LAYOUTS, OCRFIX_MODES, PipelineRun, ProjectError, check_params


class RunWindow(tk.Toplevel):
    """One window per project.  ``on_done()`` is called when a run ends (the main window reloads the result)."""

    def __init__(self, master, project, on_done=None):
        super().__init__(master)
        self.project = project
        self.on_done = on_done
        self.run = None
        self.started = 0.0
        self.title('Run pipeline: %s' % project.name)
        self.geometry('900x640')
        p = project.params
        self.v = {'lang': tk.StringVar(value=p['lang']), 'layout': tk.StringVar(value=p['layout']),
                  'scale': tk.StringVar(value=str(p['scale'])), 'ocr_scale': tk.StringVar(value=str(p['ocr_scale'])),
                  'ocrfix': tk.StringVar(value=p['ocrfix']), 'ocr_suggest': tk.BooleanVar(value=bool(p['ocr_suggest']))}
        self.resume = tk.BooleanVar(value=project.status()['processed'])

        f = ttk.Frame(self, padding=10)
        f.pack(fill='both', expand=True)
        g = ttk.Frame(f)
        g.pack(fill='x')
        self.controls = []
        cells = [('OCR language', ttk.Entry(g, textvariable=self.v['lang'], width=10)),
                 ('Page layout', ttk.Combobox(g, textvariable=self.v['layout'], values=LAYOUTS, state='readonly', width=8)),
                 ('Scale', ttk.Entry(g, textvariable=self.v['scale'], width=7)),
                 ('OCR scale', ttk.Entry(g, textvariable=self.v['ocr_scale'], width=7)),
                 ('OCR correction', ttk.Combobox(g, textvariable=self.v['ocrfix'], values=OCRFIX_MODES, state='readonly', width=10))]
        for i, (label, w) in enumerate(cells):
            ttk.Label(g, text=label).grid(row=0, column=i, sticky='w', padx=(0, 10))
            w.grid(row=1, column=i, sticky='w', padx=(0, 10), pady=(0, 4))
            self.controls.append(w)
        c = ttk.Checkbutton(g, text='suggest OCR fixes', variable=self.v['ocr_suggest'])
        c.grid(row=1, column=len(cells), sticky='w')
        self.controls.append(c)
        r = ttk.Checkbutton(f, text='--resume: keep finished spreads (stages 1-3), redo stage 4 onwards', variable=self.resume,
                            command=self._warn)
        r.pack(anchor='w', pady=(4, 0))
        self.controls.append(r)
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
        self.log = tk.Text(box, wrap='none', font=mono_font(-1), bg=TEXT_BG, fg=TEXT_FG, insertbackground=TEXT_FG, selectbackground=SEL_BG, selectforeground=SEL_FG, relief='flat',
                           state='disabled', padx=6, pady=4)
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

    # ------------------------------------------------------------------
    def _params(self):
        v = self.v
        return {'lang': v['lang'].get().strip(), 'layout': v['layout'].get(), 'scale': v['scale'].get().strip(),
                'ocr_scale': v['ocr_scale'].get().strip(), 'ocrfix': v['ocrfix'].get(), 'ocr_suggest': v['ocr_suggest'].get()}

    def _warn(self):
        b = self.project.book_dir()
        if b.exists() and not self.resume.get():
            self.warn.configure(text='Without --resume the pipeline first DELETES %s, including its ocr_review.json. '
                                     'Your own work in %s is not touched.' % (b, self.project.review_dir))
        else:
            self.warn.configure(text='')

    def _set_running(self, on):
        for w in self.controls:
            w.configure(state='disabled' if on else ('readonly' if isinstance(w, ttk.Combobox) else 'normal'))
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

    def start(self):
        if self.run is not None and self.run.running:
            return
        try:
            params = check_params(self._params())
        except ProjectError as e:
            messagebox.showerror('Parameters', str(e), parent=self)
            return
        b = self.project.book_dir()
        if b.exists() and not self.resume.get():
            if not messagebox.askyesno('Delete the previous result?',
                                       'A run without --resume deletes\n%s\nincluding its ocr_review.json.\n\nContinue?' % b,
                                       icon='warning', parent=self):
                return
        self.project.data['params'] = params
        self.project.save()
        cmd = self.project.command(self.resume.get())
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')
        self._append(['$ ' + self.project.command_line(self.resume.get()), ''])
        try:
            self.run = PipelineRun(cmd)
        except OSError as e:
            messagebox.showerror('Cannot start the pipeline', str(e), parent=self)
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
            if not messagebox.askyesno('Pipeline is running', 'Stop the pipeline and close this window?', parent=self):
                return
            self.run.stop()
        self.destroy()

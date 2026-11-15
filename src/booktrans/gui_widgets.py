"""Shared Tk pieces: colours, a read-only JSON viewer with search, background work, small helpers.

Needs tkinter (system package; MacPorts: ``sudo port install py3NN-tkinter``) and Pillow's ImageTk.
"""
import json
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

MAC = sys.platform == 'darwin'
MOD = 'Command' if MAC else 'Control'          # key modifier of the shortcuts
MOD_LABEL = 'Cmd' if MAC else 'Ctrl'

# Mark classes (pageindex.CLASSES): one colour each, used by the legend, the page list and, later, the page image.
CLASS_COLORS = {'auto': '#1f6fd1', 'uncertain': '#d62f2f', 'watch': '#e69f00', 'human': '#2a9d4b'}
CLASS_LABELS = {'auto': 'corrected automatically', 'uncertain': 'uncertain: please check',
                'watch': 'watch: probably fine', 'human': 'decided by a human'}
STATUS_COLORS = {'ok': '#2a7a2a', 'warn': '#b36b00', 'fail': '#c0261f', 'info': '#666666'}
ROW_TINT = {'attention': '#fdeaea', 'watch': '#fff4d6'}
TEXT_BG, TEXT_FG = '#fbfaf6', '#222222'         # set explicitly: the system dark mode must not recolour only half of a widget

JSON_TOKEN = re.compile(r'(?P<key>"(?:[^"\\]|\\.)*"(?=\s*:))|(?P<str>"(?:[^"\\]|\\.)*")'
                        r'|(?P<num>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(?P<lit>\btrue\b|\bfalse\b|\bnull\b)')
JSON_COLORS = {'key': '#0b5394', 'str': '#2a7a2a', 'num': '#a15c00', 'lit': '#8a2be2'}


def mono_font(delta=0):
    f = tkfont.nametofont('TkFixedFont').copy()
    f.configure(size=(12 if MAC else 10) + delta)
    return f


def run_async(widget, fn, done, poll_ms=60):
    """Run ``fn()`` in a thread; call ``done(result, error)`` in the Tk thread.  Tk is never touched from the thread."""
    q = queue.Queue()

    def work():
        try:
            q.put((fn(), None))
        except Exception as e:                  # reported to the caller, not swallowed
            q.put((None, e))

    def poll():
        try:
            res, err = q.get_nowait()
        except queue.Empty:
            widget.after(poll_ms, poll)
            return
        done(res, err)

    threading.Thread(target=work, daemon=True).start()
    widget.after(poll_ms, poll)


def reveal_in_file_manager(path):
    path = str(path)
    try:
        if MAC:
            subprocess.Popen(['open', path])
        elif sys.platform.startswith('win'):
            subprocess.Popen(['explorer', path])
        else:
            subprocess.Popen(['xdg-open', path])
    except OSError:
        pass


def copy_to_clipboard(widget, text):
    widget.clipboard_clear()
    widget.clipboard_append(text)


def human_bytes(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return '%d %s' % (n, unit) if unit == 'B' else '%.1f %s' % (n, unit)
        n /= 1024.0


class JsonView(ttk.Frame):
    """Read-only monospace text with JSON colouring and a search that highlights every match."""

    def __init__(self, master, font=None):
        super().__init__(master)
        self.text = tk.Text(self, wrap='none', undo=False, state='disabled', font=font or mono_font(), bg=TEXT_BG, fg=TEXT_FG,
                            insertbackground=TEXT_FG, relief='flat', borderwidth=0, padx=6, pady=4, highlightthickness=0,
                            selectbackground='#cfe0f7', selectforeground=TEXT_FG)
        ys = ttk.Scrollbar(self, orient='vertical', command=self.text.yview)
        xs = ttk.Scrollbar(self, orient='horizontal', command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.text.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        xs.grid(row=1, column=0, sticky='ew')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        for name, col in JSON_COLORS.items():
            self.text.tag_configure(name, foreground=col)
        self.text.tag_configure('found', background='#ffe58a')
        self.text.tag_configure('current', background='#ffb347')
        self.text.tag_raise('sel')
        self._needle, self._cur = '', None

    def set_text(self, s, highlight=False):
        t = self.text
        t.configure(state='normal')
        t.delete('1.0', 'end')
        t.insert('1.0', s)
        if highlight:
            for ln, line in enumerate(s.split('\n'), 1):
                for m in JSON_TOKEN.finditer(line):
                    t.tag_add(m.lastgroup, '%d.%d' % (ln, m.start()), '%d.%d' % (ln, m.end()))
        t.configure(state='disabled')
        t.tag_remove('found', '1.0', 'end')
        t.tag_remove('current', '1.0', 'end')
        self._cur = None
        t.yview_moveto(0)

    def set_json(self, obj):
        self.set_text(json.dumps(obj, ensure_ascii=False, indent=2), highlight=True)

    def get_text(self):
        return self.text.get('1.0', 'end-1c')

    def find(self, needle, forward=True):
        """Highlight all matches (case-insensitive) and move to the next/previous one (wrapping around).
        A new needle starts at the top.  -> number of matches."""
        t = self.text
        t.tag_remove('found', '1.0', 'end')
        t.tag_remove('current', '1.0', 'end')
        if needle != self._needle:
            self._needle, self._cur = needle, None
        if not needle:
            return 0
        count, idx = 0, '1.0'
        while count < 5000:
            pos = t.search(needle, idx, stopindex='end', nocase=True)
            if not pos:
                break
            idx = '%s+%dc' % (pos, len(needle))
            t.tag_add('found', pos, idx)
            count += 1
        if not count:
            return 0
        if forward:
            pos = t.search(needle, '%s+1c' % self._cur if self._cur else '1.0', stopindex='end', nocase=True) \
                or t.search(needle, '1.0', stopindex='end', nocase=True)
        else:
            pos = t.search(needle, self._cur or 'end', stopindex='1.0', backwards=True, nocase=True) \
                or t.search(needle, 'end', stopindex='1.0', backwards=True, nocase=True)
        if pos:
            t.tag_add('current', pos, '%s+%dc' % (pos, len(needle)))
            t.see(pos)
            self._cur = pos
        return count


class Legend(ttk.Frame):
    """The four mark colours with their meaning (``short``: one word each, for narrow windows)."""

    SHORT = {'auto': 'auto', 'uncertain': 'uncertain', 'watch': 'watch', 'human': 'human'}

    def __init__(self, master, classes=('auto', 'uncertain', 'watch', 'human'), short=False):
        super().__init__(master)
        for c in classes:
            tk.Label(self, text='\u25a0', fg=CLASS_COLORS[c], font=('TkDefaultFont', 14)).pack(side='left')
            ttk.Label(self, text=self.SHORT[c] if short else CLASS_LABELS[c]).pack(side='left', padx=(0, 12))

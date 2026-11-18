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

from PIL import ImageTk

MAC = sys.platform == 'darwin'
MOD = 'Command' if MAC else 'Control'          # key modifier of the shortcuts
MOD_LABEL = 'Cmd' if MAC else 'Ctrl'

# Mark classes (pageindex.CLASSES): one colour each, used by the legend, the page list and, later, the page image.
CLASS_COLORS = {'auto': '#1f6fd1', 'uncertain': '#d62f2f', 'watch': '#e69f00', 'human': '#2a9d4b'}
CLASS_LABELS = {'auto': 'corrected automatically', 'uncertain': 'uncertain: please check',
                'watch': 'watch: probably fine', 'human': 'decided by a human'}
# The whole GUI is dark.  One palette; nothing else in the GUI hard-codes a colour (a test checks that).
BG, PANEL, FIELD, BORDER = '#1e1f22', '#2b2d30', '#26282b', '#43464b'     # window, buttons/headings, entries/lists/text, lines
FG, MUTED, SEL_BG, SEL_FG = '#e4e6e8', '#9ea3a8', '#2f5d9a', '#ffffff'    # text, secondary text, selection
CANVAS_BG = '#141518'                                                      # behind the page image
STATUS_COLORS = {'ok': '#6fcf7f', 'warn': '#f0b040', 'fail': '#ff7b72', 'info': MUTED}
ROW_TINT = {'attention': '#4a2528', 'watch': '#4a3e1c'}                    # rows of the page list: uncertain words / watch only
TREE_BG, TREE_FG, TREE_SEL_BG, TREE_SEL_FG = FIELD, FG, SEL_BG, SEL_FG     # list widgets
TEXT_BG, TEXT_FG = FIELD, FG
NOTE_FG = '#f0b040'                                                        # remarks such as "positions estimated"
FIND_BG, FIND_CUR_BG = '#6b5a12', '#a35d10'                                # search matches in a JSON view
# render window: outlines over the page image
CHECK_COLORS = {'collision': '#ff4d4d', 'overflow': '#ff8c1a', 'drift': '#c792ea', 'blank': '#6cb6ff',
                'loose': '#e6c229', 'tight': '#e6c229'}
BLOCK_OUTLINE, SELECT_OUTLINE, ADJUSTED_OUTLINE = '#5a6f8a', '#33d17a', '#2fa8d6'

JSON_TOKEN = re.compile(r'(?P<key>"(?:[^"\\]|\\.)*"(?=\s*:))|(?P<str>"(?:[^"\\]|\\.)*")'
                        r'|(?P<num>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(?P<lit>\btrue\b|\bfalse\b|\bnull\b)')
JSON_COLORS = {'key': '#6cb6ff', 'str': '#8fd694', 'num': '#f0a860', 'lit': '#c792ea'}


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
                            selectbackground=SEL_BG, selectforeground=SEL_FG)
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
        self.text.tag_configure('found', background=FIND_BG)
        self.text.tag_configure('current', background=FIND_CUR_BG)
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


def apply_dark_theme(root):
    """Dark ttk and Tk styling for the whole application (call once, right after the root window exists).

    The 'clam' theme is used on every platform: the native macOS theme ignores most colour settings, which is how a
    light list with light text came about.  Tk widgets that are created later (Text, Canvas, Toplevel, Listbox and the
    drop-down list of a Combobox) take their colours from the option database set here."""
    st = ttk.Style(root)
    if 'clam' in st.theme_names():
        st.theme_use('clam')
    root.configure(background=BG)
    for pat, val in (('*Background', BG), ('*Foreground', FG), ('*selectBackground', SEL_BG), ('*selectForeground', SEL_FG),
                     ('*insertBackground', FG), ('*highlightBackground', BG), ('*activeBackground', PANEL),
                     ('*activeForeground', FG), ('*disabledForeground', MUTED), ('*Text.background', FIELD),
                     ('*Text.foreground', FG), ('*Canvas.background', CANVAS_BG), ('*Listbox.background', FIELD),
                     ('*TCombobox*Listbox.background', FIELD), ('*TCombobox*Listbox.foreground', FG),
                     ('*TCombobox*Listbox.selectBackground', SEL_BG), ('*TCombobox*Listbox.selectForeground', SEL_FG)):
        root.option_add(pat, val)
    st.configure('.', background=BG, foreground=FG, fieldbackground=FIELD, bordercolor=BORDER, darkcolor=BG,
                 lightcolor=BG, troughcolor=BG, insertcolor=FG, selectbackground=SEL_BG, selectforeground=SEL_FG,
                 focuscolor=BORDER)
    st.map('.', foreground=[('disabled', MUTED)])
    st.configure('TButton', background=PANEL, foreground=FG, bordercolor=BORDER, padding=(10, 3))
    st.map('TButton', background=[('pressed', SEL_BG), ('active', '#383b40'), ('disabled', BG)],
           foreground=[('disabled', MUTED)])
    for w in ('TCheckbutton', 'TRadiobutton'):
        st.configure(w, background=BG, foreground=FG, indicatorbackground=FIELD, indicatorforeground=FG)
        st.map(w, background=[('active', BG)], indicatorbackground=[('selected', SEL_BG), ('pressed', PANEL)])
    st.configure('TEntry', fieldbackground=FIELD, foreground=FG, insertcolor=FG, bordercolor=BORDER)
    st.configure('TCombobox', fieldbackground=FIELD, background=PANEL, foreground=FG, arrowcolor=FG, bordercolor=BORDER,
                 selectbackground=FIELD, selectforeground=FG)
    st.map('TCombobox', fieldbackground=[('readonly', FIELD)], foreground=[('readonly', FG)],
           background=[('active', '#383b40')], selectbackground=[('readonly', FIELD)], selectforeground=[('readonly', FG)])
    st.configure('TNotebook', background=BG, bordercolor=BORDER)
    st.configure('TNotebook.Tab', background=PANEL, foreground=MUTED, padding=(12, 4), bordercolor=BORDER)
    st.map('TNotebook.Tab', background=[('selected', FIELD)], foreground=[('selected', FG)])
    st.configure('Treeview', rowheight=22, background=TREE_BG, fieldbackground=TREE_BG, foreground=TREE_FG, bordercolor=BORDER)
    st.configure('Treeview.Heading', background=PANEL, foreground=FG, bordercolor=BORDER, relief='flat')
    st.map('Treeview', background=[('selected', TREE_SEL_BG)], foreground=[('selected', TREE_SEL_FG)])
    st.map('Treeview.Heading', background=[('active', '#383b40')])
    st.configure('TScrollbar', background=PANEL, troughcolor=BG, arrowcolor=FG, bordercolor=BORDER)
    st.map('TScrollbar', background=[('active', '#4a4d52')])
    st.configure('TPanedwindow', background=BG)
    st.configure('Sash', sashthickness=6, background=BORDER)
    st.configure('TSeparator', background=BORDER)
    st.configure('TLabelframe', background=BG, bordercolor=BORDER)
    st.configure('TLabelframe.Label', background=BG, foreground=FG)
    return st


# ---------------------------------------------------------------- images
# Python may finalise a PhotoImage in *any* thread (cyclic garbage is collected by whichever thread happens to allocate).
# PhotoImage.__del__ calls Tk, and a Tk call from a worker thread while the main thread is busy hangs: the loading thread of
# the main window hung in a test exactly like that.  A SafePhoto freed off the main thread is parked and released by the
# main thread (``release_pending_images``) the next time it draws.
_PENDING_IMAGES = []


class SafePhoto(ImageTk.PhotoImage):
    def __del__(self):
        if threading.current_thread() is threading.main_thread():
            ImageTk.PhotoImage.__del__(self)
        else:
            _PENDING_IMAGES.append(self)             # resurrected: Python calls __del__ only once per object


def release_pending_images():
    """Free the images that were finalised in another thread (call from the main thread)."""
    while _PENDING_IMAGES:
        ImageTk.PhotoImage.__del__(_PENDING_IMAGES.pop())

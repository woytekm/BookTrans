"""The OCR correction window: one separate window per page.

The page image (the deskewed page the pipeline made from the TIFF; the TIFF itself is never touched) is shown fitted
to the window with every mark of the page drawn on it in the colour of its class: blue = corrected automatically,
red = uncertain, amber = watch, green = decided by a human.  Hovering over a mark shows what it is.  Positions are
exact (Tesseract word boxes) for documents of schema 1.1, and estimated for older ones; estimated marks are
drawn as outlines only.  The JSON editor at the bottom and saving arrive in stage C (docs/GUI.md, section 9).
"""
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from gui_widgets import CLASS_COLORS, CLASS_LABELS, STATUS_COLORS

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
        self.geometry('760x940')
        self.minsize(420, 400)
        self._photo = None
        self._img = None
        self._marks, self._hits, self._origin = [], [], (0, 0)
        self.show = {c: tk.BooleanVar(value=True) for c in CLASS_COLORS}
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill='x')
        self.head = ttk.Label(top, text='', font=('TkDefaultFont', 12, 'bold'))
        self.head.pack(side='left')
        self.counts = ttk.Label(top, text='')
        self.counts.pack(side='right')
        bar = ttk.Frame(self)
        bar.pack(fill='x', padx=10)
        for c in ('auto', 'uncertain', 'watch', 'human'):          # the legend doubles as the show/hide switches
            tk.Label(bar, text='\u25a0', fg=CLASS_COLORS[c], font=('TkDefaultFont', 14)).pack(side='left')
            ttk.Checkbutton(bar, text=c, variable=self.show[c], command=self._draw).pack(side='left', padx=(0, 10))
        self.note = ttk.Label(bar, text='', foreground='#8a5a00')
        self.note.pack(side='right')
        self.canvas = tk.Canvas(self, bg='#d9d6cf', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, padx=10, pady=8)
        self.info = ttk.Label(self, text='Hover over a mark.  (The JSON editor comes in stage C.)', foreground='#444444',
                              padding=(10, 0, 10, 8), wraplength=720, justify='left')
        self.info.pack(fill='x')
        self.canvas.bind('<Configure>', lambda _e: self._draw())
        self.canvas.bind('<Motion>', self._hover)
        self.canvas.bind('<Leave>', lambda _e: self.info.configure(text=''))
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.load()

    def load(self):
        """(Re)read the page from the main window's index."""
        ix = self.app.index
        n = self.page_no
        page = ix.page_by_no.get(n) if ix else None
        if page is None:
            self.head.configure(text='Page %d: not in the document any more' % n)
            self.counts.configure(text='')
            self._img = None
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
        path = ix.image_path(n)
        try:
            self._img = Image.open(path).convert('RGB')
            self._error = ''
        except (OSError, AttributeError) as e:
            self._img, self._error = None, 'Cannot open the page image:\n%s\n%s' % (path, e)
        self._draw()

    def _draw(self):
        c = self.canvas
        c.delete('all')
        w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        if self._img is None:
            c.create_text(w // 2, h // 2, text=getattr(self, '_error', '') or 'No image', fill=STATUS_COLORS['fail'], justify='center')
            return
        s = min(w / self._img.width, h / self._img.height)
        size = (max(1, int(self._img.width * s)), max(1, int(self._img.height * s)))
        shown = {k for k, v in self.show.items() if v.get()}
        img, self._hits = compose_marks(self._img, size, self._marks, shown)
        self._photo = ImageTk.PhotoImage(img)
        self._origin = (w // 2 - size[0] // 2, h // 2 - size[1] // 2)
        c.create_image(w // 2, h // 2, image=self._photo)

    def _hover(self, e):
        x, y = e.x - self._origin[0], e.y - self._origin[1]
        found = [m for (x1, y1, x2, y2, m) in self._hits if x1 <= x <= x2 and y1 <= y <= y2]
        self.info.configure(text=describe_mark(found[-1]) if found else '')

    def close(self):
        self.app.correction_closed(self.page_no)
        self.destroy()

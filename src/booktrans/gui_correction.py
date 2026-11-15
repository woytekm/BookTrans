"""The OCR correction window: one separate window per page.

STAGE A (now): the window exists and is managed by the main window (one per page, reused, closed with the project);
it shows the page image fitted to the window and the mark counts.  Marks drawn on the image, the JSON editor at the
bottom and saving arrive in stage C (docs/GUI.md, section 9).
"""
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from gui_widgets import STATUS_COLORS, Legend


class CorrectionWindow(tk.Toplevel):
    def __init__(self, app, page_no):
        super().__init__(app)
        self.app, self.page_no = app, page_no
        self.geometry('760x940')
        self.minsize(420, 400)
        self._photo = None
        self._img = None
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill='x')
        self.head = ttk.Label(top, text='', font=('TkDefaultFont', 12, 'bold'))
        self.head.pack(side='left')
        self.counts = ttk.Label(top, text='')
        self.counts.pack(side='right')
        Legend(self, short=True).pack(fill='x', padx=10)
        self.canvas = tk.Canvas(self, bg='#d9d6cf', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, padx=10, pady=8)
        ttk.Label(self, text='Stage A: page image and counts only. Marks on the image and the JSON editor come in stage C.',
                  foreground='#666666', padding=(10, 0, 10, 8)).pack(fill='x')
        self.canvas.bind('<Configure>', lambda _e: self._draw())
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
        self._photo = ImageTk.PhotoImage(self._img.resize(size, Image.LANCZOS))
        c.create_image(w // 2, h // 2, image=self._photo)

    def close(self):
        self.app.correction_closed(self.page_no)
        self.destroy()

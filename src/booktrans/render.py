#!/usr/bin/env python3
"""Page assembly: rebuild each page from ``document.json`` with translated text.

    python render.py output/0001/document.json translations.json -o output/0001/render [--pages 3,5-7]

Two steps:

1. **Layout** (``layout.py``): where every line of the translation goes: body text through the original
   line slots, kept in step with the pages, size and letter-spacing fitted per page; headings, captions,
   bylines and footnotes in their original blocks.  The result is a plan (``layout.json``).
2. **Drawing** (this file): a page from the plan.  A clean canvas in the page's paper colour
   (``--background scan`` starts from the scanned page and wipes the translated text blocks), figures and
   page furniture pasted at their place, untranslated text kept as original pixels, then the lines.

The layout always covers the whole book (body text flows across pages); ``--pages`` only limits which
pages are drawn.  Writes ``page_NNN_X.png`` (full resolution when the scan was high resolution),
``compare.png`` (original | reconstruction), ``layout.json`` and ``report.json``.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import log  # noqa: E402
from typeset import (Fonts, typeset, pieces_of, break_lines, line_words, text_of,  # noqa: E402,F401
                     SINGLE_LETTER, RMIN, RMAX, _HYPH)
from layout import (make_plan, fit_track, calibrated_size, block_style, anchor_of,  # noqa: E402,F401
                    FIT_UP, FIT_MAX_EM, DEFAULTS)

INK = (34, 30, 26)


# ---------------------------------------------------------------- drawing
def draw_line(draw, line, font, S, ink, hyph_char='-', fonts=None):
    """Draw one plan line at scale ``S`` (working px -> output px) with ``font`` loaded at ``line['size'] * S``;
    ``fonts`` (a ``Fonts``) for words in another style (``<i>``, ``<b>``)."""
    cx = line['x'] * S
    base = line['baseline'] * S
    words = line['words']
    for i, e in enumerate(words):
        t, w = e[0], e[1]
        meta = e[2] if len(e) > 2 else {}
        f = fonts.get(meta['style'], line['size'] * S) if meta.get('style') and fonts else font
        tr = meta.get('track', line.get('track', 0.0)) * S
        if tr > 0:
            for k, ch in enumerate(t):
                draw.text((cx + f.getlength(t[:k]) + tr * k, base), ch, font=f, fill=ink, anchor='ls')
        else:
            draw.text((cx, base), t, font=f, fill=ink, anchor='ls')
        cx += w * S
        if i < len(words) - 1 and not meta.get('join'):
            cx += line['gap'] * S
    if line.get('hyphen'):
        draw.text((cx, base), hyph_char, font=font, fill=ink, anchor='ls')


# ---------------------------------------------------------------- background
def wipe(arr, box, S):
    """Image of ``box`` with the text removed: the local paper tone (max filter + blur of the
    region and its surround) so shading and the gutter gradient continue under the new text."""
    import cv2
    h, w = arr.shape[:2]
    x1, y1, x2, y2 = max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])
    m = int(round(12 * S))
    X1, Y1, X2, Y2 = max(0, x1 - m), max(0, y1 - m), min(w, x2 + m), min(h, y2 + m)
    ctx = arr[Y1:Y2, X1:X2]
    k = int(round(9 * S)) | 1
    bg = cv2.dilate(ctx, np.ones((k, k), np.uint8))
    bg = cv2.GaussianBlur(bg, (0, 0), max(1.0, 5 * S)).astype(np.float32)
    inner = (slice(y1 - Y1, y2 - Y1), slice(x1 - X1, x2 - X1))
    ring = np.ones(ctx.shape[:2], bool); ring[inner] = False
    if ring.any():          # the max filter is brighter than real paper: match the surrounding tone
        gain = np.median(ctx[ring], axis=0) / np.maximum(np.median(bg[inner].reshape(-1, 3), axis=0), 1.0)
        bg *= gain
    return Image.fromarray(np.clip(bg[inner], 0, 255).astype(np.uint8))




def _page_images(doc, page, background, resolution):
    base = Path(doc.get('_dir', '.'))
    f = float(page.get('scale') or 1.0)
    use_full = page.get('image_full') and resolution != 'work'
    S = f if use_full else 1.0
    orig = Image.open(base / (page['image_full'] if use_full else page['image'])).convert('RGB')
    paper = page['layout'].get('paper_color_rgb')
    if not paper:                                   # older/synthetic documents: the bright end of the page
        paper = np.percentile(np.asarray(orig.convert('RGB')).reshape(-1, 3), 90, axis=0).astype(int).tolist()
    canvas = orig.copy() if background == 'scan' else Image.new('RGB', orig.size, tuple(paper))
    return canvas, orig, S


def draw_page(doc, plan, pi, fonts=None, background='paper', ink=None, resolution='auto'):
    """Page ``pi`` (index into ``doc['pages']``) drawn from the layout plan: (rebuilt image, original image).
    ``ink``: default the plan's (``render`` stores the measured one) or ``INK``."""
    ink = tuple(ink or plan.get('ink') or INK)
    fonts = fonts or Fonts(plan['fonts']['regular'], plan['fonts']['bold'], plan['fonts']['italic'])
    base = Path(doc.get('_dir', '.'))
    page, pp = doc['pages'][pi], plan['pages'][pi]
    canvas, orig, S = _page_images(doc, page, background, resolution)
    blocks = {b['block_id']: b for b in page['blocks']}
    wipe_ids = set(pp.get('wipe', []))
    for b in page['blocks']:
        if background == 'scan' and b['block_id'] in wipe_ids:
            x1, y1, x2, y2 = [int(round(v * S)) for v in (b['bbox'][0] - 3, b['bbox'][1] - 3, b['bbox'][2] + 3, b['bbox'][3] + 3)]
            canvas.paste(wipe(np.asarray(canvas), (x1, y1, x2, y2), S), (max(0, x1), max(0, y1)))
        if b['kind'] in ('figure', 'graphic'):
            img = Image.open(base / b['file']).convert('RGB')
            x1, y1, x2, y2 = b['crop_bbox']
            size = (int(round((x2 - x1) * S)), int(round((y2 - y1) * S)))
            if img.size != size:
                img = img.resize(size, Image.LANCZOS)
            pos = (int(round(x1 * S)), int(round(y1 * S)))
            if b['kind'] == 'graphic':
                # page numbers, rules: keep only the ink, not the scan's paper tone
                arr = np.asarray(img).astype(np.float32)
                paper_c = np.percentile(arr.reshape(-1, 3), 90, axis=0)
                dark = np.clip(1.0 - arr / np.maximum(paper_c, 1.0), 0.0, 1.0)
                ratio = 1.0 - np.clip((dark - 0.06) / 0.94, 0.0, 1.0)      # drop paper noise, keep the ink
                region = np.asarray(canvas.crop((pos[0], pos[1], pos[0] + img.width, pos[1] + img.height))).astype(np.float32)
                canvas.paste(Image.fromarray((region * ratio).astype(np.uint8)), pos)
            else:
                canvas.paste(img, pos)
    for bid in pp.get('keep', []):                  # untranslated text: the original pixels
        x1, y1, x2, y2 = [int(round(v * S)) for v in blocks[bid]['bbox']]
        pad = int(round(2 * S))
        box = (max(0, x1 - pad), max(0, y1 - pad), x2 + pad, y2 + pad)
        canvas.paste(orig.crop(box), box[:2])
    draw = ImageDraw.Draw(canvas)
    for line in pp['lines']:
        draw_line(draw, line, fonts.get(line['style'], line['size'] * S), S, ink, fonts=fonts)
    return canvas, orig


def measure_ink(doc, max_pages=12):
    """The hue of the printed text, from the scan: inside the text blocks of up to ``max_pages`` pages, the median
    colour of the pixels clearly darker than the paper (below 0.6 x its brightness).  Scanners clip the stroke cores
    to black, so the median (edges and cores) is the colour the eye sees.  (R, G, B) or None when there is no text.
    ``render`` with ``ink='auto'`` uses ``typecolour.match_ink`` instead, which also matches the darkness."""
    base = Path(doc.get('_dir', '.'))
    pages = [p for p in doc['pages'] if any(b['kind'] == 'text' for b in p['blocks'])]
    if not pages:
        return None
    step = max(1, len(pages) // max_pages)
    samples = []
    for page in pages[::step][:max_pages]:
        try:
            arr = np.asarray(Image.open(base / page['image']).convert('RGB')).astype(np.float32)
        except OSError:
            continue
        for b in page['blocks']:
            if b['kind'] != 'text':
                continue
            x1, y1, x2, y2 = [int(round(v)) for v in b['bbox']]
            reg = arr[max(0, y1):y2, max(0, x1):x2].reshape(-1, 3)
            if len(reg) < 50:
                continue
            lum = reg.mean(axis=1)
            dark = reg[lum < 0.6 * np.percentile(lum, 90)]
            if len(dark):
                samples.append(dark)
    if not samples:
        return None
    return tuple(int(round(v)) for v in np.median(np.concatenate(samples), axis=0))


WORK_DPI = 120.0                 # resolution of the working page images (the 600 DPI originals at scale ~5)


class PdfWriter:
    """A book PDF written page by page (each page a JPEG; nothing is kept in memory).  Page size from the image
    and ``dpi``, so a printout has the original's physical size when the DPI is right."""

    def __init__(self, path, quality=90):
        self.f = open(path, 'wb')
        self.quality = quality
        self.offsets = {}
        self.kids = []
        self.next = 3                                 # 1: catalog, 2: page tree (written last)
        self.f.write(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')

    def _obj(self, num, body, stream=None):
        self.offsets[num] = self.f.tell()
        self.f.write(b'%d 0 obj\n' % num + body)
        if stream is not None:
            self.f.write(b'\nstream\n' + stream + b'\nendstream')
        self.f.write(b'\nendobj\n')

    def add(self, img, dpi):
        import io
        buf = io.BytesIO()
        img.convert('RGB').save(buf, 'JPEG', quality=self.quality, dpi=(dpi, dpi))
        data = buf.getvalue()
        w_pt, h_pt = img.width * 72.0 / dpi, img.height * 72.0 / dpi
        page, content, image = self.next, self.next + 1, self.next + 2
        self.next += 3
        self._obj(image, b'<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB '
                         b'/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>' % (img.width, img.height, len(data)), data)
        cs = b'q %.3f 0 0 %.3f 0 0 cm /Im0 Do Q' % (w_pt, h_pt)
        self._obj(content, b'<< /Length %d >>' % len(cs), cs)
        self._obj(page, b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.3f %.3f] /Contents %d 0 R '
                        b'/Resources << /XObject << /Im0 %d 0 R >> >> >>' % (w_pt, h_pt, content, image))
        self.kids.append(page)

    def close(self):
        self._obj(2, b'<< /Type /Pages /Kids [%s] /Count %d >>' % (b' '.join(b'%d 0 R' % k for k in self.kids), len(self.kids)))
        self._obj(1, b'<< /Type /Catalog /Pages 2 0 R >>')
        xref = self.f.tell()
        n = self.next
        self.f.write(b'xref\n0 %d\n0000000000 65535 f \n' % n)
        for i in range(1, n):
            self.f.write(b'%010d 00000 n \n' % self.offsets.get(i, 0))
        self.f.write(b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (n, xref))
        self.f.close()


def compare_strip(images, H=1100):
    tiles = [im.resize((int(im.width * H / im.height), H), Image.LANCZOS) for im in images]
    comp = Image.new('RGB', (sum(t.width for t in tiles) + 10 * (len(tiles) - 1), H), (90, 90, 90))
    x = 0
    for t in tiles:
        comp.paste(t, (x, 0)); x += t.width + 10
    return comp


def parse_pages(spec, doc):
    """'3,5-7' (printed page numbers as in document.json) -> page indexes; None -> all."""
    if not spec:
        return list(range(len(doc['pages'])))
    want = set()
    for part in str(spec).split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1); want.update(range(int(a), int(b) + 1))
        else:
            want.add(int(part))
    return [i for i, p in enumerate(doc['pages']) if p['page'] in want]


def render(doc, trans, out_dir, fonts, background='paper', ink='auto', resolution='auto', para_indent_em=None,
           tracking=None, size_scale=1.0, word_space=1.0, pages=None, settings=None, pdf=None, pdf_dpi=None):
    """Lay out the whole book, draw the pages ``pages`` (indexes; default all) into ``out_dir``.

    ``tracking``: None = fit the letter-spacing so the translation fills the original line widths; a number =
    fixed letter-spacing in em (0 = none).  ``size_scale`` and ``word_space`` multiply the chosen font size and
    the natural word space.  ``ink``: (R, G, B) or 'auto' (``typecolour.match_ink``: the scan's ink hue at the shade
    where the rebuilt text is as dark as the scan).  ``settings``
    (a dict, see ``layout.DEFAULTS``) overrides these.  ``pdf``: a file name in ``out_dir`` for the drawn pages as one
    PDF, at ``pdf_dpi`` (default: ``WORK_DPI`` x the drawing scale).
    Returns (file names, report)."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    st = {'tracking': tracking, 'size_scale': size_scale, 'word_space': word_space, 'paragraph_indent_em': para_indent_em}
    st.update(settings or {})
    plan = make_plan(doc, trans, fonts, st)
    report = plan['report']
    if ink is None or ink == 'auto':
        from typecolour import match_ink
        ink, report['ink_match'] = match_ink(doc, fonts)
        ink = ink or measure_ink(doc) or INK
    report['ink'] = plan['ink'] = list(ink)
    idx = list(range(len(doc['pages']))) if pages is None else list(pages)
    names, tiles = [], []
    many = len(idx) > 4
    pdf_w = PdfWriter(out_dir / pdf) if pdf else None
    for pi in idx:
        page = doc['pages'][pi]
        canvas, orig = draw_page(doc, plan, pi, fonts, background, ink, resolution)
        name = 'page_%03d_%s.png' % (page['page'], page.get('side', ''))
        canvas.save(out_dir / name); names.append(name)
        if pdf_w:
            S = float(page.get('scale') or 1.0) if (page.get('image_full') and resolution != 'work') else 1.0
            pdf_w.add(canvas, pdf_dpi or WORK_DPI * S)
        if many:                                      # a whole book: one comparison per page, written at once
            compare_strip([orig, canvas]).save(out_dir / ('compare_page_%03d.png' % page['page']))
        else:                                         # keep small copies only (full-resolution pages are large)
            tiles += [im.resize((int(im.width * 1100 / im.height), 1100), Image.LANCZOS) for im in (orig, canvas)]
        del canvas, orig
    if tiles:
        compare_strip(tiles).save(out_dir / 'compare.png')
    if pdf_w:
        pdf_w.close()
        report['pdf'] = pdf
    (out_dir / 'layout.json').write_text(json.dumps({k: v for k, v in plan.items() if k != 'report'}, ensure_ascii=False),
                                         encoding='utf-8')
    (out_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return names, report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('document', type=Path, help='document.json written by booktrans.py')
    ap.add_argument('translations', type=Path, help='JSON with {"units": {unit_id: {"pl": text}}}')
    ap.add_argument('-o', '--output', type=Path, default=None, help='default: <document dir>/render')
    ap.add_argument('--font', type=Path, help='regular serif TTF with Polish letters, or path@weight for a variable font (default: config/fonts.json, Crimson Text)')
    ap.add_argument('--bold-font', type=Path)
    ap.add_argument('--italic-font', type=Path)
    ap.add_argument('--background', choices=['paper', 'scan'], default='paper',
                    help='paper: clean canvas (default); scan: the scanned page with text blocks wiped')
    ap.add_argument('--resolution', choices=['auto', 'work'], default='auto',
                    help='auto: full resolution when the scan has it; work: analysis scale')
    ap.add_argument('--tracking', default='auto',
                    help="letter-spacing: 'auto' (default) fits it per block so the translation fills the original "
                         "line widths; a number in em (e.g. 0.05) fixes it; 0 = none")
    ap.add_argument('--size-scale', type=float, default=1.0, help='multiply the chosen font size (default 1.0)')
    ap.add_argument('--word-space', type=float, default=1.0, help='multiply the natural word space (default 1.0)')
    ap.add_argument('--ink', default='auto', help="text colour R,G,B, or 'auto' (default): the scan's ink, shaded so the text is as dark as on the scan")
    ap.add_argument('--paragraph-indent', type=float, default=None,
                    help='first-line indent in em (default: as in the original page, normally none)')
    ap.add_argument('--pages', help='draw only these pages (printed numbers, e.g. 3,5-7); the layout still covers the book')
    ap.add_argument('--overrides', type=Path,
                    help="human adjustments of blocks and pages (a project's review/render_overrides.json, see render_overrides.py)")
    ap.add_argument('--pdf', nargs='?', const='book.pdf', help='also write the drawn pages as one PDF (default name book.pdf)')
    ap.add_argument('--pdf-dpi', type=float, help='PDF resolution (default %g x the drawing scale: the physical size of the 600 DPI scans)' % WORK_DPI)
    ap.add_argument('--no-page-fit', action='store_true',
                    help='one body size for the whole book (shrunk only when a page cannot hold its text)')
    a = ap.parse_args()
    doc = json.loads(a.document.read_text(encoding='utf-8'))
    doc['_dir'] = str(a.document.parent)
    trans = json.loads(a.translations.read_text(encoding='utf-8'))
    out = a.output or a.document.parent / 'render'
    ink = 'auto' if a.ink == 'auto' else tuple(int(v) for v in a.ink.split(','))
    log.info('render', 'typesetting %d pages with %d translated units' % (len(doc['pages']), len(trans['units'])))
    if _HYPH is None:
        log.info('render', 'pyphen not installed: no hyphenation (pip install pyphen)')
    settings = {'page_fit': not a.no_page_fit}
    stale = []
    if a.overrides:
        import render_overrides
        settings['blocks'], settings['pages'], stale = render_overrides.resolve(doc, render_overrides.load(a.overrides))
        log.info('render', 'overrides: %d block(s), %d page(s) adjusted by hand' % (len(settings['blocks']), len(settings['pages'])))
    names, rep = render(doc, trans, out, Fonts(a.font, a.bold_font, a.italic_font), a.background, ink, a.resolution, a.paragraph_indent,
                        None if a.tracking == 'auto' else float(a.tracking), a.size_scale, a.word_space,
                        pages=parse_pages(a.pages, doc), settings=settings, pdf=a.pdf, pdf_dpi=a.pdf_dpi)
    for s in stale:
        log.info('render', 'WARNING stale override (not applied): ' + s)
    for r in rep['units']:
        log.info('render', '%s %s: %d lines (original %d), size %.1f px, tracking %.3f em' % (r['unit'], r['kind'], r['lines'], r['lines_original'], r['size_px'], r['tracking_em']))
    for r in rep['pages']:
        log.info('render', 'page %d body: %d of %d lines used, size %.1f px, tracking %.3f em' % (
            r['page'], r['body_lines_used'], r['body_lines_capacity'], r['size_px'], r['tracking_em']))
    for w in rep['warnings']:
        log.info('render', 'WARNING ' + w)
    kinds = {}
    for c in rep.get('checks', []):
        kinds[c['kind']] = kinds.get(c['kind'], 0) + 1
    log.info('render', 'ink %s' % ','.join(str(v) for v in rep['ink']))
    log.info('render', 'checks: %s' % (', '.join('%s %d' % kv for kv in sorted(kinds.items())) or 'none'))
    log.info('render', 'wrote %s' % ', '.join(names) + ', compare, layout.json, report.json in %s' % out)


if __name__ == '__main__':
    main()

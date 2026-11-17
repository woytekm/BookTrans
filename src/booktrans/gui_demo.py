#!/usr/bin/env python3
"""A small synthetic project to try the GUI without scans, Tesseract or a pipeline run.

    python gui_demo.py /tmp/demo          # writes the project; then:  python gui.py /tmp/demo
    python gui.py --demo /tmp/demo        # both in one go

Three pages (two spreads) with typical OCR slips from the handoff (``Binfassung``, ``Finzelheiten``,
``nichtmehr``, ``vesten``, ``z. B.in``, ``O8``), a paragraph that continues over a page break with a hyphen
(``Spitz-`` / ``bogen``), a caption, a footnote, a figure and a text label inside the figure.

The unit texts are built by ``stage4.build_units`` and corrected by ``ocrfix.run`` (a small lexicon made from
the text itself instead of the downloaded dictionaries), so the marks come from the real code, not from a mock.
Needs ``symspellpy`` (for the correction); without it only ``document.json`` is written.
"""
import copy
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import stage4                                  # noqa: E402
from project import Project, write_json        # noqa: E402

PAGE_W, PAGE_H = 925, 1315
LEFT, RIGHT = 90, 835
PITCH, GLYPH = 27, 15
PAPER = (240, 233, 218)

# (page, side, tiff): the OCR text as the pipeline would have produced it, errors included.
# A block: (id, role, lines, status).  Lines of a block are one paragraph; a trailing "-" continues the unit.
PAGES = [
    (1, 'L', 'demo_0001.tif', [
        ('s1-h1', 'heading', ['MITTELALTERLICHE KIRCHENPORTALE', 'OBERSCHLESTEN'], 'translate'),
        ('s1-p1', 'paragraph', [
            'Die Portale der Kirchen Niederschlesten bilden eine',
            'wichtige Gruppe innerhalb der Baukunst. Die Binfassung',
            'der Portale ist im wesentlihen erhalten, doch sind die',
            'Finzelheiten des ursprünglichen Zustandes nichtmehr',
            'genau zu bestimmen. Besonders der Sockel im vesten',
            'ist stark entstellt; die Ecken sind abgeschlagen.'], 'translate'),
        ('s1-fig1', 'figure', [], 'exclude'),
        ('s1-c1', 'caption', ['37 PITSCHEN O8'], 'translate'),
        ('s1-p2', 'paragraph', [
            'Das Portal der Kirche in Pitschen besitzt einen reich',
            'gegliederten Spitz-'], 'translate'),
    ]),
    (2, 'R', 'demo_0001.tif', [
        ('s1-p3', 'paragraph', [
            'bogen mit Wulsten und qwlstig verkröpften Pfeilern. Man',
            'erkennt z. B.in der Mitte ein Tympanon aus Sandstein.'], 'translate'),
        ('s1-p4', 'paragraph', [
            'Der Archivolte fehlen die Kapitelle; der Kämpfer ist',
            'durch eine Platte ersetzt. Fin Wulst umzieht das Feld,',
            'dessen Profil nur noch schwer zu erkennen ist.'], 'translate'),
        ('s1-f1', 'footnote_or_footer', ['1) Vgl. Lutsch, Verzeichnis der Kunstdenkmäler der Provinz Schlesien.'], 'translate'),
    ]),
    (3, 'L', 'demo_0002.tif', [
        ('s2-p1', 'paragraph', [
            'Auch das Südportal der Kirche zu Friedland OS ist in',
            'Bruchstelnen ausgeführt. Die Fenster sind nicht mehr',
            'ursprünglich, die Türen wurden im Jahre 1862 erneuert.'], 'translate'),
        ('s2-fig1', 'figure', [], 'exclude'),
        ('s2-l1', 'illustration_text', ['A-B'], 'exclude'),
    ]),
]
NAMES = ['Niederschlesien', 'Oberschlesien', 'Pitschen', 'Friedland', 'OS', 'Lutsch', 'Schlesien']
# correct words the demo text needs but the text itself shows only misspelled or hyphenated
EXTRA = ['Einfassung', 'wesentlichen', 'Einzelheiten', 'nicht', 'mehr', 'Spitzbogen', 'Kirchenportale', 'Wulst', 'Wulsten',
         'verkröpften', 'wulstig', 'Bruchsteinen', 'besten', 'westen', 'festen', 'ein', 'Ein', 'fin', 'Ecken', 'Fenster']


def _tokens(text):
    return re.findall(r"[^\W\d_]+", text, re.U)


def _lexicon_words():
    """All words of the demo text except the deliberately wrong ones, plus the right forms."""
    wrong = {'Niederschlesten', 'Binfassung', 'wesentlihen', 'Finzelheiten', 'nichtmehr', 'vesten', 'qwlstig', 'Bruchstelnen',
             'OBERSCHLESTEN', 'Fin', 'Archivolte'}       # Archivolte: a real word the lexicon does not know -> low-severity flag
    words = set(EXTRA)
    for _, _, _, blocks in PAGES:
        for _, role, lines, _ in blocks:
            for ln in lines:
                words.update(t for t in _tokens(ln.rstrip('-')) if t not in wrong)
    words.add('z')
    return sorted(words, key=str.lower)


# ------------------------------------------------------------------ page images
def _font(size):
    from PIL import ImageFont
    f = HERE.parent.parent / 'fonts' / 'CrimsonText-Regular.ttf'
    try:
        return ImageFont.truetype(str(f), size)
    except OSError:
        return ImageFont.load_default()


def _draw_line(draw, font, y, text, justify):
    """Draw one line from LEFT; justified lines are spread to RIGHT.  -> (bbox, word boxes)."""
    words = text.split(' ')
    widths = [draw.textlength(w, font=font) for w in words]
    space = draw.textlength(' ', font=font)
    if justify and len(words) > 1:
        space = max(space, (RIGHT - LEFT - sum(widths)) / (len(words) - 1))
    x = LEFT
    boxes = []
    for w, wd in zip(words, widths):
        draw.text((x, y), w, font=font, fill=(25, 22, 18))
        boxes.append({'text': w, 'bbox': [int(round(x)), y + 2, int(round(x + wd)), y + 2 + GLYPH], 'conf': 90.0})
        x += wd + space
    return [LEFT, y + 2, int(round(x - space)), y + 2 + GLYPH], boxes


def _build_page(spec, out):
    """Draw the page, return the document.json page entry (blocks with lines, typography) and unit regions."""
    from PIL import Image, ImageDraw
    n, side, tiff, blocks = spec
    img = Image.new('RGB', (PAGE_W, PAGE_H), PAPER)
    d = ImageDraw.Draw(img)
    body, small = _font(22), _font(17)
    y = 120
    out_blocks, regions = [], []
    figs = 0
    for k, (bid, role, lines, status) in enumerate(blocks, 1):
        if role == 'figure':
            figs += 1
            h = 300
            box = [LEFT + 40, y, RIGHT - 40, y + h]
            d.rectangle(box, fill=(188, 182, 170), outline=(60, 55, 50), width=2)
            d.line([box[0], box[1], box[2], box[3]], fill=(120, 115, 108), width=3)
            fn = 'figures/demo_page_%02d_fig_%02d.png' % (n, figs)
            (out / 'figures').mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (box[2] - box[0], h), (188, 182, 170)).save(str(out / fn))
            out_blocks.append({'block_id': bid, 'kind': 'figure', 'role': 'illustration', 'bbox': box, 'crop_bbox': box,
                               'file': fn, 'detector': 'tonal', 'captions': [], 'reading_order': k})
            y += h + 22
            continue
        if role == 'illustration_text':
            d.text((LEFT + 200, y - 160), lines[0], font=small, fill=(40, 40, 40))
            out_blocks.append({'block_id': bid, 'kind': 'text', 'role': 'paragraph', 'bbox': [LEFT + 200, y - 162, LEFT + 240, y - 140],
                               'text': lines[0], 'line_count': 1, 'translation_status': 'exclude', 'content_context': 'illustration_text',
                               'typography': {'glyph_height_px': 12.0}, 'reading_order': k,
                               'lines': [{'bbox': [LEFT + 200, y - 162, LEFT + 240, y - 140], 'text': lines[0], 'confidence': 55.0,
                                          'glyph_height_px': 12.0, 'paragraph_start': True}]})
            continue
        is_caption, is_head, is_foot = role == 'caption', role == 'heading', role == 'footnote_or_footer'
        font = small if (is_caption or is_foot) else body
        if is_foot:
            y = max(y, PAGE_H - 150)
        ln_out, top = [], y
        for i, t in enumerate(lines):
            last = i == len(lines) - 1
            bb, wb = _draw_line(d, font, y, t, justify=not (last or is_head or is_caption or is_foot))
            ln_out.append({'bbox': bb, 'text': t, 'confidence': round(96.0 - (i * 3.7) % 31, 1), 'glyph_height_px': float(GLYPH),
                           'paragraph_start': i == 0, 'words': wb})
            y += PITCH
        out_blocks.append({'block_id': bid, 'kind': 'text', 'role': 'heading' if is_head else role,
                           'bbox': [LEFT, top, RIGHT, y], 'text': ' '.join(lines), 'line_count': len(lines),
                           'translation_status': status, 'content_context': 'caption' if is_caption else 'page_text',
                           'typography': {'glyph_height_px': float(GLYPH), 'line_pitch_px': float(PITCH), 'alignment': 'justified'},
                           'reading_order': k, 'lines': ln_out})
        regions.append({'object_id': bid, 'type': 'paragraph' if role == 'paragraph' else ('heading' if is_head else role),
                        'translation_status': status, 'bbox': [LEFT, top, RIGHT, y], 'lines': [{'text': t} for t in lines],
                        'text': ' '.join(lines)})
        y += PITCH if is_head else PITCH // 2
    d.text((PAGE_W // 2 - 10, PAGE_H - 70), str(50 + n), font=small, fill=(70, 66, 60))
    out_blocks.append({'block_id': 'mark-%d' % n, 'kind': 'graphic', 'role': 'page_mark', 'bbox': [PAGE_W // 2 - 10, PAGE_H - 72, PAGE_W // 2 + 20, PAGE_H - 50],
                       'crop_bbox': [PAGE_W // 2 - 10, PAGE_H - 72, PAGE_W // 2 + 20, PAGE_H - 50], 'file': 'figures/demo_mark_%d.png' % n,
                       'reading_order': len(out_blocks) + 1})
    (out / 'pages').mkdir(parents=True, exist_ok=True)
    rel = 'pages/%s_page_%02d_%s.png' % (Path(tiff).stem, n, side)
    img.save(str(out / rel))
    page = {'page': n, 'side': side, 'image': rel, 'width': PAGE_W, 'height': PAGE_H, 'scale': 1.0, 'image_full': None,
            'source_tiff': tiff, 'spread': 1 if tiff.endswith('0001.tif') else 2, 'page_in_spread': 1 if side == 'L' else 2,
            'skew_deg': 0.0, 'layout': {'columns': [{'left': LEFT, 'right': RIGHT}], 'default_alignment': 'justified'},
            'blocks': out_blocks}
    return page, regions, tiff


def make_demo(root, force=False):
    """Create the demo project in ``root`` (an empty or missing folder).  -> Project."""
    from PIL import Image
    root = Path(root)
    if root.exists() and any(root.iterdir()):
        if not force:
            raise SystemExit('%s is not empty (use --force to replace it)' % root)
        shutil.rmtree(str(root))
    scans = root / 'scans'
    scans.mkdir(parents=True)
    for t in ('demo_0001.tif', 'demo_0002.tif'):
        Image.new('L', (240, 160), 230).save(str(scans / t))
    proj = Project.create(root, 'demo', scans, {'ocrfix': 'strict'})
    book = proj.pipeline_root / 'demo'
    (book / 'figures').mkdir(parents=True)
    pages, s3_pages = [], []
    for spec in PAGES:
        pg, regions, tiff = _build_page(spec, book)
        for fn in {b['file'] for b in pg['blocks'] if b['kind'] == 'graphic'}:
            Image.new('RGB', (30, 22), (90, 86, 80)).save(str(book / fn))
        pages.append(pg)
        s3_pages.append({'page': pg['page'], 'input_index': pg['spread'], 'regions': regions})
    # units exactly as stage 4 makes them (a paragraph that ends in a hyphen continues on the next page)
    stage4.link_pages(s3_pages)
    units = []
    for u in stage4.build_units(s3_pages):
        units.append({'unit_id': u['unit_id'], 'type': u['type'], 'text': u['text'], 'dehyphenated': u['dehyphenated'],
                      'joins': u['joins'], 'source_block_ids': u['source_object_ids']})
    unit_of = {b: u['unit_id'] for u in units for b in u['source_block_ids']}
    for pg in pages:
        for b in pg['blocks']:
            if b['block_id'] in unit_of:
                b['unit_id'] = unit_of[b['block_id']]
    doc = {'schema_version': '1.1', 'source_file': str(scans), 'pages': pages, 'units': units,
           'coordinate_system': 'pixels of each page image (synthetic demo)'}
    write_json(book / 'document.json', doc, indent=2)
    try:
        _correct(doc, book)
    except ImportError as e:
        print('demo: OCR correction skipped (%s); only document.json was written' % e)
    return proj


def _correct(doc, book):
    """ocrfix.run with a lexicon made from the demo text; writes the same three files as ``ocrfix.py run``."""
    import ocrfix
    freq = {w.lower(): 2000000 - 1000 * i for i, w in enumerate(_lexicon_words())}
    freq['fin'] = 3000          # a rare real word that is an OCR slip of the very common "ein": ocrfix flags it as "suspect"
    lex = ocrfix.Lexicon(dict_dir='/nonexistent', vocab=(), use_hunspell=False,
                         freq_lines=['%s %d' % kv for kv in freq.items()])
    for nm in NAMES:
        lex.add_name(nm)
    fixer = ocrfix.Fixer(lex, suggest=False)
    raw = copy.deepcopy(doc)
    cors, flags = ocrfix.run(doc, fixer, {})
    # one flag is resolved by a "human" and ocrfix run again, as after a real review round (-> mark class "human")
    res = {f['key']: 'Bruchsteinen' for f in flags if f['token'] == 'Bruchstelnen'}
    if res:
        doc = copy.deepcopy(raw)
        cors, flags = ocrfix.run(doc, fixer, res)
    doc['ocr_correction'] = {'version': 1, 'corrections': len(cors), 'flags': len(flags), 'mode': 'strict', 'hunspell': False,
                             'review_resolutions_applied': len(res), 'demo': True}
    write_json(book / 'document.corrected.json', doc, indent=1)
    write_json(book / 'ocr_corrections.json', {'schema': '1.0', 'corrections': cors})
    write_json(book / 'ocr_review.json', {'schema': '1.0', 'accepted_words': [],
                                          'resolved': [{'key': k, 'token': k.split('|')[1], 'resolution': v} for k, v in sorted(res.items())],
                                          'flags': flags, 'note': 'demo data'})


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('folder')
    ap.add_argument('--force', action='store_true', help='replace the folder if it exists')
    a = ap.parse_args()
    p = make_demo(a.folder, a.force)
    print(p.root)


if __name__ == '__main__':
    main()

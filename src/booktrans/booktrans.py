#!/usr/bin/env python3
"""Run every analysis stage on one TIFF and write a self-contained result folder.

    python booktrans.py 0001.tif [-o output_root] [--lang deu] [--ocr-scale 2.0]

creates ``<output_root>/<tiff name>/``:

    document.json   nested document model (pages -> blocks -> lines, layout,
                    typography, figure links) + translation units
    pages/          deskewed page images (all coordinates refer to these)
    figures/        every detected illustration cropped to its own PNG
    debug/          diagnostic overlays
    stages/         raw JSON of stages 1-4 (s1..s4), for debugging
    document.corrected.json   copy of document.json with OCR-corrected unit texts (stage 5, ocrfix.py;
                    skipped when symspellpy or the dictionaries are missing, or with --no-ocrfix)
    ocr_corrections.json      every automatic OCR correction (before, after, rule, cost)
    ocr_review.json           words flagged for a human; ``ocrreview.py`` asks an LLM about them

Several TIFFs (or a folder of them) are processed as one book: ``python booktrans.py scans/ -o out``
writes ``<out>/<folder name>/`` with the same layout but one ``document.json`` for all spreads (pages
numbered consecutively, paragraphs joined across spread boundaries, one list of translation units),
and the per-spread stage files in ``stages/<tiff name>/``. ``--resume`` skips spreads whose stages 1-3
are already there; ``--separate`` keeps one folder per TIFF instead.

The input may be an open-book spread (default, split at the gutter), a single
whole page (``--layout page``) or a crop of a page (``--layout crop``).
"""
import argparse
import os
import shutil
import subprocess
import sys
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from docmodel import build_document   # noqa: E402
import log                             # noqa: E402


def run(cmd):
    # stderr is inherited, so the stages' progress lines appear live; stdout (a JSON
    # summary) is only shown when a stage fails.
    r = subprocess.run([sys.executable] + [str(c) for c in cmd], stdout=subprocess.PIPE, text=True)
    if r.returncode:
        sys.stderr.write(r.stdout[-2000:])
        raise SystemExit('stage failed (exit %d): %s' % (r.returncode, Path(str(cmd[0])).name))


def ocr_correct(out, suggest=False):
    """Stage 5: offline OCR correction of the units (ocrfix.py) -> document.corrected.json, ocr_corrections.json,
    ocr_review.json next to document.json.  Skipped (with the reason) when its requirements are missing."""
    import importlib.util
    if importlib.util.find_spec('symspellpy') is None:
        log.info('wrap', 'OCR correction skipped: pip install spylls symspellpy')
        return False
    if not (HERE.parent.parent / 'dictionaries' / 'de-100k.txt').exists():
        log.info('wrap', 'OCR correction skipped: dictionaries missing, run "python ocrfix.py fetch" once')
        return False
    log.info('wrap', '=== stage 5: OCR correction (ocrfix.py) ===')
    run([HERE / 'ocrfix.py', 'run', out / 'document.json'] + ([] if suggest else ['--no-suggest']))
    return True


def step(n, title):
    log.info('wrap', '=== stage %d/4: %s ===' % (n, title))


def process(tiff, out_root, lang='deu', ocr_scale=2.0, scale='auto', layout='spread'):
    tiff = Path(tiff)
    out = Path(out_root) / tiff.stem
    if out.exists():
        shutil.rmtree(out)
    st = out / 'stages'
    s1, s2, s3, s4 = (st / n for n in ('s1', 's2', 's3', 's4'))
    log.info('wrap', 'input %s -> %s' % (tiff, out))
    step(1, 'page split, deskew, illustrations, text regions')
    run([HERE / 'stage1.py', tiff, '-o', s1, '--lang', lang, '--scale', scale, '--layout', layout])
    step(2, 'OCR lines, captions, paragraphs')
    run([HERE / 'stage2.py', tiff, '--stage1-json', s1 / 'page_structure.json', '-o', s2,
         '--lang', lang, '--ocr-scale', ocr_scale])
    step(3, 'classification and illustration association')
    run([HERE / 'stage3.py', s2 / 'page_structure_stage2.json', '--stage1-json', s1 / 'page_structure.json', '-o', s3])
    step(4, 'cross-page links and translation units')
    run([HERE / 'stage4.py', s3 / 'page_structure_stage3.json', '-o', s4])
    log.info('wrap', '=== building document.json and cropping figures ===')
    doc = build_document(json.loads((s4 / 'units.json').read_text(encoding='utf-8')), s1, out, tiff)
    (out / 'document.json').write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info('wrap', 'wrote %s' % (out / 'document.json'))
    dbg = out / 'debug'; dbg.mkdir(exist_ok=True)
    for src in list(s1.glob('debug*.png')) + list(s2.glob('*debug*.png')) + list(s3.glob('debug*.png')):
        shutil.copy(src, dbg / ('%s_%s' % (src.parent.name, src.name)))
    return out, doc


def process_book(tiffs, out_root, name, lang='deu', ocr_scale=2.0, scale='auto', layout='spread', resume=False):
    """Several spreads -> one document (stage 4 runs over all of them, in the order given)."""
    out = Path(out_root) / name
    if out.exists() and not resume:
        shutil.rmtree(out)
    s4 = out / 'stages' / 's4'
    log.info('wrap', 'book %s: %d spreads -> %s' % (name, len(tiffs), out))
    s3_files = []
    for k, tiff in enumerate(tiffs, 1):
        st = out / 'stages' / tiff.stem
        s1, s2, s3 = st / 's1', st / 's2', st / 's3'
        done = (s3 / 'page_structure_stage3.json').exists()
        if resume and done:
            log.info('wrap', '[%d/%d] %s: stages 1-3 present, skipped (--resume)' % (k, len(tiffs), tiff.name))
        else:
            log.info('wrap', '[%d/%d] %s' % (k, len(tiffs), tiff.name))
            step(1, 'page split, deskew, illustrations, text regions')
            run([HERE / 'stage1.py', tiff, '-o', s1, '--lang', lang, '--scale', scale, '--layout', layout])
            step(2, 'OCR lines, captions, paragraphs')
            run([HERE / 'stage2.py', tiff, '--stage1-json', s1 / 'page_structure.json', '-o', s2,
                 '--lang', lang, '--ocr-scale', ocr_scale])
            step(3, 'classification and illustration association')
            run([HERE / 'stage3.py', s2 / 'page_structure_stage2.json', '--stage1-json', s1 / 'page_structure.json', '-o', s3])
        s3_files.append(s3 / 'page_structure_stage3.json')
    log.info('wrap', '=== stage 4: cross-page and cross-spread links, translation units ===')
    run([HERE / 'stage4.py'] + s3_files + ['-o', s4])
    units_doc = json.loads((s4 / 'units.json').read_text(encoding='utf-8'))
    for pg in units_doc['pages']:                    # tell docmodel where each page's files are
        t = tiffs[pg['input_index'] - 1]
        pg['source_tiff'] = str(t)
        pg['stage1_dir'] = str(out / 'stages' / t.stem / 's1')
    log.info('wrap', '=== building document.json and cropping figures ===')
    doc = build_document(units_doc, None, out, name, book=True)
    (out / 'document.json').write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info('wrap', 'wrote %s (%d pages, %d units)' % (out / 'document.json', len(doc['pages']), len(doc['units'])))
    dbg = out / 'debug'; dbg.mkdir(exist_ok=True)
    for t in tiffs:
        st = out / 'stages' / t.stem
        for src in list((st / 's1').glob('debug*.png')) + list((st / 's2').glob('*debug*.png')) + list((st / 's3').glob('debug*.png')):
            shutil.copy(src, dbg / ('%s_%s_%s' % (t.stem, src.parent.name, src.name)))
    return out, doc


def expand_inputs(paths):
    """Files as given; a folder stands for its TIFFs in name order."""
    tiffs, folder = [], None
    for p in paths:
        if p.is_dir():
            found = sorted(f for f in p.iterdir() if f.suffix.lower() in ('.tif', '.tiff') and not f.name.startswith('.'))
            if not found:
                raise SystemExit('no .tif/.tiff files in %s' % p)
            tiffs += found; folder = p
        else:
            tiffs.append(p)
    return tiffs, folder


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('inputs', nargs='+', type=Path, help='TIFF files and/or folders of TIFFs; several = one book (in name order for a folder, as given for files)')
    ap.add_argument('--name', help='book folder name (default: the folder name, or first-last TIFF name)')
    ap.add_argument('--separate', action='store_true', help='several inputs: one result folder per TIFF instead of one book')
    ap.add_argument('--resume', action='store_true', help='book mode: skip spreads whose stages 1-3 are already in the output folder')
    ap.add_argument('-o', '--output', type=Path, default=Path('output'))
    ap.add_argument('--lang', default='deu')
    ap.add_argument('--ocr-scale', type=float, default=2.0)
    ap.add_argument('--scale', default='auto',
                    help='full px per working px (auto = measured from the body line pitch; 1 = analyse as is). Larger scans are analysed downscaled; OCR and figures use full resolution')
    ap.add_argument('--layout', choices=['spread', 'page', 'crop'], default='spread',
                    help='spread: open book split at the gutter (default); page: one whole page; crop: part of a page')
    ap.add_argument('--no-ocrfix', action='store_true', help='skip the OCR correction stage (ocrfix.py)')
    ap.add_argument('--ocr-suggest', action='store_true', help='OCR correction: also ask Hunspell for suggestions (slow, 3-8 s per hopeless word)')
    ap.add_argument('--translations', type=Path, help='translations JSON ({"units": {unit_id: {"pl": text}}}); if given, the pages are also rebuilt into <out>/<name>/render/')
    ap.add_argument('--background', choices=['paper', 'scan'], default='paper', help='for --translations: clean paper canvas or the scan with text wiped')
    ap.add_argument('--font', type=Path, help='regular serif TTF with Polish letters (default: bundled Crimson Text)')
    ap.add_argument('--bold-font', type=Path)
    ap.add_argument('--italic-font', type=Path)
    ap.add_argument('--tracking', default='auto', help="letter-spacing for --translations: 'auto' fits it to the original line widths; a number in em fixes it; 0 = none")
    ap.add_argument('--size-scale', type=float, default=1.0, help='multiply the chosen font size')
    ap.add_argument('--word-space', type=float, default=1.0, help='multiply the natural word space')
    ap.add_argument('-v', '--verbose', action='store_true', help='also print every region and every OCR line with its confidence')
    a = ap.parse_args()
    os.environ.setdefault('BOOKTRANS_T0', str(__import__('time').time()))
    if a.verbose:
        os.environ['BOOKTRANS_VERBOSE'] = '1'
    tiffs, folder = expand_inputs(a.inputs)
    jobs = []                                         # (label, out, doc)
    if len(tiffs) > 1 and not a.separate:
        name = a.name or (folder.resolve().name if folder else '%s-%s' % (tiffs[0].stem, tiffs[-1].stem))
        out, doc = process_book(tiffs, a.output, name, a.lang, a.ocr_scale, a.scale, a.layout, a.resume)
        jobs.append((name, out, doc))
    else:
        for f in tiffs:
            out, doc = process(f, a.output, a.lang, a.ocr_scale, a.scale, a.layout)
            jobs.append((f.name, out, doc))
    for label, out, doc in jobs:
        for p in doc['pages']:
            nf = sum(b['kind'] == 'figure' for b in p['blocks'])
            nt = sum(b['kind'] == 'text' for b in p['blocks'])
            log.info('wrap', '%s page %d%s: %d text blocks, %d figures' % (label, p['page'], p['side'], nt, nf))
        log.info('wrap', 'done: %s (%d pages, %d units)' % (out, len(doc['pages']), len(doc['units'])))
        if not a.no_ocrfix and ocr_correct(out, a.ocr_suggest):
            log.info('wrap', 'next: python ocrreview.py propose %s  (LLM proposals for the flags), then translate.py on document.corrected.json'
                     % (out / 'ocr_review.json'))
        if a.translations:
            from render import render, Fonts
            log.info('wrap', '=== page assembly (render.py) ===')
            doc['_dir'] = str(out)
            trans = json.loads(a.translations.read_text(encoding='utf-8'))
            names, rep = render(doc, trans, out / 'render', Fonts(a.font, a.bold_font, a.italic_font), a.background,
                               tracking=None if a.tracking == 'auto' else float(a.tracking),
                               size_scale=a.size_scale, word_space=a.word_space)
            for w in rep['warnings']:
                log.info('wrap', 'WARNING ' + w)
            log.info('wrap', 'rebuilt %d pages in %s' % (len(names), out / 'render'))
        print(out)

if __name__ == '__main__':
    main()

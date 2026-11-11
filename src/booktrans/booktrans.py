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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('inputs', nargs='+', type=Path)
    ap.add_argument('-o', '--output', type=Path, default=Path('output'))
    ap.add_argument('--lang', default='deu')
    ap.add_argument('--ocr-scale', type=float, default=2.0)
    ap.add_argument('--scale', default='auto',
                    help='full px per working px (auto = measured from the body line pitch; 1 = analyse as is). Larger scans are analysed downscaled; OCR and figures use full resolution')
    ap.add_argument('--layout', choices=['spread', 'page', 'crop'], default='spread',
                    help='spread: open book split at the gutter (default); page: one whole page; crop: part of a page')
    ap.add_argument('--translations', type=Path, help='translations JSON ({"units": {unit_id: {"pl": text}}}); if given, the pages are also rebuilt into <out>/<name>/render/')
    ap.add_argument('--background', choices=['paper', 'scan'], default='paper', help='for --translations: clean paper canvas or the scan with text wiped')
    ap.add_argument('-v', '--verbose', action='store_true', help='also print every region and every OCR line with its confidence')
    a = ap.parse_args()
    os.environ.setdefault('BOOKTRANS_T0', str(__import__('time').time()))
    if a.verbose:
        os.environ['BOOKTRANS_VERBOSE'] = '1'
    for f in a.inputs:
        out, doc = process(f, a.output, a.lang, a.ocr_scale, a.scale, a.layout)
        for p in doc['pages']:
            nf = sum(b['kind'] == 'figure' for b in p['blocks'])
            nt = sum(b['kind'] == 'text' for b in p['blocks'])
            log.info('wrap', '%s page %d%s: %d text blocks, %d figures' % (f.name, p['page'], p['side'], nt, nf))
        log.info('wrap', 'done: %s (%d pages, %d units)' % (out, len(doc['pages']), len(doc['units'])))
        if a.translations:
            from render import render, Fonts
            log.info('wrap', '=== page assembly (render.py) ===')
            doc['_dir'] = str(out)
            trans = json.loads(a.translations.read_text(encoding='utf-8'))
            names, rep = render(doc, trans, out / 'render', Fonts(), a.background)
            for w in rep['warnings']:
                log.info('wrap', 'WARNING ' + w)
            log.info('wrap', 'rebuilt pages: %s in %s' % (', '.join(names), out / 'render'))
        print(out)


if __name__ == '__main__':
    main()

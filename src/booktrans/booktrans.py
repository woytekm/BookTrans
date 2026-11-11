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

The input may be an open-book spread (split at the gutter) or a single page.
"""
import argparse
import shutil
import subprocess
import sys
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from docmodel import build_document   # noqa: E402


def run(cmd):
    r = subprocess.run([sys.executable] + [str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stderr[-2000:])
        raise SystemExit('stage failed: %s' % ' '.join(str(c) for c in cmd[:2]))


def process(tiff, out_root, lang='deu', ocr_scale=2.0):
    tiff = Path(tiff)
    out = Path(out_root) / tiff.stem
    if out.exists():
        shutil.rmtree(out)
    st = out / 'stages'
    s1, s2, s3, s4 = (st / n for n in ('s1', 's2', 's3', 's4'))
    run([HERE / 'stage1.py', tiff, '-o', s1, '--lang', lang])
    run([HERE / 'stage2.py', tiff, '--stage1-json', s1 / 'page_structure.json', '-o', s2,
         '--lang', lang, '--ocr-scale', ocr_scale])
    run([HERE / 'stage3.py', s2 / 'page_structure_stage2.json', '--stage1-json', s1 / 'page_structure.json', '-o', s3])
    run([HERE / 'stage4.py', s3 / 'page_structure_stage3.json', '-o', s4])
    doc = build_document(json.loads((s4 / 'units.json').read_text(encoding='utf-8')), s1, out, tiff)
    (out / 'document.json').write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
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
    a = ap.parse_args()
    for f in a.inputs:
        out, doc = process(f, a.output, a.lang, a.ocr_scale)
        for p in doc['pages']:
            nf = sum(b['kind'] == 'figure' for b in p['blocks'])
            nt = sum(b['kind'] == 'text' for b in p['blocks'])
            print('%s page %d%s: %d text blocks, %d figures' % (f.name, p['page'], p['side'], nt, nf))
        print('->', out)


if __name__ == '__main__':
    main()

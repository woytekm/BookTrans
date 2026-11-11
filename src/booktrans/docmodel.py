#!/usr/bin/env python3
"""Nested document model: everything needed to recreate a page, in one JSON.

Built from the Stage 4 output (which already holds the Stage 1-3 data) plus the
deskewed page images.  Per page it gives

* ``layout``  - page size, paper colour, the text area and the column boxes;
* ``blocks``  - reading-ordered text blocks and figures, each with its box;
  * text block: ``role`` (paragraph / heading / caption / footnote_or_footer ...),
    OCR text, link to its translation ``unit_id`` (and ``continues_from`` /
    ``continues_to``), ``typography`` (glyph height, line pitch, alignment,
    first-line indent, gap above) and ``lines`` (box, text, confidence);
  * figure: ``file`` (cropped image, relative path) and the ids of its captions.

All coordinates are pixels of the deskewed page image ``image``.  Nothing here
depends on translation or rendering; the renderer will place translated unit
text into the block boxes and paste the figure files back.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image

from imgutil import paper_color

SCHEMA = '1.0'
JUSTIFY_TOL = 6        # px: a line ends "on the edge" if within this of the column edge
FIG_PAD = 2            # px added around a figure crop


def _col_edges(paragraph_regions):
    x1 = [l['bbox'][0] for r in paragraph_regions for l in r['lines']]
    x2 = [l['bbox'][2] for r in paragraph_regions for l in r['lines']]
    if not x1:
        return None
    x1 = np.array(x1, float); x2 = np.array(x2, float)
    return (float(np.median(x1[x1 <= np.percentile(x1, 50)])),
            float(np.median(x2[x2 >= np.percentile(x2, 60)])))


def _alignment(region, L, R):
    ls = region['lines']
    if region['type'] == 'heading':
        c = (region['bbox'][0] + region['bbox'][2]) / 2
        if abs(c - (L + R) / 2) <= 8 and region['bbox'][0] - L > 12:
            return 'center'
    inner = ls[:-1]
    if len(inner) >= 3:
        on_edge = sum(abs(l['bbox'][2] - R) <= JUSTIFY_TOL for l in inner)
        return 'justified' if on_edge >= 0.6 * len(inner) else 'left'
    return None


def _typography(region, L, R, page_align):
    ls = region['lines']
    cy = [(l['bbox'][1] + l['bbox'][3]) / 2 for l in ls]
    t = {
        'glyph_height_px': round(float(np.median([l.get('glyph_height', 0) for l in ls])), 1),
        'relative_size': region.get('median_relative_size'),
        'line_pitch_px': round(float(np.median(np.diff(cy))), 1) if len(ls) > 1 else None,
        'style': region.get('style') or ls[0].get('style', 'unknown'),
        'uppercase_ratio': round(float(np.mean([l.get('uppercase_ratio', 0) for l in ls])), 2),
    }
    al = _alignment(region, L, R) if L is not None else None
    t['alignment'] = al or (page_align if region['type'] == 'paragraph' else 'left')
    t['alignment_measured'] = al is not None
    if L is not None and region['type'] == 'paragraph':
        t['first_line_indent_px'] = round(max(0.0, ls[0]['bbox'][0] - L), 1)
        t['last_line_shortfall_px'] = round(max(0.0, R - ls[-1]['bbox'][2]), 1)
    return t


def _nid(i):
    """Drop Stage 4's per-input prefix (``s1-``): a document is one input."""
    return i[3:] if i and i[:1] == 's' and i[1:2].isdigit() and i[2:3] == '-' else i


def build_page(page, image_rel, page_img, fig_dir, unit_of):
    """Return the nested model of one page; writes figure crops into ``fig_dir``."""
    h, w = page['height'], page['width']
    regions = page['regions']
    paras = [r for r in regions if r['type'] == 'paragraph']
    edges = _col_edges(paras)
    L, R = edges if edges else (None, None)
    def union(items):
        if not items:
            return None
        return [min(r['bbox'][0] for r in items), min(r['bbox'][1] for r in items),
                max(r['bbox'][2] for r in items), max(r['bbox'][3] for r in items)]
    text_like = [r for r in regions if r['type'] not in ('illustration', 'caption')]
    area = union(text_like) or union([r for r in regions if r['type'] != 'illustration'])
    content_area = union(regions + page.get('illustrations', []))
    # page default alignment: majority of the measured paragraphs
    meas = [_alignment(r, L, R) for r in paras] if edges else []
    meas = [m for m in meas if m]
    page_align = max(set(meas), key=meas.count) if meas else 'left'

    blocks = []; fig_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(image_rel).stem
    figs = sorted(page.get('illustrations', []), key=lambda i: (i['bbox'][1], i['bbox'][0]))
    fig_of = {}
    for k, ill in enumerate(figs, 1):
        x1, y1, x2, y2 = ill['bbox']
        x1, y1 = max(0, x1 - FIG_PAD), max(0, y1 - FIG_PAD)
        x2, y2 = min(w, x2 + FIG_PAD), min(h, y2 + FIG_PAD)
        fn = '%s_fig_%02d.png' % (stem, k)
        Image.fromarray(page_img[y1:y2, x1:x2]).save(fig_dir / fn)
        fig_of[ill['object_id']] = {
            'block_id': ill['object_id'], 'kind': 'figure', 'role': 'illustration',
            'bbox': ill['bbox'], 'crop_bbox': [x1, y1, x2, y2],
            'file': 'figures/' + fn, 'detector': ill.get('classification_reason', '').replace('stage1_', ''),
            'captions': [],
        }
    for r in sorted(regions, key=lambda r: (r['bbox'][1], r['bbox'][0])):
        if r['type'] == 'illustration':
            continue
        b = {'block_id': _nid(r['object_id']), 'kind': 'text', 'role': r['type'], 'bbox': r['bbox'],
             'text': r['text'], 'line_count': r['line_count'],
             'translation_status': r.get('translation_status'),
             'content_context': r.get('content_context'),
             'typography': _typography(r, L, R, page_align)}
        if r['object_id'] in unit_of:
            b['unit_id'] = unit_of[r['object_id']]
        for key in ('continues_from', 'continues_to'):
            if r.get(key):
                b[key] = _nid(r[key])
        ids = r.get('associated_illustration_ids') or []
        if ids:
            b['figure_ids'] = ids
            for i in ids:
                if i in fig_of:
                    fig_of[i]['captions'].append(_nid(r['object_id']))
        b['lines'] = [{'bbox': l['bbox'], 'text': l['text'], 'confidence': l.get('confidence'),
                       'glyph_height_px': l.get('glyph_height'),
                       'paragraph_start': l.get('paragraph_break', False)} for l in r['lines']]
        blocks.append(b)
    blocks += list(fig_of.values())
    blocks.sort(key=lambda b: (b['bbox'][1], b['bbox'][0]))
    for i, b in enumerate(blocks, 1):
        b['reading_order'] = i
    # vertical gap to the nearest block above that shares the column (>= 50 % horizontal overlap)
    for b in blocks:
        if b['kind'] != 'text':
            continue
        x1, y1, x2, _ = b['bbox']; best = None
        for o in blocks:
            a1, _, a2, a4 = o['bbox']
            if o is b or a4 > y1 + 2:
                continue
            if max(0, min(x2, a2) - max(x1, a1)) >= 0.5 * min(x2 - x1, a2 - a1) and (best is None or a4 > best):
                best = a4
        if best is not None:
            b['typography']['gap_above_px'] = y1 - best
    return {
        'page': page['page'], 'side': page['side'],
        'image': image_rel, 'width': w, 'height': h,
        'source_x_offset': page['x_offset'], 'skew_deg': page.get('skew_deg'),
        'deskew': page.get('deskew'),
        'layout': {
            'paper_color_rgb': list(paper_color(page_img)),
            'text_area': area, 'content_area': content_area,
            'columns': [{'left': round(L, 1), 'right': round(R, 1)}] if edges else [],
            'body_glyph_height_px': page.get('estimated_body_glyph_height'),
            'default_alignment': page_align,
        },
        'blocks': blocks,
    }


def build_document(units_doc, stage1_dir, out_dir, source_file):
    """Assemble ``document.json`` content; copies page images, writes figures."""
    out_dir = Path(out_dir)
    (out_dir / 'pages').mkdir(parents=True, exist_ok=True)
    unit_of = {oid: u['unit_id'] for u in units_doc['units'] for oid in u['source_object_ids']}
    pages = []
    for page in units_doc['pages']:
        src = Path(stage1_dir) / page['page_image']
        dst = out_dir / 'pages' / page['page_image']
        arr = np.asarray(Image.open(src).convert('RGB'))
        Image.fromarray(arr).save(dst)
        pages.append(build_page(page, 'pages/' + page['page_image'], arr, out_dir / 'figures', unit_of))
    units = []
    for u in units_doc['units']:
        d = {k: u[k] for k in ('unit_id', 'type', 'text', 'dehyphenated', 'joins')}
        d['source_block_ids'] = [_nid(i) for i in u['source_object_ids']]
        units.append(d)
    return {'schema_version': SCHEMA, 'source_file': str(source_file),
            'coordinate_system': 'pixels of each page image (deskewed)',
            'pages': pages, 'units': units}

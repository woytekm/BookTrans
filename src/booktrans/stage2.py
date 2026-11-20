#!/usr/bin/env python3
"""BookTrans stage 2: OCR lines, line classification and paragraph/caption regions.

Tesseract's line bounding boxes are retained as permanent objects. Lines are
classified (body, heading, caption, footnote/byline) and grouped into regions
*at page level*. Paragraph boundaries come from line geometry on the deskewed
page - a line that stops short of the column's right edge ends a paragraph - and
not from vertical gaps, because the sample book has no extra paragraph spacing
and no first-line indent. See ``mark_paragraph_breaks``.
"""
import argparse, json, re
from pathlib import Path
import cv2, numpy as np, pytesseract
from PIL import Image
from pytesseract import Output

from imgutil import crop_for_ocr, load_pages, load_full_pages, paper_color
from illustrations import mask_box
import captions
import log


SEGMENT_GAP_FACTOR = 3.0   # a gap wider than this many glyph heights splits a line


def split_at_gaps(ids, d):
    """Split one Tesseract "line" into segments at very wide horizontal gaps.

    Tesseract (``--psm 6``) happily reads captions that are set side by side
    (e.g. five figure captions in one row under a row of profile drawings) as a
    single line. Word spacing is ~1/3 of a glyph height, justified stretching
    stays below ~1.5 glyph heights, so a gap of 3 glyph heights is a column break.
    """
    ids=sorted(ids,key=lambda i:d['left'][i])
    hs=[d['height'][i] for i in ids if d['height'][i]>0]
    mh=float(np.median(hs)) if hs else 10.0
    # Small text (captions, ~9 px) is split at ~1.8 glyph heights: side-by-side
    # captions can be only ~20 px apart. Body text keeps the generous 3x rule,
    # because justified word gaps can stretch to ~1.2 glyph heights.
    thr=max(14.0,1.8*mh) if mh<=11 else max(24.0,SEGMENT_GAP_FACTOR*mh)
    segs=[[ids[0]]]
    for i in ids[1:]:
        prev=segs[-1][-1]
        if d['left'][i]-(d['left'][prev]+d['width'][prev])>thr: segs.append([i])
        else: segs[-1].append(i)
    return segs


def ocr_lines(page, bbox, lang, context=10, scale=2.0, hires=None, f=1.0):
    """OCR one coarse region of ``page`` and return line objects in page coordinates.

    The region is cut with real context pixels and surrounded by paper-coloured
    padding (see imgutil.crop_for_ocr); a bare crop made Tesseract drop or
    misread line-initial letters that touch the crop border.
    """
    # ``bbox`` and the result are in working-scale pixels. With a full-resolution
    # page (``hires``, ``f`` full px per working px) the crop is cut from the real
    # pixels and resized by scale/f, so ``scale`` keeps its meaning (OCR pixels
    # per working pixel) whatever the scan resolution.
    if hires is not None and f>1:
        crop,(ox,oy)=crop_for_ocr(hires,[v*f for v in bbox],context=int(round(context*f)),pad=int(round(24*f)))
        offset=(ox/f,oy/f)
        gray=cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY)
        gray=cv2.resize(gray,None,fx=scale/f,fy=scale/f,interpolation=cv2.INTER_AREA if scale<f else cv2.INTER_CUBIC)
        scale_done=True
    else:
        crop,offset=crop_for_ocr(page,bbox,context=context,pad=24)
        gray=cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY)
        scale_done=False
    # The sample scans are ~135 DPI: body glyphs are ~8 px, captions ~6 px (x-height),
    # far below what Tesseract likes. Upscaling the crop first improves small text
    # (captions) clearly and leaves body text unchanged. Boxes are mapped back.
    if scale!=1 and not scale_done: gray=cv2.resize(gray,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
    # CLAHE helps soft ~135 DPI scans; on real high-resolution pixels it only distorts thin small caps.
    if not scale_done: gray=cv2.createCLAHE(clipLimit=1.5,tileGridSize=(8,8)).apply(gray)
    d=pytesseract.image_to_data(gray,lang=lang,config='--psm 6',output_type=Output.DICT)
    if scale!=1:
        for k in ('left','top','width','height'): d[k]=[int(round(v/scale)) for v in d[k]]
    groups={}
    for i,t in enumerate(d['text']):
        t=t.strip()
        try: conf=float(d['conf'][i])
        except: conf=-1
        if not t or conf<5: continue
        # Stray dashes/bars/dots that Tesseract invents in blank gaps (typically
        # between side-by-side captions) bridge the gaps and merge the captions.
        if not re.search(r'[A-Za-zÄÖÜäöüß0-9]',t) and conf<50: continue
        key=(d['block_num'][i],d['par_num'][i],d['line_num'][i])
        groups.setdefault(key,[]).append(i)
    out=[]; ox,oy=offset
    segments=[]
    for ids in groups.values():
        segments.extend(split_at_gaps(ids,d))
    for ids in segments:
        x1=min(d['left'][i] for i in ids); y1=min(d['top'][i] for i in ids)
        x2=max(d['left'][i]+d['width'][i] for i in ids); y2=max(d['top'][i]+d['height'][i] for i in ids)
        txt=' '.join(d['text'][i].strip() for i in ids)
        hs=[d['height'][i] for i in ids if d['height'][i]>0]
        chars=re.sub(r'[^A-Za-zÄÖÜäöüß]','',txt)
        caps=sum(c.isupper() for c in chars)/max(1,len(chars))
        out.append({'bbox':[int(round(x1+ox)),int(round(y1+oy)),int(round(x2+ox)),int(round(y2+oy))],'text':txt,
                    'glyph_height':round(float(np.median(hs)),1) if hs else 0,
                    'confidence':round(float(np.mean([float(d['conf'][i]) for i in ids])),1),
                    'uppercase_ratio':round(caps,2),'word_count':len(ids),
                    # word boxes (same coordinates as the line box): lets a GUI mark single words on the page
                    'words':[{'text':d['text'][i].strip(),
                              'bbox':[int(round(d['left'][i]+ox)),int(round(d['top'][i]+oy)),
                                      int(round(d['left'][i]+d['width'][i]+ox)),int(round(d['top'][i]+d['height'][i]+oy))],
                              'conf':round(float(d['conf'][i]),1)} for i in ids]})
    return sorted(out,key=lambda x:(x['bbox'][1],x['bbox'][0]))


# Caption lines are smaller than body text, but an accent (Ä, Ü) in a small-caps
# caption makes OCR report a taller line, so allow a little headroom.
CAPTION_MAX_REL = 1.15
# ... but a mixed-case line of body size next to a picture is running text (the body of a page that starts under a
# figure used to become one long "caption"): it must be clearly smaller than the body.
CAPTION_MIXED_REL = 0.88


def caption_sized(ln, body):
    """Small enough for a caption: capitals up to ``CAPTION_MAX_REL`` x body, other text below ``CAPTION_MIXED_REL``."""
    h=ln['glyph_height']
    return h<body*CAPTION_MIXED_REL or (ln.get('uppercase_ratio',0)>.6 and h<body*CAPTION_MAX_REL)


def is_near(box, fig, gap):
    """True if ``box`` touches the figure's side/top/bottom within ``gap`` px.

    Captions sit below a figure (horizontal overlap, small vertical gap) or beside
    it (vertical overlap, small horizontal gap); diagonal neighbours do not count.
    """
    dx=max(0,max(fig[0],box[0])-min(fig[2],box[2])); dy=max(0,max(fig[1],box[1])-min(fig[3],box[3]))
    return (dx==0 and dy<=gap) or (dy==0 and dx<=gap) or (dx<=12 and dy<=12)


def absorb_caption_continuations(lines, body):
    """A small line directly under a caption line, in the same column, is part of it.

    Second/third caption lines are not always near the figure themselves.
    """
    changed=True
    while changed:
        changed=False
        for ln in lines:
            if ln['type']!='body_line' and ln['type']!='heading_line': continue
            if not caption_sized(ln,body): continue
            x1,y1,x2,y2=ln['bbox']
            for cp in lines:
                if cp['type']!='caption_line': continue
                a,b,c,d=cp['bbox']
                ov=max(0,min(x2,c)-max(x1,a))/max(1,min(x2-x1,c-a))
                if 0<=y1-d<=max(6,0.9*(d-b)) and ov>=.5:
                    ln['type']='caption_line'; ln['classification_reason']='continuation of the caption line above'; ln['classification_confidence']=.7
                    changed=True; break
    return lines


def _iou(a,b):
    ix=max(0,min(a[2],b[2])-max(a[0],b[0])); iy=max(0,min(a[3],b[3])-max(a[1],b[1])); inter=ix*iy
    return inter/max(1,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter)


def dedupe_lines(lines, iou=0.5):
    """Drop duplicate OCR lines produced by overlapping coarse text boxes.

    Overlapping Stage 1 boxes are OCR'd separately and each used to emit its own
    copy of the shared lines (and so each copy became a region to translate).
    The more confident copy wins.
    """
    kept=[]
    for ln in sorted(lines,key=lambda z:-z['confidence']*max(1,z['word_count'])):
        if all(_iou(ln['bbox'],k['bbox'])<iou for k in kept): kept.append(ln)
    return sorted(kept,key=lambda z:(z['bbox'][1],z['bbox'][0]))


def classify_lines(lines, images, page_h):
    if not lines:return [],0
    # Body size from running text only: captions are small and, on a drawing
    # page, can outnumber body lines, which would drag a plain median down.
    longer=[x['glyph_height'] for x in lines if x['glyph_height']>0 and x['word_count']>=6]
    hs=longer or [x['glyph_height'] for x in lines if x['glyph_height']>0]
    body=float(np.median(hs)) if hs else 12
    for ln in lines:
        x1,y1,x2,y2=ln['bbox']; h=ln['glyph_height']; txt=ln['text']
        typ='body_line'; reason='default text line'; conf=.65
        if y1>page_h*.82 and h<body*.9:
            typ='footnote_or_footer'; reason='small text near bottom margin'; conf=.78
        elif ln['uppercase_ratio']>.72 and len(txt)<90 and h>=body*.92:
            typ='heading_line'; reason='predominantly uppercase'; conf=.84
        for im in images:
            if caption_sized(ln,body) and is_near(ln['bbox'],im['bbox'],max(26,body*2.2)):
                typ='caption_line'; reason='beside/below an illustration and smaller than body'; conf=.76; break
        ln.update({'type':typ,'classification_reason':reason,'classification_confidence':conf,
                   'relative_size':round(h/max(body,1),2),'style':'unknown'})
    absorb_caption_continuations(lines,body)
    mark_foot_notes(lines,body,page_h)
    return lines,body


def _x_overlap(a, b):
    return min(a[2], b[2]) - max(a[0], b[0]) > 0.3 * min(a[2] - a[0], b[2] - b[0])


def mark_foot_notes(lines, body, page_h):
    """Notes at the foot of the text, wherever they start: a run of smaller lines (< 0.9 body) at the foot of a column -
    no body-sized line below them in the same column - set off from the text above by a gap wider than the normal
    leading (or by a footnote rule, which leaves such a gap).  With body text above it in the column the run is a note
    anywhere on the page: footnotes taking a quarter of the page, or a "Literatur:" note under the last paragraph of a
    chapter that ends half way down (page 70); before, those were set as body text and flowed with it.  Without body
    text above (a page of small print), only a run in the lower half of the page counts."""
    textual = [l for l in lines if l['type'] in ('body_line', 'footnote_or_footer')]
    small = lambda l: 0 < l['glyph_height'] < body * .9
    for ln in sorted(textual, key=lambda z: z['bbox'][1]):
        if ln['type'] != 'body_line' or not small(ln):
            continue
        if ln['bbox'][2] - ln['bbox'][0] < 4 * body:     # a speck or stray mark, not a line of text
            continue
        col = [l for l in textual if _x_overlap(l['bbox'], ln['bbox'])]
        below = [l for l in col if l['bbox'][1] > ln['bbox'][1]]
        if any(not small(l) for l in below):
            continue                                  # body text follows: a small passage inside the text
        run = [ln] + below
        above = [l for l in col if l['bbox'][3] <= ln['bbox'][1] and l not in run]
        if above:
            gap = ln['bbox'][1] - max(l['bbox'][3] for l in above)
            if gap < body:                            # continues the text above with normal leading
                continue
        if not any(not small(l) for l in above) and ln['bbox'][1] < page_h * .5:
            continue                                  # no body text above: small print in the upper half is text
        for l in run:
            if l['type'] == 'body_line':
                l.update({'type': 'footnote_or_footer', 'classification_reason': 'small lines at the foot of the column',
                          'classification_confidence': .74})


def make_region(g,n,typ):
    x1=min(z['bbox'][0] for z in g); y1=min(z['bbox'][1] for z in g)
    x2=max(z['bbox'][2] for z in g); y2=max(z['bbox'][3] for z in g)
    outtype=typ
    if typ=='body_line': outtype='paragraph'
    elif typ=='heading_line': outtype='heading'
    elif typ=='caption_line': outtype='caption'
    elif typ=='footnote_or_footer': outtype='footnote_or_footer'
    return {'id':n,'type':outtype,'bbox':[x1,y1,x2,y2],
            'text':'\n'.join(z['text'] for z in g),'line_count':len(g),
            'median_relative_size':round(float(np.median([z['relative_size'] for z in g])),2),
            'lines':g}


def group_stacked_lines(lines):
    """Group lines of one kind into blocks: stacked closely and sharing a column.

    Used for captions (keeps five side-by-side captions apart and two-line
    captions whole), multi-line headings and footnotes.
    """
    groups=[]
    for ln in sorted(lines,key=lambda z:(z['bbox'][1],z['bbox'][0])):
        x1,y1,x2,y2=ln['bbox']
        for g in groups:
            a,b,c,d=g[-1]['bbox']
            ov=max(0,min(x2,c)-max(x1,a))/max(1,min(x2-x1,c-a))
            same_left=abs(x1-a)<=6
            if 0<=y1-d<=max(8,1.2*(d-b)) and (ov>=.3 or same_left):
                g.append(ln); break
        else:
            groups.append([ln])
    return groups


def body_columns(lines):
    """Cluster body lines into text columns by horizontal extent."""
    cols=[]
    for ln in sorted(lines,key=lambda z:(z['bbox'][1],z['bbox'][0])):
        x1,_,x2,_=ln['bbox']; w=x2-x1
        best=None
        for c in cols:
            ov=max(0,min(x2,c['r'])-max(x1,c['l']))/max(1,min(w,c['r']-c['l']))
            if ov>=.5 and (best is None or ov>best[0]): best=(ov,c)
        if best:
            c=best[1]; c['lines'].append(ln); c['l']=min(c['l'],x1); c['r']=max(c['r'],x2)
        else:
            cols.append({'l':x1,'r':x2,'lines':[ln]})
    return [c['lines'] for c in cols]


def _ends_with_hyphen(text):
    return text.rstrip().endswith(('-','\u2010','\u00ac'))


def _continues_sentence(prev_text, cur_text):
    """True when ``cur_text`` cannot start a paragraph: it begins with a lower-case letter and the
    previous line does not end a sentence. (German paragraphs start with a capital; a lost word in the
    OCR can make the line before look short, which would otherwise cut the paragraph there.)"""
    first = next((c for c in cur_text if c.isalpha()), '')
    return bool(first) and first.islower() and not prev_text.rstrip().endswith(('.', '!', '?', '\u00bb', '"', '\u201d', '\u201c'))


def mark_paragraph_breaks(col, body):
    """Mark the first line of every paragraph in one text column.

    Evidence, in this order (the first that applies wins):

    * **short last line** - in justified text every full line ends on the
      column's right edge, so a line that stops clearly short ends a paragraph.
      This is the signal the sample book uses (it has neither first-line indents
      nor extra paragraph spacing). After deskew, full lines end within ~3 px of
      the edge while real paragraph-final lines are >= 19 px short.
    * **first-line indent** - the next line starts clearly right of the column's
      left edge.
    * **extra line spacing** - clearly more than the normal line pitch.

    A line that ends in a hyphen never ends a paragraph, so a paragraph is never
    cut inside a hyphenated word; nor does a line followed by a line that starts in
    lower case when the sentence is not finished (a word lost in OCR can make a
    line look short). Every marked line gets ``paragraph_break`` and
    ``paragraph_break_reason``; the first line of the column is a break by definition.

    *Stray marks* read as lines (narrower than 12 % of the widest line, at most two
    letters or digits: ``)``, ``5``, ``Ef`` from a mark in the margin) decide nothing:
    one among the text lines stays in the paragraph around it (``stray_mark``; stage 4
    leaves it out of the unit text); one apart from the text (a page number below it)
    is a region of its own (``isolated_mark``).
    """
    ls=sorted(col,key=lambda z:z['bbox'][1])
    # stray marks read as lines (')', '5', 'Ef' from a pencil or printer's mark in the margin) take no part: placed
    # between two lines they looked like an indented first line and cut the paragraph there (page 62 of the test book)
    w=np.array([l['bbox'][2]-l['bbox'][0] for l in ls],float)
    stray=[w[i] < 0.12*float(np.max(w)) and sum(c.isalnum() for c in ls[i].get('text','')) <= 2 for i in range(len(ls))]
    real=[l for l,s in zip(ls,stray) if not s] or ls
    x1=np.array([l['bbox'][0] for l in real],float); x2=np.array([l['bbox'][2] for l in real],float)
    L=float(np.median(x1[x1<=np.percentile(x1,50)])); R=float(np.median(x2[x2>=np.percentile(x2,60)]))
    short=R-x2
    full=short[short<=6]
    sd=float(np.std(full)) if len(full)>=3 else 1.5
    short_thr=max(8.0,5.0*sd); indent_thr=max(10.0,1.2*body)
    cy=np.array([(l['bbox'][1]+l['bbox'][3])/2 for l in real],float)
    pitch=float(np.median(np.diff(cy))) if len(real)>2 else 0.0
    first=True; prev=None; pcy=None; after_mark=False
    last_cy=float(cy[-1]) if len(cy) else 0.0
    for l,s in zip(ls,stray):
        c=(l['bbox'][1]+l['bbox'][3])/2
        if s and real is not ls:
            l['stray_mark']=True
            between = pcy is not None and c <= last_cy + 0.5*max(pitch,1.0) and (pitch <= 0 or c-pcy <= 1.4*pitch)
            if between:              # among the text lines: belongs to the paragraph around it, decides nothing
                l['paragraph_break']=False; l['paragraph_break_reason']=None
            else:                    # apart from the text (a page number below it): a region of its own
                l['paragraph_break']=True; l['paragraph_break_reason']='isolated_mark'; after_mark=True
            continue
        if first:
            l['paragraph_break']=True; l['paragraph_break_reason']='column_start'; first=False
        elif after_mark:
            l['paragraph_break']=True; l['paragraph_break_reason']='after_isolated_mark'
        else:
            reason=None
            if not _ends_with_hyphen(prev['text']) and not _continues_sentence(prev['text'], l['text']):
                if R-prev['bbox'][2]>short_thr: reason='short_last_line'
                elif l['bbox'][0]-L>indent_thr: reason='first_line_indent'
                elif pitch>0 and (c-pcy)>1.4*pitch: reason='extra_line_spacing'
            l['paragraph_break']=reason is not None; l['paragraph_break_reason']=reason
        after_mark=False
        prev, pcy = l, c
    if ls and not any(l['paragraph_break'] for l in ls):
        ls[0]['paragraph_break']=True; ls[0]['paragraph_break_reason']='column_start'
    return ls


NOTE_START = re.compile(r'^\s*(\d{1,3}\s?\)|\(\d{1,3}\)|\d{1,3}(?=\s?\S)|\*{1,3}|[\u00b9\u00b2\u00b3\u2070-\u2079]+)')
SENTENCE_END = re.compile(r'[.!?:;\u201c\u201d"\u00bb\u00ab)\]]\s*$')


def split_notes(group):
    """One block per footnote: a stack of footnote lines splits where a line starts with a note marker (``1``,
    ``1)``, ``(1)``, ``*``, a superscript digit) and the line above ends a sentence - so each note starts on its own
    line in the rendering instead of running on from the one before."""
    parts = [[group[0]]]
    for prev, ln in zip(group, group[1:]):
        if NOTE_START.match(ln['text']) and SENTENCE_END.search(prev['text']):
            parts.append([])
        parts[-1].append(ln)
    return parts


def build_regions(lines, body):
    """Turn classified lines into semantic regions, at page level.

    Paragraphs are built per text *column* of the whole page, not per coarse
    Stage 1 box: box boundaries are arbitrary and used to cut paragraphs in the
    wrong place (and split ``mu\u00dften.`` from its own paragraph).
    """
    regs=[]
    caps=[l for l in lines if l['type']=='caption_line']
    for g in group_stacked_lines(caps): regs.append(make_region(g,0,'caption_line'))
    bodyl=[l for l in lines if l['type']=='body_line']
    for col in body_columns(bodyl):
        cur=[]
        for ln in mark_paragraph_breaks(col,body):
            if ln['paragraph_break'] and cur:
                regs.append(make_region(cur,0,'body_line')); cur=[]
            cur.append(ln)
        if cur: regs.append(make_region(cur,0,'body_line'))
    for kind in ('heading_line','footnote_or_footer','author_byline_candidate'):
        for g in group_stacked_lines([l for l in lines if l['type']==kind]):
            for part in (split_notes(g) if kind=='footnote_or_footer' else [g]): regs.append(make_region(part,0,kind))
    regs.sort(key=lambda r:(r['bbox'][1],r['bbox'][0]))
    for i,r in enumerate(regs,1): r['id']=i
    return regs


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('input',type=Path); ap.add_argument('--stage1-json',type=Path,default=Path('out1/page_structure.json'))
    ap.add_argument('-o','--output',type=Path,default=Path('stage2_out')); ap.add_argument('--lang',default='deu')
    ap.add_argument('--ocr-scale',type=float,default=2.0,help='upscale factor applied to OCR crops (default 2.0; 1 = off)'); a=ap.parse_args()
    base=json.loads(a.stage1_json.read_text(encoding='utf8'))
    # Analyse the exact (deskewed) page images Stage 1 saved; ``a.input`` is only
    # the fallback for old Stage 1 JSON without page images.
    log.info('stage2','loading deskewed pages from %s' % a.stage1_json.parent)
    page_imgs=load_pages(base,a.stage1_json.parent,a.input)
    full_imgs=load_full_pages(base,a.stage1_json.parent)
    src=np.hstack(page_imgs)
    a.output.mkdir(parents=True,exist_ok=True); result={'schema_version':'0.3.0','source_file':str(a.input),'pages':[]}
    for p,page,fullpage in zip(base['pages'],page_imgs,full_imgs):
        side=p['side']; xoff=p['x_offset']; f=p.get('scale',1.0) or 1.0
        todo=[r for r in p['regions'] if r['type'] in ('text','heading','caption','text_candidate')]
        log.info('stage2','page %d (%s): OCR of %d text regions%s, ocr-scale %.1f' % (p['page'],side,len(todo),' from full-resolution pixels' if fullpage is not None else '',a.ocr_scale))
        n_done=0
        all_lines=[]; image_boxes=[]
        # Text is OCR'd from a copy of the page with every illustration painted
        # over in paper colour: OCR crops carry real context pixels, and the edge
        # of a neighbouring drawing used to come back as junk "captions".
        ocr_page=page.copy(); paper=paper_color(page)
        ocr_full=fullpage.copy() if fullpage is not None else None
        paper_full=paper_color(fullpage) if fullpage is not None else None
        text_boxes=[r['bbox'] for r in todo]
        for r in p['regions']:
            if 'image' in r['type'] or 'drawing' in r['type']:
                # the mask stops at a caption set against the picture (it used to cover the caption's tops)
                x1,y1,x2,y2=captions.clip_mask(mask_box(r),text_boxes); ocr_page[max(0,y1):y2,max(0,x1):x2]=paper
                if fullpage is not None:
                    X1,Y1,X2,Y2=[int(round(v*f)) for v in (x1,y1,x2,y2)]; ocr_full[max(0,Y1):Y2,max(0,X1):X2]=paper_full
        for r in p['regions']:
            if r['type'] not in ('text','heading','caption','text_candidate'):
                if 'image' in r['type'] or 'drawing' in r['type']:
                    image_boxes.append({'bbox':r['bbox'],'type':'illustration'})
                continue
            ls=ocr_lines(ocr_page,r['bbox'],a.lang,scale=a.ocr_scale,hires=ocr_full,f=f); all_lines.extend(ls)
            n_done+=1
            log.info('stage2','  region %d/%d %s: %d lines' % (n_done,len(todo),r['bbox'],len(ls)))
        n_raw=len(all_lines)
        all_lines=dedupe_lines(all_lines)
        if n_raw!=len(all_lines): log.info('stage2','  removed %d duplicate lines' % (n_raw-len(all_lines)))
        all_lines.sort(key=lambda z:(z['bbox'][1],z['bbox'][0]))
        lines,body=classify_lines(all_lines,image_boxes,page.shape[0])
        figs=[im['bbox'] for im in image_boxes]
        n0=len(lines); lines=captions.drop_junk_in_figures(lines,figs)
        if n0!=len(lines): log.info('stage2','  dropped %d low-confidence line(s) inside figures' % (n0-len(lines)))
        n0=sum(l['type']=='caption_line' for l in lines); lines=captions.join_caption_rows(lines,figs,page)
        n1=sum(l['type']=='caption_line' for l in lines)
        if n0!=n1: log.info('stage2','  joined caption pieces: %d -> %d caption lines' % (n0,n1))
        for i,ln in enumerate(lines):
            if ln['type']=='caption_line':
                lines[i]=captions.reocr_line(ln,ocr_page,a.lang,hires=ocr_full,f=f)
                if lines[i] is not ln: log.info('stage2','  caption re-read: %r (%.0f) -> %r (%.0f)' % (ln['text'],ln['confidence'],lines[i]['text'],lines[i]['confidence']))
        junk=[l for l in lines if l['type']=='caption_line' and captions.is_junk_caption(l)]
        if junk:
            log.info('stage2','  dropped %d junk caption piece(s): %s' % (len(junk),', '.join(repr(l['text']) for l in junk)))
            lines=[l for l in lines if l not in junk]
        kinds={}
        for ln in lines: kinds[ln['type']]=kinds.get(ln['type'],0)+1
        log.info('stage2','  %d lines (%s), body glyph height %.1f px' % (len(lines),', '.join('%d %s' % (v,k.replace('_line','')) for k,v in sorted(kinds.items())),body))
        for ln in lines: log.debug('stage2','%-14s conf %3d %s' % (ln['type'],ln['confidence'],ln['text'][:70]))
        for i,ln in enumerate(lines):
            if ln['type']!='caption_line' and i+1<len(lines) and ln['bbox'][1]<page.shape[0]*.55 and ln['bbox'][0]>page.shape[1]*.45:
                if lines[i+1]['type']=='heading_line':
                    ln['type']='author_byline_candidate'; ln['classification_reason']='right-aligned line immediately before heading'; ln['classification_confidence']=.72
        regs=build_regions(lines,body)
        log.info('stage2','  built %d regions: %s' % (len(regs),', '.join('%d %s' % (sum(r['type']==t for r in regs),t) for t in sorted({r['type'] for r in regs}))))
        for im in image_boxes: regs.append(im)
        regs.sort(key=lambda r:(r['bbox'][1],r['bbox'][0]))
        for i,r in enumerate(regs,1): r['id']=i
        result['pages'].append({'page':p['page'],'side':side,'x_offset':xoff,'width':p['width'],'height':p['height'],
                                'skew_deg':p.get('skew_deg'),'deskew':p.get('deskew'),'page_image':p.get('page_image'),
                                'scale':f,'page_image_full':p.get('page_image_full'),
                                'estimated_body_glyph_height':round(body,1),'regions':regs,'line_count':len(lines)})
    (a.output/'page_structure_stage2.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    dbg=cv2.cvtColor(src,cv2.COLOR_RGB2BGR).copy()
    colors={'heading':(0,140,255),'paragraph':(0,180,0),'caption':(200,0,200),'footnote_or_footer':(180,100,0),'author_byline_candidate':(255,120,0)}
    for p in result['pages']:
        for r in p['regions']:
            x1,y1,x2,y2=r['bbox']; x1+=p['x_offset']; x2+=p['x_offset']; c=colors.get(r['type'],(0,180,0))
            cv2.rectangle(dbg,(x1,y1),(x2,y2),c,2); cv2.putText(dbg,f"{p['page']}:{r['id']} {r['type']}",(x1,max(14,y1-5)),cv2.FONT_HERSHEY_SIMPLEX,.4,c,1,cv2.LINE_AA)
    for p in base['pages']:
        for r in p['regions']:
            if 'image' in r['type'] or 'drawing' in r['type']:
                x1,y1,x2,y2=r['bbox_global']; cv2.rectangle(dbg,(x1,y1),(x2,y2),(200,0,200),3)
    cv2.imwrite(str(a.output/'debug_stage2.png'),dbg)
    summary={'pages':len(result['pages']),'lines':sum(x['line_count'] for x in result['pages']),
             'regions':sum(len(x['regions']) for x in result['pages']),'paragraphs':sum(sum(r['type']=='paragraph' for r in x['regions']) for x in result['pages']),
             'json':str(a.output/'page_structure_stage2.json'),'debug':str(a.output/'debug_stage2.png')}
    print(json.dumps(summary,indent=2))
if __name__=='__main__': main()

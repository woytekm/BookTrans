#!/usr/bin/env python3
"""BookTrans stage 2.1: OCR lines, typography candidates and paragraph decomposition.

The important change from stage 2.0 is paragraph detection. Tesseract's line
bounding boxes are retained, but paragraph boundaries are inferred from the
original scan's horizontal ink projection. This is much more reliable for old
books because Tesseract boxes can overlap vertically or use inconsistent box
heights.
"""
import argparse, json, re
from pathlib import Path
import cv2, numpy as np, pytesseract
from PIL import Image
from pytesseract import Output

from imgutil import crop_for_ocr, load_pages, paper_color
from illustrations import mask_box


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


def ocr_lines(page, bbox, lang, context=10, scale=2.0):
    """OCR one coarse region of ``page`` and return line objects in page coordinates.

    The region is cut with real context pixels and surrounded by paper-coloured
    padding (see imgutil.crop_for_ocr); a bare crop made Tesseract drop or
    misread line-initial letters that touch the crop border.
    """
    crop,offset=crop_for_ocr(page,bbox,context=context,pad=24)
    gray=cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY)
    # The sample scans are ~135 DPI: body glyphs are ~8 px, captions ~6 px (x-height),
    # far below what Tesseract likes. Upscaling the crop first improves small text
    # (captions) clearly and leaves body text unchanged. Boxes are mapped back.
    if scale!=1: gray=cv2.resize(gray,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
    gray=cv2.createCLAHE(clipLimit=1.5,tileGridSize=(8,8)).apply(gray)
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
        out.append({'bbox':[x1+ox,y1+oy,x2+ox,y2+oy],'text':txt,
                    'glyph_height':round(float(np.median(hs)),1) if hs else 0,
                    'confidence':round(float(np.mean([float(d['conf'][i]) for i in ids])),1),
                    'uppercase_ratio':round(caps,2),'word_count':len(ids)})
    return sorted(out,key=lambda x:(x['bbox'][1],x['bbox'][0]))


def visual_text_bands(page_gray, bbox):
    """Return horizontal ink bands inside a text bbox in page coordinates.

    Illumination normalization is used because the paper is not perfectly flat
    or uniformly lit. The threshold is deliberately based on page width so that
    small isolated specks don't become lines.
    """
    x1,y1,x2,y2=bbox
    crop=page_gray[y1:y2,x1:x2]
    if crop.size==0: return []
    bg=cv2.GaussianBlur(crop,(0,0),15)
    norm=cv2.divide(crop,bg,scale=220)
    _,bw=cv2.threshold(norm,170,255,cv2.THRESH_BINARY_INV)
    proj=(bw>0).sum(axis=1)
    threshold=max(8,int(crop.shape[1]*0.025))
    on=proj>threshold
    bands=[]; s=None
    for i,v in enumerate(on):
        if v and s is None: s=i
        elif not v and s is not None:
            bands.append([s,i-1]); s=None
    if s is not None: bands.append([s,len(on)-1])
    merged=[]
    for a,b in bands:
        if merged and a-merged[-1][1]-1<=2:
            merged[-1][1]=b
        else:
            merged.append([a,b])
    return [[x1,a+y1,x2,b+y1] for a,b in merged]


def assign_visual_bands(lines, bands):
    if not lines or not bands: return lines
    centers=np.array([(b[1]+b[3])/2 for b in bands],dtype=float)
    for ln in lines:
        cy=(ln['bbox'][1]+ln['bbox'][3])/2
        j=int(np.argmin(np.abs(centers-cy)))
        ln['_visual_band']=j
        ln['_visual_band_bbox']=bands[j]
    return lines


# Caption lines are smaller than body text, but an accent (Ä, Ü) in a small-caps
# caption makes OCR report a taller line, so allow a little headroom.
CAPTION_MAX_REL = 1.15


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
            if ln['glyph_height']>=body*CAPTION_MAX_REL: continue
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
            if h<body*CAPTION_MAX_REL and is_near(ln['bbox'],im['bbox'],max(26,body*2.2)):
                typ='caption_line'; reason='beside/below an illustration and smaller than body'; conf=.76; break
        ln.update({'type':typ,'classification_reason':reason,'classification_confidence':conf,
                   'relative_size':round(h/max(body,1),2),'style':'unknown'})
    absorb_caption_continuations(lines,body)
    return lines,body


def split_body_lines_into_paragraphs(lines, page_gray, text_bbox):
    """Return body-line groups using visual inter-line spacing.

    The scan is authoritative for paragraph spacing. We calculate the median
    visual gap between neighbouring printed lines and mark a break when the
    gap is substantially larger than normal. Tesseract's own bbox gap is kept
    as a secondary signal but is *not* used as the primary measurement.
    """
    body=[l for l in lines if l['type']=='body_line']
    if not body: return []
    bands=visual_text_bands(page_gray,text_bbox)
    assign_visual_bands(body,bands)

    # One OCR line normally maps to one visual band. If not, retain the closest
    # band and let the OCR ordering remain authoritative.
    band_ids=[l.get('_visual_band') for l in body]
    valid=[i for i in band_ids if i is not None]
    if len(valid)<2:
        return [body]

    # Calculate visual gaps only between successive *used* bands.
    gaps=[]
    for i in range(1,len(body)):
        a=body[i-1].get('_visual_band'); b=body[i].get('_visual_band')
        if a is None or b is None or b==a: continue
        if b<a: continue
        # If a line was skipped by OCR, sum all intervening gaps rather than
        # pretending it is a paragraph break.
        gap=bands[b][1]-bands[a][3]
        gaps.append(max(0,gap))
    normal=float(np.median(gaps)) if gaps else 0.0
    # A ratio of ~1.5 works well on the supplied book: normal gaps are about
    # 6-9 px, while clear paragraph gaps are 10-35 px.
    threshold=max(3.0,normal*1.35)

    groups=[]; cur=[body[0]]
    for i,ln in enumerate(body[1:],1):
        prev=body[i-1]
        a=prev.get('_visual_band'); b=ln.get('_visual_band')
        gap=0.0
        if a is not None and b is not None and b>a:
            gap=max(0,bands[b][1]-bands[a][3])
        # Preserve the diagnostic decision on the line starting the new paragraph.
        ln['paragraph_break'] = bool(gap>threshold)
        ln['paragraph_gap'] = round(float(gap),1)
        ln['paragraph_gap_threshold'] = round(float(threshold),1)
        if gap>threshold:
            groups.append(cur); cur=[ln]
        else:
            cur.append(ln)
    groups.append(cur)

    # Remove internal helper fields; keep useful paragraph diagnostics.
    for g in groups:
        for ln in g:
            ln.pop('_visual_band',None); ln.pop('_visual_band_bbox',None)
    return groups


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


def group_caption_lines(lines):
    """Group caption lines into captions, one per column.

    Lines belong to the same caption when they are stacked (small vertical gap)
    and share a column (horizontal overlap or the same left edge). This keeps
    five side-by-side figure captions apart and keeps two-line captions whole.
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


def group_semantic_regions(lines, body, page_gray, text_bbox):
    """Group lines while splitting body text at visual paragraph breaks."""
    if not lines:return []
    cap=[l for l in lines if l['type']=='caption_line']
    if cap:
        regs=[make_region(g,0,'caption_line') for g in group_caption_lines(cap)]
        rest=[l for l in lines if l['type']!='caption_line']
        regs.extend(group_semantic_regions(rest,body,page_gray,text_bbox) if rest else [])
        regs.sort(key=lambda r:(r['bbox'][1],r['bbox'][0]))
        for i,r in enumerate(regs,1): r['id']=i
        return regs
    # First identify paragraph groups among body lines belonging to this coarse
    # text area. Other semantic line types remain grouped by type transitions.
    body_groups=split_body_lines_into_paragraphs(lines,page_gray,text_bbox)
    body_lookup={id(l):gidx for gidx,g in enumerate(body_groups) for l in g}
    out=[]; cur=[]; last_kind=None; last_body_group=None
    for ln in lines:
        kind=ln['type']
        if kind=='body_line':
            bg=body_lookup[id(ln)]
            if cur and (last_kind!='body_line' or bg!=last_body_group):
                out.append(make_region(cur,len(out)+1,last_kind))
                cur=[]
            cur.append(ln); last_kind=kind; last_body_group=bg
        else:
            if cur and last_kind!=kind:
                out.append(make_region(cur,len(out)+1,last_kind)); cur=[]
            cur.append(ln); last_kind=kind; last_body_group=None
    if cur: out.append(make_region(cur,len(out)+1,last_kind))
    return out


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('input',type=Path); ap.add_argument('--stage1-json',type=Path,default=Path('out1/page_structure.json'))
    ap.add_argument('-o','--output',type=Path,default=Path('stage2_out')); ap.add_argument('--lang',default='deu')
    ap.add_argument('--ocr-scale',type=float,default=2.0,help='upscale factor applied to OCR crops (default 2.0; 1 = off)'); a=ap.parse_args()
    base=json.loads(a.stage1_json.read_text(encoding='utf8'))
    # Analyse the exact (deskewed) page images Stage 1 saved; ``a.input`` is only
    # the fallback for old Stage 1 JSON without page images.
    page_imgs=load_pages(base,a.stage1_json.parent,a.input)
    src=np.hstack(page_imgs)
    a.output.mkdir(parents=True,exist_ok=True); result={'schema_version':'0.2.1','source_file':str(a.input),'pages':[]}
    for p,page in zip(base['pages'],page_imgs):
        side=p['side']; xoff=p['x_offset']; page_gray=cv2.cvtColor(page,cv2.COLOR_RGB2GRAY)
        page_regions=[]; all_lines=[]; image_boxes=[]; text_bboxes=[]
        # Text is OCR'd from a copy of the page with every illustration painted
        # over in paper colour: OCR crops carry real context pixels, and the edge
        # of a neighbouring drawing used to come back as junk "captions".
        ocr_page=page.copy(); paper=paper_color(page)
        for r in p['regions']:
            if 'image' in r['type'] or 'drawing' in r['type']:
                x1,y1,x2,y2=mask_box(r); ocr_page[max(0,y1):y2,max(0,x1):x2]=paper
        for r in p['regions']:
            if r['type'] not in ('text','heading','caption','text_candidate'):
                if 'image' in r['type'] or 'drawing' in r['type']:
                    image_boxes.append({'bbox':r['bbox'],'type':'illustration'})
                continue
            ls=ocr_lines(ocr_page,r['bbox'],a.lang,scale=a.ocr_scale); all_lines.extend(ls); text_bboxes.append(r['bbox'])
        all_lines=dedupe_lines(all_lines)
        all_lines.sort(key=lambda z:(z['bbox'][1],z['bbox'][0]))
        lines,body=classify_lines(all_lines,image_boxes,page.shape[0])
        for i,ln in enumerate(lines):
            if ln['type']!='caption_line' and i+1<len(lines) and ln['bbox'][1]<page.shape[0]*.55 and ln['bbox'][0]>page.shape[1]*.45:
                if lines[i+1]['type']=='heading_line':
                    ln['type']='author_byline_candidate'; ln['classification_reason']='right-aligned line immediately before heading'; ln['classification_confidence']=.72
        # Each original Stage-1 text box is processed independently so columns
        # and unrelated text areas cannot create paragraph boundaries together.
        regs=[]; claimed=set()
        for tb in text_bboxes:
            x1,y1,x2,y2=tb
            # A line is claimed by the first coarse box that contains it, so
            # overlapping boxes cannot emit it twice.
            local=[ln for ln in lines if id(ln) not in claimed and x1-8<=ln['bbox'][0]<=x2+8 and y1-8<=ln['bbox'][1]<=y2+8]
            claimed.update(id(ln) for ln in local)
            if local: regs.extend(group_semantic_regions(local,body,page_gray,tb))
        # Captions/author lines not belonging to a Stage-1 text box.
        assigned={id(l) for r in regs for l in r['lines']}
        leftovers=[l for l in lines if id(l) not in assigned]
        if leftovers:
            regs.extend(group_semantic_regions(leftovers,body,page_gray,[0,0,page.shape[1],page.shape[0]]))
        for im in image_boxes: regs.append(im)
        regs.sort(key=lambda r:(r['bbox'][1],r['bbox'][0]))
        for i,r in enumerate(regs,1): r['id']=i
        result['pages'].append({'page':p['page'],'side':side,'x_offset':xoff,'width':p['width'],'height':p['height'],
                                'skew_deg':p.get('skew_deg'),'deskew':p.get('deskew'),'page_image':p.get('page_image'),
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

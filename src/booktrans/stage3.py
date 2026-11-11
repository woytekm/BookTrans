#!/usr/bin/env python3
"""BookTrans stage 3: classify page objects and associate OCR with illustrations.

This stage deliberately does not edit illustrations. OCR lines that fall inside
an illustration are retained as evidence, but excluded from translation and
page-text reconstruction for now.
"""
import argparse, json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from imgutil import load_pages


def area(b):
    return max(0, b[2]-b[0]) * max(0, b[3]-b[1])


def intersection(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    return [x1,y1,x2,y2] if x2>x1 and y2>y1 else [0,0,0,0]


def overlap_ratio(a, b):
    return area(intersection(a,b)) / max(1, area(a))


def center_inside(a, b):
    cx=(a[0]+a[2])/2; cy=(a[1]+a[3])/2
    return b[0] <= cx <= b[2] and b[1] <= cy <= b[3]


def associate_line(line, illustrations, threshold=0.30):
    matches=[]
    for im in illustrations:
        ov=overlap_ratio(line["bbox"], im["bbox"])
        if center_inside(line["bbox"], im["bbox"]) or ov >= threshold:
            matches.append((1 if center_inside(line["bbox"], im["bbox"]) else 0, ov, im))
    return max(matches, key=lambda x:(x[0],x[1]))[2] if matches else None


def stage1_illustration_candidates(stage1_page):
    """Recover illustration candidates, including obvious false text regions.

    Stage 1 already emits image_or_drawing candidates. A second conservative
    rule catches the supplied technical-drawing case: a very large region that
    Stage 1 called heading/text but whose OCR confidence is very low. This is a
    classification hypothesis, not proof that the region is an illustration.
    """
    W, H = stage1_page['width'], stage1_page['height']
    page_area=W*H
    out=[]
    for r in stage1_page.get('regions', []):
        typ=r.get('type','')
        b=r['bbox']
        if 'image' in typ or 'drawing' in typ:
            out.append((r, 'stage1_image_or_drawing', 0.95))
            continue
        frac=area(b)/max(1,page_area)
        conf=float(r.get('ocr_confidence',100))
        if frac >= 0.18 and conf < 45 and typ in ('heading','text','text_candidate','caption'):
            out.append((r, 'large_region_with_low_ocr_confidence', 0.80))
    return out


def classify(data, stage1, threshold):
    for page in data.get('pages', []):
        sp=next((p for p in stage1['pages'] if p['page']==page['page']), None)
        candidates=stage1_illustration_candidates(sp) if sp else []
        illustrations=[]
        for i,(src,reason,conf) in enumerate(candidates,1):
            aid=f"p{page['page']}-ill-{i}"
            im={'object_id':aid, 'type':'illustration', 'object_class':'illustration',
                'bbox':list(src['bbox']), 'classification_confidence':conf,
                'classification_reason':reason, 'translation_status':'exclude',
                'association_note':'Original illustration retained unchanged in current stage.'}
            illustrations.append(im)

        # Prefer explicitly detected illustrations if a heuristic candidate
        # overlaps one of them heavily; keep only the explicit object.
        filtered=[]
        for im in illustrations:
            duplicate=False
            for kept in filtered:
                if overlap_ratio(im['bbox'],kept['bbox']) > .60 or overlap_ratio(kept['bbox'],im['bbox']) > .60:
                    if kept['classification_reason']=='stage1_image_or_drawing': duplicate=True
                    elif im['classification_reason']=='stage1_image_or_drawing':
                        filtered.remove(kept)
                    else: duplicate=True
                    break
            if not duplicate: filtered.append(im)
        illustrations=filtered

        for i,r in enumerate(page.get('regions', []),1):
            r['object_id']=f"p{page['page']}-reg-{i}"

        counts={'page_text':0,'illustration_text':0}
        for r in page.get('regions', []):
            lines=r.get('lines',[])
            if not lines:
                r['content_context']='page_text' if r.get('type')!='illustration' else 'illustration'
                r['translation_status']='translate' if r['content_context']=='page_text' else 'exclude'
                continue
            assoc=[]
            for ln in lines:
                matches=[]
                for im in illustrations:
                    ov=overlap_ratio(ln['bbox'],im['bbox'])
                    if center_inside(ln['bbox'],im['bbox']) or ov >= threshold:
                        matches.append((1 if center_inside(ln['bbox'],im['bbox']) else 0,ov,im))
                if matches:
                    _,ov,im=max(matches,key=lambda x:(x[0],x[1]))
                    ln['content_context']='illustration_text'
                    ln['associated_illustration_id']=im['object_id']
                    ln['translation_status']='exclude'
                    ln['association_reason']='OCR line lies inside or overlaps illustration geometry'
                    assoc.append(im['object_id']); counts['illustration_text']+=1
                else:
                    ln['content_context']='page_text'
                    ln['translation_status']='translate'
                    counts['page_text']+=1
            ids=sorted(set(assoc))
            if not ids:
                r['content_context']='page_text'; r['translation_status']='translate'
            elif len(assoc)==len(lines):
                r['content_context']='illustration_text'; r['translation_status']='exclude'
                r['associated_illustration_ids']=ids
            else:
                r['content_context']='mixed'; r['translation_status']='review'
                r['associated_illustration_ids']=ids

        # Add illustration objects after page regions. They are separate from
        # semantic text regions and therefore cannot accidentally be translated.
        page['illustrations']=illustrations
        page['classification']={
            'line_counts':counts,
            'illustration_count':len(illustrations),
            'association_policy':'geometry_overlap_or_containment',
            'illustration_text_policy':'preserve_ocr_exclude_translation'
        }
    return data


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('stage2_json',type=Path)
    ap.add_argument('--stage1-json',type=Path,required=True)
    ap.add_argument('-o','--output',type=Path,default=Path('stage3_out'))
    ap.add_argument('--line-overlap-threshold',type=float,default=.30)
    a=ap.parse_args()
    data=json.loads(a.stage2_json.read_text(encoding='utf8'))
    stage1=json.loads(a.stage1_json.read_text(encoding='utf8'))
    data=classify(data,stage1,a.line_overlap_threshold)
    data['schema_version']='0.3.0'
    data['stage']='classification_and_association'
    data['policy']={'illustrations':'preserve_original_exclude_reconstruction',
                    'illustration_text':'preserve_ocr_exclude_translation'}
    a.output.mkdir(parents=True,exist_ok=True)
    out=a.output/'page_structure_stage3.json'
    out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
    # Diagnostic overlay on the original spread: illustration geometry is
    # emphasized, and OCR lines are coloured by translation eligibility.
    try:
        src=np.hstack(load_pages(stage1,a.stage1_json.parent,data['source_file']))
        dbg=cv2.cvtColor(src,cv2.COLOR_RGB2BGR).copy()
        for p in data['pages']:
            ox=p['x_offset']
            for im in p.get('illustrations',[]):
                x1,y1,x2,y2=im['bbox']; x1+=ox; x2+=ox
                cv2.rectangle(dbg,(x1,y1),(x2,y2),(0,0,255),4)
                cv2.putText(dbg,im['object_id'],(x1+4,max(18,y1-6)),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,0,255),2,cv2.LINE_AA)
            for r in p.get('regions',[]):
                for ln in r.get('lines',[]):
                    x1,y1,x2,y2=ln['bbox']; x1+=ox; x2+=ox
                    c=(0,0,255) if ln.get('content_context')=='illustration_text' else (0,150,0)
                    cv2.rectangle(dbg,(x1,y1),(x2,y2),c,1)
        cv2.imwrite(str(a.output/'debug_stage3.png'),dbg)
    except Exception as exc:
        print(f'warning: could not create debug overlay: {exc}')

    summary={'pages':len(data.get('pages',[])),'illustrations':sum(p['classification']['illustration_count'] for p in data['pages']),
             'page_text_lines':sum(p['classification']['line_counts']['page_text'] for p in data['pages']),
             'illustration_text_lines':sum(p['classification']['line_counts']['illustration_text'] for p in data['pages']),
             'mixed_regions':sum(r.get('content_context')=='mixed' for p in data['pages'] for r in p.get('regions',[])),
             'output':str(out),'debug':str(a.output/'debug_stage3.png')}
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()

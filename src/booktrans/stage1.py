#!/usr/bin/env python3
import argparse, json, math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
import pytesseract
from pytesseract import Output


def load_rgb(path):
    im = Image.open(path).convert('RGB')
    return np.array(im)


def detect_gutter(rgb):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    a, b = int(w * .38), int(w * .62)
    # Median brightness is less sensitive than a mean to printed text.
    # The binding forms a broad, consistently darker vertical band.
    profile = np.median(gray[60:h-60, a:b], axis=0).astype(np.float32)
    profile = cv2.GaussianBlur(profile.reshape(1, -1), (0, 0), 7).ravel()
    return a + int(np.argmin(profile))

def split_spread(rgb):
    h, w = rgb.shape[:2]
    gutter = detect_gutter(rgb)
    left = rgb[:, :gutter]
    right = rgb[:, gutter:]
    return [("L", left, 0), ("R", right, gutter)]


def ocr_lines(rgb, lang='deu'):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    # Gentle normalization; retain the original image for final output.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    norm = clahe.apply(gray)
    data = pytesseract.image_to_data(norm, lang=lang, config='--psm 3', output_type=Output.DICT)

    words = []
    n = len(data['text'])
    for i in range(n):
        txt = data['text'][i].strip()
        conf = float(data['conf'][i]) if data['conf'][i] != '-1' else -1
        if not txt or conf < 15:
            continue
        x, y, w, h = [int(data[k][i]) for k in ('left','top','width','height')]
        words.append({
            'text': txt, 'conf': round(conf, 1),
            'x': x, 'y': y, 'w': w, 'h': h,
            'block': int(data['block_num'][i]),
            'par': int(data['par_num'][i]),
            'line': int(data['line_num'][i])
        })

    # Tesseract's line identifiers are good enough for a first prototype.
    groups = {}
    for wd in words:
        key = (wd['block'], wd['par'], wd['line'])
        groups.setdefault(key, []).append(wd)

    lines = []
    for key, ws in groups.items():
        x1 = min(w['x'] for w in ws); y1 = min(w['y'] for w in ws)
        x2 = max(w['x'] + w['w'] for w in ws); y2 = max(w['y'] + w['h'] for w in ws)
        text = ' '.join(w['text'] for w in ws)
        conf = sum(w['conf'] for w in ws) / len(ws)
        lines.append({'x':x1,'y':y1,'w':x2-x1,'h':y2-y1,'text':text,'conf':round(conf,1)})

    lines.sort(key=lambda r: (r['y'], r['x']))
    return lines


def merge_lines_into_regions(lines, page_w, page_h):
    """Turn OCR lines into coarse layout regions.

    This is intentionally conservative: for the first stage a paragraph/column-sized
    region is preferable to dozens of tiny boxes.  Later we can replace this with a
    dedicated document-layout model.
    """
    if not lines:
        return []
    heights = [r['h'] for r in lines]
    med_h = max(1.0, float(np.median(heights)))
    regions = []
    cur = [lines[0]]

    def overlap_ratio(a, b):
        ax1, ax2 = a['x'], a['x'] + a['w']
        bx1, bx2 = b['x'], b['x'] + b['w']
        inter = max(0, min(ax2,bx2)-max(ax1,bx1))
        return inter / max(1, min(a['w'], b['w']))

    for line in lines[1:]:
        prev = cur[-1]
        gap = line['y'] - (prev['y'] + prev['h'])
        ov = overlap_ratio(prev, line)
        # Same text flow: normal line spacing and significant horizontal overlap.
        same_flow = gap <= med_h * 2.4 and ov >= 0.35
        if same_flow:
            cur.append(line)
        else:
            regions.append(cur)
            cur = [line]
    regions.append(cur)

    out = []
    for idx, rs in enumerate(regions, 1):
        x1 = min(r['x'] for r in rs); y1 = min(r['y'] for r in rs)
        x2 = max(r['x']+r['w'] for r in rs); y2 = max(r['y']+r['h'] for r in rs)
        text = '\n'.join(r['text'] for r in rs)
        avg_conf = sum(r['conf'] for r in rs)/len(rs)
        avg_line_h = sum(r['h'] for r in rs)/len(rs)
        nlines = len(rs)

        if nlines <= 3 and avg_line_h >= med_h * 1.25:
            kind = 'heading'
        elif avg_line_h <= med_h * 0.78:
            kind = 'caption'
        else:
            kind = 'text'
        out.append({
            'id': idx, 'type': kind,
            'bbox': [x1,y1,x2,y2],
            'ocr_confidence': round(avg_conf,1),
            'text': text,
            'lines': rs
        })
    return out

def detect_image_regions(rgb, text_regions):
    """Detect photographs/drawings as large contiguous dark/tonal regions.

    Raw threshold contours are used here instead of aggressive morphology. On the
    supplied book this cleanly separates the photograph from its small caption.
    """
    h,w=rgb.shape[:2]
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    # A dark-pixel contour is a useful first approximation for halftone photos and
    # line drawings. Otsu is deliberately avoided because the paper is unevenly lit.
    bw=cv2.threshold(gray,170,255,cv2.THRESH_BINARY_INV)[1]
    # Don't let the physical page border become a candidate.
    bw[:65,:]=0; bw[-65:,:]=0; bw[:,:65]=0; bw[:,-65:]=0
    contours,_=cv2.findContours(bw,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    candidates=[]
    for c in contours:
        x,y,ww,hh=cv2.boundingRect(c)
        if ww<120 or hh<120 or ww*hh < w*h*.01:
            continue
        density=float(np.mean(gray[y:y+hh,x:x+ww] < 170))
        if density < .35:
            continue
        # If a candidate substantially overlaps an already detected text region,
        # it is probably a text block rather than an image.
        box_area=ww*hh; max_overlap=0
        for r in text_regions:
            a,b,c2,d=r['bbox']; ix=max(0,min(x+ww,c2)-max(x,a)); iy=max(0,min(y+hh,d)-max(y,b))
            max_overlap=max(max_overlap,(ix*iy)/box_area)
        if max_overlap < .20:
            candidates.append({'type':'image_or_drawing','bbox':[int(x),int(y),int(x+ww),int(y+hh)],'ink_density':round(density,3)})
    candidates.sort(key=lambda r:(r['bbox'][2]-r['bbox'][0])*(r['bbox'][3]-r['bbox'][1]),reverse=True)
    return candidates

def detect_text_regions(rgb, lang='deu'):
    """Find coarse printed-text blocks, then OCR each block.

    Morphological grouping is much more stable for this book than grouping
    individual Tesseract lines: a whole paragraph/column becomes one region.
    """
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    bw=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY_INV,41,15)
    # Remove obvious scan border and gutter artifacts.
    h,w=gray.shape
    
    # Ignore the physical book edge; content starts well inside it on these scans.
    margin=65
    bw[:margin,:]=0; bw[-margin:,:]=0; bw[:,:margin]=0; bw[:,-margin:]=0
    # Connect characters into lines and nearby lines into paragraph-sized blocks.
    bw=cv2.morphologyEx(bw,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_RECT,(21,3)))
    grouped=cv2.dilate(bw,cv2.getStructuringElement(cv2.MORPH_RECT,(7,13)))
    n,labels,stats,_=cv2.connectedComponentsWithStats(grouped,8)
    candidates=[]
    for i in range(1,n):
        x,y,ww,hh,area=stats[i]
        if area < 600 or ww < 100 or hh < 12:
            continue
        if x+ww < 40 or x > w-40:
            continue
        # Printed text blocks have modest dark-pixel density. Very dense regions
        # are more likely photographs and are handled separately.
        density=float(np.mean(gray[y:y+hh,x:x+ww] < 170))
        if density > .28:
            continue
        candidates.append((x,y,ww,hh,density))

    # Merge candidates that are clearly parts of one text flow.
    candidates.sort(key=lambda z:(z[1],z[0]))
    merged=[]
    for c in candidates:
        x,y,ww,hh,d=c; x2=x+ww; y2=y+hh
        merged_into=False
        for j,m in enumerate(merged):
            mx1,my1,mx2,my2=m['bbox']
            vert_gap=max(0,max(my1,y1 if False else y)-min(my2,y))
            horiz_overlap=max(0,min(mx2,x2)-max(mx1,x)) / max(1,min(mx2-mx1,ww))
            # same column / nearby text blocks
            if horiz_overlap>.35 and y <= my2+28 and y2 >= my1:
                merged[j]['bbox']=[min(mx1,x),min(my1,y),max(mx2,x2),max(my2,y2)]
                merged_into=True; break
        if not merged_into:
            merged.append({'bbox':[x,y,x2,y2]})

    regions=[]
    for idx,r in enumerate(merged,1):
        x1,y1,x2,y2=r['bbox']
        crop=rgb[max(0,y1-4):min(h,y2+4),max(0,x1-4):min(w,x2+4)]
        # psm 6 works well for paragraph/title blocks; preserve line breaks via TSV.
        text=pytesseract.image_to_string(crop,lang=lang,config='--psm 6').strip()
        data=pytesseract.image_to_data(crop,lang=lang,config='--psm 6',output_type=Output.DICT)
        hs=[]; confs=[]
        for i,t in enumerate(data['text']):
            if t.strip() and data['conf'][i] != '-1':
                confs.append(float(data['conf'][i])); hs.append(int(data['height'][i]))
        avg_h=float(np.median(hs)) if hs else 0
        avg_conf=sum(confs)/len(confs) if confs else 0
        regions.append({'id':idx,'type':'text_candidate','bbox':[x1,y1,x2,y2],
                        'ocr_confidence':round(avg_conf,1),'ocr_line_height':round(avg_h,1),'text':text})
    return regions

def process_page(rgb, side, x_offset, lang):
    text_regions = detect_text_regions(rgb, lang)
    image_regions = detect_image_regions(rgb, text_regions)
    # Estimate body line height and classify text regions by typography scale.
    hs=[r['ocr_line_height'] for r in text_regions if r['ocr_line_height']>0]
    body_h=float(np.median(hs)) if hs else 20
    regions=[]
    for r in text_regions:
        h=r['ocr_line_height']
        if h >= body_h*1.35 and len(r['text'].splitlines()) <= 4:
            r['type']='heading'
        elif h <= body_h*.72 and len(r['text'].splitlines()) <= 5:
            r['type']='caption'
        else:
            r['type']='text'
        r['side']=side
        x1,y1,x2,y2=r['bbox']; r['bbox_global']=[x1+x_offset,y1,x2+x_offset,y2]
        regions.append(r)
    for i,r in enumerate(image_regions,len(regions)+1):
        r['id']=i; r['side']=side
        x1,y1,x2,y2=r['bbox']; r['bbox_global']=[x1+x_offset,y1,x2+x_offset,y2]
        regions.append(r)
    regions.sort(key=lambda r:(r['bbox'][1],r['bbox'][0]))
    return regions

def draw_debug(rgb, regions, out_path):
    img=cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR).copy()
    for r in regions:
        x1,y1,x2,y2=r['bbox_global']
        color=(0,180,0) if r['type']=='text' else ((255,120,0) if r['type']=='heading_or_caption' else (180,0,220))
        cv2.rectangle(img,(x1,y1),(x2,y2),color,3)
        label=f"{r['id']} {r['type']}"
        cv2.rectangle(img,(x1,max(0,y1-28)),(min(img.shape[1]-1,x1+250),y1),color,-1)
        cv2.putText(img,label,(x1+5,max(18,y1-7)),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1,cv2.LINE_AA)
    cv2.imwrite(str(out_path),img)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('input', type=Path)
    ap.add_argument('-o','--output', type=Path, default=Path('output'))
    ap.add_argument('--lang', default='deu')
    args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    rgb=load_rgb(args.input)
    spread={'file':str(args.input),'width':rgb.shape[1],'height':rgb.shape[0], 'pages':[]}
    for side,page,offset in split_spread(rgb):
        page_no=len(spread['pages'])+1
        regions=process_page(page,side,offset,args.lang)
        # Save cropped page image for later stages.
        Image.fromarray(page).save(args.output/f"page_{page_no:02d}_{side}.png")
        spread['pages'].append({'page':page_no,'side':side,'x_offset':offset,'width':page.shape[1],'height':page.shape[0],'regions':regions})
    # Debug overlay on the original spread.
    all_regions=[r for p in spread['pages'] for r in p['regions']]
    draw_debug(rgb,all_regions,args.output/'debug_layout.png')
    with open(args.output/'page_structure.json','w',encoding='utf-8') as f:
        json.dump(spread,f,ensure_ascii=False,indent=2,default=lambda o: o.item() if hasattr(o, 'item') else str(o))
    print(json.dumps({'input':str(args.input),'pages':len(spread['pages']),'regions':len(all_regions),'output':str(args.output/'page_structure.json')},indent=2))

if __name__=='__main__': main()

from stage3 import overlap_ratio, center_inside, associate_line, stage1_illustration_candidates, classify


def test_overlap_and_center():
    a=[10,10,30,30]; b=[20,20,40,40]
    assert center_inside(a,b) is True
    assert overlap_ratio(a,b) == 0.25


def test_line_associates_with_illustration():
    line={'bbox':[20,20,40,30]}
    ims=[{'object_id':'ill-1','bbox':[10,10,50,50]}]
    assert associate_line(line,ims)['object_id']=='ill-1'


def test_only_explicit_illustrations_are_candidates():
    """A large low-confidence *text* region is no longer promoted to an illustration."""
    page={'width':1000,'height':1000,'regions':[
        {'type':'heading','bbox':[100,100,600,600],'ocr_confidence':20},
        {'type':'image_or_drawing','detector':'line_art','bbox':[100,700,300,900]},
    ]}
    c=stage1_illustration_candidates(page)
    assert len(c)==1
    assert c[0][0]['bbox']==[100,700,300,900]
    assert c[0][1]=='stage1_line_art'


def test_caption_line_is_translated_not_excluded():
    data={'pages':[{'page':1,'regions':[
        {'type':'caption','bbox':[100,905,300,915],'lines':[
            {'type':'caption_line','bbox':[100,905,300,915],'text':'38 ABB'}]}]}]}
    stage1={'pages':[{'page':1,'width':1000,'height':1000,'regions':[
        {'type':'image_or_drawing','detector':'line_art','bbox':[100,700,300,900]}]}]}
    out=classify(data,stage1,.3)
    ln=out['pages'][0]['regions'][0]['lines'][0]
    assert ln['content_context']=='caption' and ln['translation_status']=='translate'
    assert ln['associated_illustration_id']=='p1-ill-1'
    assert out['pages'][0]['regions'][0]['translation_status']=='translate'

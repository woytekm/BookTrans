from stage3 import overlap_ratio, center_inside, associate_line, stage1_illustration_candidates


def test_overlap_and_center():
    a=[10,10,30,30]; b=[20,20,40,40]
    assert center_inside(a,b) is True
    assert overlap_ratio(a,b) == 0.25


def test_line_associates_with_illustration():
    line={'bbox':[20,20,40,30]}
    ims=[{'object_id':'ill-1','bbox':[10,10,50,50]}]
    assert associate_line(line,ims)['object_id']=='ill-1'


def test_low_confidence_large_region_is_candidate():
    page={'width':1000,'height':1000,'regions':[
        {'type':'heading','bbox':[100,100,600,600],'ocr_confidence':20}
    ]}
    c=stage1_illustration_candidates(page)
    assert len(c)==1
    assert c[0][1]=='large_region_with_low_ocr_confidence'

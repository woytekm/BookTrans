import numpy as np

from marks import find_marks


def test_rule_and_page_number_found_but_not_text_or_page_edge():
    img = np.full((600, 500, 3), 225, np.uint8)
    img[300:302, 60:440] = 195                      # faint hairline rule inside the content area
    img[540:552, 240:262] = 40                      # page number below the text
    img[100:112, 60:300] = 30                       # a text line (inside a text box: must be ignored)
    img[598:600, :] = 20                            # page edge
    boxes = [[60, 100, 300, 112]]
    cols = [{'left': 60, 'right': 440}]
    found = find_marks(img, boxes, [60, 100, 440, 480], cols)
    roles = sorted(m['role'] for m in found)
    assert roles == ['page_mark', 'rule']
    rule = next(m for m in found if m['role'] == 'rule')
    assert rule['bbox'][0] <= 61 and rule['bbox'][2] >= 439


def test_letter_spaced_capitals_are_not_a_rule():
    """'Z I E G E N H A L S' in faint, letter-spaced capitals closes into a long thin bar; it is not a hairline."""
    img = np.full((600, 500, 3), 225, np.uint8)
    for k in range(14):                              # 14 capitals, 8 px tall, 4 px gaps
        x = 80 + k * 14
        img[300:308, x:x + 2] = 200; img[300:302, x:x + 10] = 200; img[306:308, x:x + 10] = 200
    found = find_marks(img, [], [60, 100, 440, 480], [{'left': 60, 'right': 440}])
    assert not [m for m in found if m['role'] == 'rule']


def test_is_rule_band_and_coverage():
    from marks import is_rule
    m = np.zeros((12, 200), np.uint8)
    m[5:8, :] = 1
    assert is_rule(m)
    m2 = np.zeros((12, 200), np.uint8)
    m2[2:10, ::7] = 1                                 # strokes over the glyph height with gaps
    assert not is_rule(m2)


def test_a_plate_page_keeps_its_page_number_far_below_the_pictures():
    """A page with pictures and captions only has no text columns, and its content ends well above the page number."""
    img = np.full((1300, 880, 3), 225, np.uint8)
    img[170:960, 250:630] = 90                              # the pictures (masked as boxes)
    img[1150:1170, 600:606] = 40; img[1150:1170, 612:618] = 40; img[1150:1153, 600:618] = 40   # "69"
    img[1080:1083, 500:503] = 60                            # a speck between
    img[150:152, 250:330] = 200                             # a faint picture edge above (not a rule on a plate)
    found = find_marks(img, [[250, 170, 630, 960], [150, 963, 626, 978]], [150, 170, 630, 978], [])
    assert [m['role'] for m in found] == ['page_mark']
    x1, y1, x2, y2 = found[0]['bbox']
    assert x1 <= 600 and x2 >= 618 and y1 <= 1150 and y2 >= 1170

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

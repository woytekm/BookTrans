"""Illustration detector on synthetic pages: text is never an illustration,
a line drawing is found as one figure, and a caption under it stays text."""
import cv2
import numpy as np

import illustrations as ill


def glyph_line(img, x0, x1, y, rng):
    """A line of word-like groups of letter-sized rectangles (glyph height ~9 px)."""
    x = x0
    while x < x1:
        for _ in range(int(rng.integers(3, 9))):
            w = int(rng.integers(3, 8)); h = int(rng.integers(8, 11))
            if x + w >= x1:
                return
            cv2.rectangle(img, (x, y + 10 - h), (x + w, y + 10), 25, -1)
            x += w + int(rng.integers(1, 3))
        x += int(rng.integers(6, 10))


def text_page(w=900, h=1300, n_lines=30, y0=700, seed=3):
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 235, np.uint8)
    for i in range(n_lines):
        glyph_line(img, 120, 780, y0 + i * 18, rng)
    return img, rng


def draw_figure(img, box):
    """Thin-line technical drawing: frame, diagonals, arcs, dimension ticks."""
    x1, y1, x2, y2 = box
    cv2.rectangle(img, (x1, y1), (x2, y2), 30, 1)
    cv2.line(img, (x1, y1), (x2, y2), 30, 1)
    cv2.line(img, (x1, y2), (x2, y1), 30, 1)
    cv2.ellipse(img, ((x1 + x2) // 2, (y1 + y2) // 2), ((x2 - x1) // 3, (y2 - y1) // 3), 0, 0, 360, 30, 1)
    for k in range(6):
        xx = x1 + 20 + k * (x2 - x1 - 40) // 5
        cv2.line(img, (xx, y1 + 20), (xx, y2 - 20), 30, 1)


def to_rgb(g):
    return cv2.cvtColor(g, cv2.COLOR_GRAY2RGB)


def test_plain_text_page_has_no_illustration():
    img, _ = text_page(y0=200, n_lines=45)
    assert ill.detect_line_art(img, 'R') == []


def test_line_drawing_found_and_caption_left_as_text():
    img, rng = text_page(y0=700, n_lines=25)
    fig = (150, 200, 700, 500)
    draw_figure(img, fig)
    # caption: two short lines 14 px below the figure
    glyph_line(img, 150, 330, 514, rng)
    glyph_line(img, 150, 280, 532, rng)
    found = ill.detect_line_art(img, 'R')
    assert len(found) == 1
    x1, y1, x2, y2 = found[0]['bbox']
    # figure covered...
    assert x1 <= fig[0] + 3 and y1 <= fig[1] + 3 and x2 >= fig[2] - 3 and y2 >= fig[3] - 3
    # ...but the caption (starts at y=514) and the body text (y >= 700) are not
    assert y2 < 514


def test_tonal_photo_is_detected():
    img, _ = text_page(y0=800, n_lines=20)
    cv2.rectangle(img, (200, 150), (500, 450), 90, -1)          # mid-dark halftone-like block
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 60, (300, 300)).astype(np.uint8)
    img[150:450, 200:500] = np.clip(img[150:450, 200:500].astype(int) - noise, 0, 255).astype(np.uint8)
    found = ill.detect_tonal_images(to_rgb(img))
    assert len(found) == 1
    x1, y1, x2, y2 = found[0]['bbox']
    assert abs(x1 - 200) <= 3 and abs(y1 - 150) <= 3 and abs(x2 - 500) <= 4 and abs(y2 - 450) <= 4


def test_mask_box_pads_photos_more_vertically_than_line_art():
    photo = {'detector': 'tonal', 'bbox': [100, 100, 200, 200]}
    art = {'detector': 'line_art', 'bbox': [100, 100, 200, 200]}
    assert ill.mask_box(photo) == [96, 90, 204, 210]
    assert ill.mask_box(art) == [96, 95, 204, 205]

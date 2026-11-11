"""Deskew must recover a known skew + shear on a synthetic justified text page."""
import cv2
import numpy as np

import deskew


def make_page(w=900, h=1300, left=120, right=780, first=140, pitch=26, n=40, seed=1):
    """White page with justified 'text': rows of word-like black bars with ragged
    last lines, so both column edges and baselines are well defined."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 235, np.uint8)
    for i in range(n):
        y = first + i * pitch
        short = (i % 9 == 8)
        end = right - int(rng.integers(150, 300)) if short else right
        x = left
        while x < end:
            wl = int(rng.integers(25, 70))
            wl = min(wl, end - x)
            cv2.rectangle(img, (x, y), (x + wl, y + 12), 20, -1)
            x += wl + int(rng.integers(6, 10))
        # make the justified right edge exact
        if not short:
            cv2.rectangle(img, (right - 30, y), (right, y + 12), 20, -1)
    return img


def distort(img, a, s):
    """Apply baseline slope ``a`` and column-edge slope ``s`` (inverse of the correction)."""
    h, w = img.shape
    c = np.array([w / 2.0, h / 2.0])
    A = np.linalg.inv(np.array([[1.0, -s], [-a, 1.0]]))
    m = np.hstack([A, (c - A @ c).reshape(2, 1)])
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=235)


def test_recovers_rotation():
    page = distort(make_page(), 0.017, -0.017)          # ~1 deg pure rotation
    a, s, info = deskew.estimate_geometry(page)
    assert abs(a - 0.017) < 0.002
    assert abs(s + 0.017) < 0.003
    assert info['edge_model'] == 'measured'


def test_recovers_shear_without_tilting_edges():
    page = distort(make_page(), 0.008, 0.0)             # baselines slope, edges vertical
    a, s, _ = deskew.estimate_geometry(page)
    assert abs(a - 0.008) < 0.002
    assert abs(s) < 0.003                                # a plain rotation would give s = -a


def test_correction_straightens_page():
    page = distort(make_page(), 0.015, -0.022)
    rgb = cv2.cvtColor(page, cv2.COLOR_GRAY2RGB)
    out, info = deskew.deskew_page(rgb)
    a2, s2, _ = deskew.estimate_geometry(cv2.cvtColor(out, cv2.COLOR_RGB2GRAY))
    assert abs(a2) < 0.003 and abs(s2) < 0.004


def test_page_without_text_lines_is_left_alone():
    blank = np.full((600, 600, 3), 235, np.uint8)
    out, info = deskew.deskew_page(blank)
    assert info['angle_deg'] == 0.0 and info['edge_tilt_deg'] == 0.0
    assert np.array_equal(out, blank)

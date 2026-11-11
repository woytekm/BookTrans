import numpy as np
import cv2

from scale import estimate_scale, REF_PITCH


def _text_page(pitch, width=1000, height=800):
    """Synthetic page: rows of word-like dark bars at the given line pitch."""
    img = np.full((height, width, 3), 235, np.uint8)
    rng = np.random.RandomState(1)
    h = max(3, int(pitch * 0.35))
    for y in range(int(pitch), height - int(pitch), int(round(pitch))):
        x = 40
        while x < width - 60:
            w = int(rng.randint(2, 6) * h * 0.8)
            cv2.rectangle(img, (x, y), (x + w, y + h), (30, 30, 30), -1)
            x += w + int(h * 0.5)
    return img


def test_native_scan_is_not_scaled():
    f, info = estimate_scale(_text_page(REF_PITCH))
    assert f == 1.0 and abs(info["line_pitch_px"] - REF_PITCH) < 2


def test_big_scan_scale_from_line_pitch():
    f, _ = estimate_scale(_text_page(REF_PITCH * 5, 4000, 3200))
    assert 4.6 < f < 5.4


def test_override_and_no_text():
    assert estimate_scale(_text_page(REF_PITCH), 3.0)[0] == 3.0
    f, info = estimate_scale(np.full((500, 500, 3), 240, np.uint8))
    assert f == 1.0 and "note" in info

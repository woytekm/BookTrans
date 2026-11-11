#!/usr/bin/env python3
"""Small image helpers shared by the BookTrans stages.

Kept dependency-free apart from numpy/OpenCV so the stage scripts stay
runnable straight from the source directory (no package install needed).
"""
import cv2
import numpy as np


def paper_color(img):
    """Estimate the paper tone of an RGB/gray image.

    The median of the brightest 30 % of pixels is robust against both printed
    ink and the dark gutter shadow.
    """
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    cut = np.percentile(gray, 70)
    mask = gray >= cut
    if img.ndim == 2:
        return int(np.median(img[mask]))
    return tuple(int(np.median(img[..., c][mask])) for c in range(img.shape[2]))


def crop_for_ocr(page, bbox, context=10, pad=24, color=None):
    """Crop ``bbox`` (x1,y1,x2,y2) from ``page`` for Tesseract.

    Two things are done that a bare ``page[y1:y2, x1:x2]`` crop does not do:

    * ``context`` real pixels around the box are included, so letters that
      stick out of a slightly too tight box (typically line-initial letters)
      are still seen by the OCR engine;
    * the result is surrounded by ``pad`` pixels of paper colour, because
      Tesseract is unreliable when ink touches the image border.

    Returns ``(image, (ox, oy))`` where ``(ox, oy)`` is the position of the
    returned image's top-left pixel in page coordinates (it can be negative).
    Add it to OCR coordinates to get page coordinates.
    """
    h, w = page.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in bbox]
    cx1, cy1 = max(0, x1 - context), max(0, y1 - context)
    cx2, cy2 = min(w, x2 + context), min(h, y2 + context)
    crop = page[cy1:cy2, cx1:cx2]
    if color is None:
        color = paper_color(page)
    padded = cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=color)
    return padded, (cx1 - pad, cy1 - pad)

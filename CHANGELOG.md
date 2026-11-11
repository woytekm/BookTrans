# Changelog

## Unreleased — stage2-rework

### OCR crops and Stage 1 clean-up

- New `src/booktrans/imgutil.py` with `crop_for_ocr`: OCR crops now include
  10 px of real context and are surrounded by 24 px of paper-coloured padding.
  Bare crops made Tesseract drop or misread line-initial letters.
- Stage 1: the hard-coded 65 px ink-ignore margin is now side-aware (65 px on the
  outer edge, 10 px at the gutter). On the right-hand page the text starts only
  ~60 px from the gutter, so the old margin clipped the first letters of lines
  (`hen`, `var`, `ceit`, `lelt`, `ischen`, `ondern`, and a lost first word).
- Stage 1: removed unused `ocr_lines` / `merge_lines_into_regions` and a leftover
  `y1 if False else y` expression.
- Sample result (`0001.tif`, page 2): 12 of 40 OCR lines changed, the large
  majority of them line-initial fixes.

## 2026-11-11 — Documentation baseline and project consolidation

- Created a persistent project tree and initial architecture/data-model/pipeline/decision/status documentation.
- Consolidated the current Stage 1 and Stage 2 prototype scripts into `src/booktrans/`.
- Added representative Stage 2.1 JSON and diagnostic outputs for both supplied sample spreads.
- Documented known limitations and the next development target.

## Earlier prototype work (before repository baseline)

- Stage 1: TIFF input, open-spread splitting, coarse text/image segmentation and OCR.
- Stage 2: OCR-line geometry, initial typography/semantic candidates and diagnostic overlays.
- Stage 2.1 experiments: improved gutter estimate and paragraph-boundary inference using visual horizontal ink bands.

## 2026-11-11 — Stage 3 classification and association

- Added `src/booktrans/stage3.py`.
- Added explicit illustration objects and OCR-line context/translation status.
- Illustration-associated OCR is preserved but excluded from translation and
  reconstruction.
- Added a conservative fallback for large Stage 1 regions with very low OCR
  confidence, covering the supplied technical-drawing stress case.
- Added Stage 3 diagnostic overlay generation.
- Updated data model, pipeline and development documentation.

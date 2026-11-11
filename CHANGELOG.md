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

### Per-page deskew (skew + shear)

- New `src/booktrans/deskew.py`. Each page is corrected on its own right after the
  spread is split, before any analysis. Two numbers are estimated per page: the
  text-baseline slope and the column-edge slope (from full-width justified
  lines), and one small affine map makes baselines horizontal and edges vertical.
  A pure rotation was not enough: on the left page of `0001.tif` the baselines
  slope 0.45 deg while the column edges are already vertical, so rotating alone
  tilted the edges by the same amount. Pages with fewer than 8 full-width lines
  fall back to rotation; pages with fewer than 6 text lines are left untouched.
- Measured on the sample spreads (baseline / edge tilt before correction):
  0001 L 0.45 / 0.00 deg, 0001 R 1.01 / -0.94, 0002 L 0.75 / 0.01, 0002 R 0.94 / -1.30.
  After correction all residuals are < 0.05 deg; right-page column-edge drift fell
  from about -16 and -24 px per 1000 px to under 1 px.
- Stage 1 saves the deskewed page (`page_image`) and records `skew_deg` and a
  `deskew` block (angles, edge model, source-to-destination matrix) per page.
  Stages 2 and 3 now analyse those saved pixels; all JSON coordinates refer to
  them. Debug overlays are drawn on the deskewed spread.
- OCR text accuracy is roughly unchanged by deskew at the current scan resolution
  (some line-initial and word fixes, some new digit confusions such as 3/5); the
  gain is geometric and matters for paragraph detection and reconstruction.
- Tests: `tests/test_deskew.py` (synthetic page with known skew/shear),
  `tests/conftest.py` puts the stage directory on the path; `test_stage3.py`
  imports `stage3` directly.

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

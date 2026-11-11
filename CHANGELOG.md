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

### Illustration and caption model

- New `src/booktrans/illustrations.py`: illustrations are now detected *before*
  text OCR, from geometry alone. Photographs/halftones use the existing tonal
  contour detector; line drawings are "everything that is not part of a text
  line" (a text line = a row of glyph-sized pieces; leftover ink that forms a
  sizeable cluster is a figure; nearby pieces merge into one figure).
- Stage 1 masks illustration boxes (padded per detector) out of text-region
  detection, so drawing fragments no longer become text regions. Overlapping
  coarse text boxes are merged before OCR.
- Stage 2 OCRs from a copy of the page with illustrations painted over (crop
  context used to pull neighbouring drawing edges in as junk "captions").
- Captions are found by geometry (small text beside or below a figure, plus
  continuation lines), OCR lines are split at wide horizontal gaps (side-by-side
  captions), low-confidence symbol-only tokens are dropped, and captions are
  grouped per column. The old "caption band to the right of the image" hack is gone.
- Body glyph size is estimated from running text only (captions can outnumber
  body lines on a drawing page).
- Stage 2 removes duplicate OCR lines (IoU) and each line belongs to exactly one
  coarse text box.
- Stage 2 `--ocr-scale` (default 2.0) upscales crops before OCR. Measured on lines
  with known text: small captions improve clearly (e.g. `41 BARZDORF` 0.91 -> 1.00,
  caption 37 0.89 -> 0.96 similarity), body text is unchanged; 3-4x is no better.
- Stage 3: the "large region with low OCR confidence" illustration fallback is
  removed (it swallowed the captions under a drawing group). Caption lines are
  `content_context: caption`, `translation_status: translate`, with an
  `associated_illustration_id`; geometry alone never excludes them.
- Sample result: `0002.tif` page 1 went from one detected illustration plus about
  eight junk "text" regions (`ä faf`, `{ LEA l" A`, ...) sent to translation, to six
  illustrations (main plate + five profile drawings), zero junk regions and seven
  separate two-line captions, plus the two plate captions 38/39.
- Tests: `tests/test_illustrations.py` (synthetic pages); `test_stage3.py` updated.

### Paragraph detection (page-level)

- Paragraphs are now built per text *column of the whole page*, not per coarse
  Stage 1 box (box edges cut paragraphs at arbitrary places). The visual
  "horizontal ink band" heuristic and the footnote-marker split are removed.
- Break evidence, first match wins: a line that stops short of the column's right
  edge (threshold max(8 px, 5 x std of full-line shortfall); after deskew full
  lines are within ~3 px and real paragraph ends are >= 19 px short), a
  first-line indent, extra line spacing. A line ending in a hyphen never ends a
  paragraph. Each line records `paragraph_break` and `paragraph_break_reason`.
- Sample result: paragraph counts 3 / 4 / 3 / 4 on the four sample pages (page 51
  has four paragraphs; an earlier note claiming five was wrong), no cuts inside
  hyphenated words. Stage 2 schema_version is now 0.3.0.
- Tests: `tests/test_paragraphs.py`.

### Cross-page links and translation units (Stage 4)

- New `src/booktrans/stage4.py`: reads Stage 3 JSONs in book order and links the
  last body paragraph of a page to the first of the next (also across spreads)
  when the first ends in a hyphen or lacks sentence-final punctuation and the
  second starts lower-case. Sets `continues_from` / `continues_to`.
- Dehyphenation: `Spitz-`+`bogen` -> `Spitzbogen`; upper-case continuation keeps
  the hyphen; `Werk-`+`und Form-` keeps the hyphen with a space. Every hyphen join
  is recorded in `joins`. Output `units.json` (one unit per paragraph chain or caption).
- Sample result: three cross-page chains found, matching the ones identified by
  hand (`Werk- und Form-`/`steinen`, `(Niederschlesien`/`und die tschechoslowakische
  Republik)` across the spread boundary, `Spitz-`/`bogen`).
- Tests: `tests/test_stage4.py`.

### Documentation

- Corrected the DPI claim (effective ~135 DPI), described the new detectors,
  paragraph rule and limitations; data model additions; README lists
  hand-installed modules and MacPorts `tesseract` / `tesseract-deu`.

### One-command wrapper and nested document model

- New `src/booktrans/booktrans.py`: runs stages 1-4 on a TIFF and writes
  `<out>/<tiff name>/` with `document.json`, `pages/`, `figures/`, `debug/` and
  `stages/` (raw stage JSON).
- New `src/booktrans/docmodel.py` builds `document.json` (schema 1.0): per page
  layout (size, paper colour, text/content area, column edges, default
  alignment), reading-ordered blocks (text: role, OCR text, unit id, continuation
  links, typography = glyph height, relative size, line pitch, alignment,
  first-line indent, last-line shortfall, gap above; lines with boxes and
  confidence; figures: cropped file, detector, caption block ids), plus the
  translation units.
- Not included yet: page numbers/printer marks, curvature correction.
- Tests: `tests/test_docmodel.py`.

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

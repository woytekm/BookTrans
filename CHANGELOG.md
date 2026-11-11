# Changelog

## Unreleased — stage2-rework

### Glossary

- `src/booktrans/glossary.py` and `glossary/terms.csv`: German -> Polish place names and terms. Lookup
  before translation (normalised, compound- and OCR-tolerant matching), `prompt_block`, post-translation
  `check` (inflected forms accepted), `import-places` for a `german,polish` CSV (duplicates dropped,
  names with several Polish forms marked `ambiguous`), `match` and `candidates` reports. 6 new tests (43).
  On the sample book `check` flagged a caption translated more briefly than the glossary term.

### Batch mode (books)

- `booktrans.py` takes a folder of TIFFs or several files and writes one book folder: stages 1-3 per
  spread in `stages/<tiff>/`, stage 4 once over all spreads (paragraphs cut at a spread boundary become
  one unit), one `document.json` with consecutive page numbers and unique block ids.
  Options `--name`, `--resume`, `--separate`.
- `docmodel.build_document(book=True)`; `render.py` writes `page_NNN_X.png` and per-page
  `compare_page_NNN.png` for books (`compare.png` up to 4 pages).
- `examples/book_0001-0002.pl.json`: both sample spreads as one book. 2 new tests (37).
- Docs: glossary design (lookup, not RAG) in `docs/TRANSLATION.md`.

### Documentation

- New `docs/HANDOFF.md` (start here), `docs/TRANSLATION.md` (translation service contract), `docs/RENDERING.md`.
  README, DEVELOPMENT, PIPELINE, ARCHITECTURE, DATA_MODEL (translations JSON, render report, scale fields) and
  DECISIONS (ADR-008 to ADR-013) updated.

### Font fitting (render)

- Letter-spacing is fitted per block (`--tracking auto`, default): the translation fills the
  original line widths; capped at 0.12 em, size at most 4 % above the glyph-height size.
  0001: body 49 -> 54 lines (original 56), with 0.08 em tracking.
- New options `--tracking EM`, `--size-scale`, `--word-space`; `booktrans.py` passes
  `--font/--bold-font/--italic-font` and these to the renderer. `--tracking 0` = previous look.
- `report.json` gains `tracking_em` and `lines_original`.
- Fixed frames (captions, headings) no longer widen into a neighbouring block on the same
  rows and no longer overflow their width: tracking, then size, is reduced until every line fits
  (fixes side-by-side captions colliding on 0002).
- `examples/0002.pl.json`: translation of the second sample spread (joins the sentence cut at the end of 0001).

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

### Translation of the first spread and page assembly (`render.py`)

- `prompts/translate_pl.txt` (the translation prompt) and `examples/0001.pl.json`:
  Polish translation of the ten units of `0001.tif` (post-1945 place names:
  Byczyna, Głuchołazy, Domaszkowice, Pakosławice, Korfantów, Górny/Dolny Śląsk;
  obvious OCR slips were corrected before translating, listed in the file's notes).
- New `src/booktrans/render.py`: rebuilds pages from `document.json` + translations.
  Clean paper canvas (or `--background scan`: scan with text blocks wiped and filled
  with the local paper tone), figures and page furniture pasted back, text typeset by
  optimum-fit line breaking with justification, Polish hyphenation (optional `pyphen`),
  no line ending in a one-letter word, original line pitch and anchoring.
  Body paragraphs flow through the page columns and over page breaks (a shorter
  translation leaves space at the end instead of gaps inside the page); headings,
  captions, bylines and footnotes keep their own frames; capitals are letter-spaced up
  to the original line width. Font size comes from the original line widths (same
  typographic colour) within 15 % of the size implied by the glyph height; if the
  text needs more lines than the page has, the size shrinks up to 10 %, then overflows
  and a warning is reported. Units without translation keep their original pixels.
- Default font Crimson Text (SIL OFL) bundled in `fonts/`.
- New `src/booktrans/marks.py`: page numbers, printer's marks and horizontal rules
  (including faint hairlines such as the footnote rule) are detected and stored as
  `graphic` blocks with image crops in `document.json`, pasted back by the renderer.
- Wrapper: `--translations FILE` also runs the page assembly.
- Result on `0001.tif`: both pages reproduce the original layout (photo, caption,
  byline, heading, justified body, footnote with rule, page number); the Polish body
  needs 49 lines where the German set 56, so page 2 ends 7 lines early. The paragraph
  cut at the spread boundary (`(Dolny Śląsk`) needs the next spread.
- Tests: `tests/test_render.py`, `tests/test_marks.py`.

### Progress and debug output

- New `src/booktrans/log.py`: timestamped progress lines on stderr (one shared clock
  for all stages). Every stage reports what it is doing: scale estimate, deskew
  angles, illustrations and text regions found, OCR progress per region, line
  counts by type, regions built, classification counts, paragraph links, units.
- Wrapper prints stage headers (`=== stage 2/4 ... ===`) and a final summary;
  `-v` / `--verbose` also lists every detected region and every OCR line with its
  confidence. Stage stdout JSON summaries are shown only when a stage fails.

### High-resolution scans, crops and single pages

- Scale is measured from the text, not the page height: `src/booktrans/scale.py`
  closes ink horizontally into line blobs and takes the dominant vertical distance
  between consecutive lines (the body line pitch; ~23.4 px on the sample scans).
  `f = pitch / 23.3`, scans below 1.25 are used as they are. Measured: samples
  23.4 px (f = 1), the samples upscaled 5x 116.5 px (f = 5.0), a real 600 DPI crop
  113-116 px (f = 4.87). Sizing by page height (my first version) was wrong for
  crops: text came out twice too big and the body text was detected as a drawing.
- A scan with f > 1 is analysed on an area-averaged working copy (all thresholds
  are pixel values tuned at the sample size); the same split and affine deskew are
  applied to the full-resolution page (`page_NN_X_full.png`; `scale` and
  `scale_info` in the JSON). `--scale auto|N` on Stage 1 and the wrapper.
- Stage 2 OCRs crops cut from the full-resolution pixels, resized so `--ocr-scale`
  keeps its meaning (OCR px per working px; default 2). CLAHE is applied only to
  the soft native-size path (it distorted thin small caps on real high-res pixels).
  Boxes and glyph heights stay in working px. Figures are cropped from the
  full-resolution page; `document.json` pages carry `scale` and `image_full`.
- `--layout spread|page|crop` (Stage 1 and wrapper). `page` keeps the 65 px edge
  margins on all sides, `crop` uses 10 px (a crop has no physical book edge), so
  a photo or text cut by the crop edge is still found. The spread default is unchanged.
- Photo mask padding below a photo reduced from 10 to 4 px: a caption sits only
  ~6 working px under a photo at this scale and the old pad painted over the top
  of its capitals. Native sample results are identical (all four pages, same lines
  and illustrations).
- Real 600 DPI crop (photo, caption, two paragraphs; 12 lines): 12/12 lines
  match a manual transcription exactly, 1 figure, 2 paragraphs, caption linked.
  The same crop downscaled to ~135 DPI and run natively also gives 12/12, so on
  clean body text and this caption full-resolution OCR gives no measurable gain;
  the gain is the full-resolution figure crops, and possibly on harder text (digits,
  tiny captions, drawing labels), which this crop does not contain.
- Still open: a synthetic 5x upscale of `0002.tif` splits the main drawing plate
  into several pieces (its gaps sit at the 40 px merge limit); not seen on real
  data yet, needs a real hi-res drawing page.
- Tests: `tests/test_scale.py`; full-resolution figure crop test in `tests/test_docmodel.py`.

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

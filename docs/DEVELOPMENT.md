# Development status

Last updated: 2026-11-11. For the overview and the open issues start with `HANDOFF.md`.

## Current project state

BookTrans is a working prototype: scans go in, a complete page-recreation model and, with a translations file, reconstructed Polish pages come out. The implementation is a set of standalone scripts rather than a packaged application. The translation service is not built. The supplied sample scans are two 1800-pixel-wide open-book TIFF spreads (`0001.tif` and `0002.tif`, TIFF metadata says 600 DPI, but the effective resolution is only about 135 DPI: Tesseract estimates 135 and body glyphs are 8-14 px high; thresholds in the code are expressed in pixels for that scale).

## Implemented / prototyped

- TIFF loading through Pillow.
- Open-spread gutter estimation and left/right page splitting.
- Coarse text-region proposals using OpenCV thresholding/morphology.
- Per-page deskew (baseline slope and column-edge slope, one affine map).
- Illustration detection before OCR: tonal contour detector for photographs, "ink that is not part of a text line" for line art; illustrations are masked out of text analysis.
- Geometric caption model (small text beside/below a figure) and caption grouping.
- Tesseract German OCR (`deu`) within coarse text regions.
- OCR line records with page-local bounding boxes, text, confidence and glyph-height estimates.
- Initial line classification (body, heading, caption, footer/byline candidates).
- Page-level paragraph detection per text column: short last line (primary signal in this book), first-line indent, extra spacing; hyphenated line ends never end a paragraph.
- Stage 4: cross-page paragraph links (`continues_from`/`continues_to`), dehyphenation and translation units.
- JSON structure output and diagnostic overlay generation.
- Resolution handling: scale from the body line pitch, analysis on a working copy, OCR and figure crops from the full-resolution scan (`--scale`, `--ocr-scale`); layout modes `spread|page|crop`.
- `booktrans.py` wrapper and `docmodel.py`: one command per TIFF gives `document.json` (layout, typography, blocks, figures, units), cropped figures, debug overlays and the stage JSONs.
- `marks.py`: page numbers, printer's mark and rules as image crops.
- `render.py`: page assembly from the document model and a translations JSON, with optimum-fit justified line breaking, Polish hyphenation, flow across pages, fixed frames for headings/captions/footnotes, paper or scan background, and automatic font fitting (letter-spacing and size fitted to the original line widths; `--tracking`, `--size-scale`, `--word-space`, `--font*`).
- Hand translations of both sample spreads (`examples/0001.pl.json`, `examples/0002.pl.json`) and their reconstructions (`examples/*_render_compare.jpg`).
- Batch mode: a folder or several TIFFs become one book document (`--resume`, `--separate`, `--name`); cross-spread paragraph joins.
- `glossary.py`: glossary lookup (places with OCR-tolerant matching, terms inside compounds), prompt block, post-translation check, place-name CSV importer, candidate extractor; seed `glossary/terms.csv`.
- Progress/timing output on stderr; 43 tests.

## Experimental results and observations

- On the first spread (`0001.tif`), the analysis separates a prominent photograph from surrounding text and identifies several text roles.
- The right-hand page of `0001.tif` (page 51) has four prose paragraphs; the current detection finds four (3/4/3/4 on the four sample pages). Counts were checked by eye, not against labelled ground truth.
- A gutter estimate around x=925 was used in the Stage 2.1 output. Earlier versions estimated around x=994 and clipped the inner edge of the right page. Gutter detection therefore remains an active risk and needs validation on more spreads.
- The second spread (`0002.tif`) includes technical drawings with labels. Current threshold-based region detection can confuse dense drawing content with text-bearing areas. Distinguishing illustrations from text and retaining drawing-associated labels is a key next task.
- OCR and typography heuristics are sample-specific. The presence and amount of Fraktur in the book still need systematic inspection and OCR-engine comparison.

## Known limitations

- Gutter/page-boundary detection is heuristic and not yet robust to arbitrary book geometry.
- Coarse image/drawing detection can misclassify technical illustrations.
- Text regions may contain multiple semantic units.
- Paragraph detection relies on justified text; a book with ragged right edges or other conventions needs the indent/spacing signals tuned. It needs a labelled set of expected boundaries.
- Deskew is global per page (no curvature correction at the gutter); photos within 65 px of the gutter are not detected; page numbers and printer marks are not extracted.
- OCR accuracy is limited by the ~135 DPI scan (digit confusions such as 3/5 in captions).
- Reading order and multi-column layouts are not robustly modelled.
- Italic, bold, small caps and typeface identification are not implemented reliably; `style` is currently unknown.
- Tesseract confidence is not a calibrated probability of correctness.
- No human-review UI exists.
- No translation client or cache exists yet (`TRANSLATION.md` has the contract). Output is PNG per page, no PDF export.
- Body text flows with no widow/orphan control.
- No automatic font choice among several fonts; page numbers are pasted as crops; one body column per page is assumed.
- The code is a set of scripts, not a tested library/API; tests use synthetic data only.

## Immediate next development

1. **Translation service** (`translate.py`, local LLM; see `TRANSLATION.md`).
2. **Glossary content** (import place names, review terms) and wiring into the translation service (`TRANSLATION.md`).
3. Automatic font choice among supplied fonts; more samples (two-column, ragged right, footnotes, running heads).

Earlier list (still relevant for robustness):

1. Create a small manually reviewed ground-truth annotation for both spreads: page boundaries, illustration boxes, text blocks and paragraph/line membership.
2. Stabilize gutter detection against both scans and add regression tests.
3. Validate paragraph detection against labelled boundaries on more pages.
4. Add typography measurements (relative size, line spacing, alignment, weight/italic candidates) without attempting exact font identification.
5. Add reading-order representation.
6. Compare Tesseract Antiqua/German, Fraktur-capable models and Kraken on representative crops.

## Versioning guidance

Use semantic versioning once the project becomes a stable package. Until then, use milestone labels (`prototype-0.x`) and record schema changes explicitly. Do not treat `stage2_1` in a filename as a stable release. Each meaningful change should update this document, `CHANGELOG.md` and relevant decision/data-model documentation.

## Stage 3 progress — classification and association

Implemented a first Stage 3 classifier/association layer.

### Decisions

- Illustration editing is deferred.
- OCR lines remain permanent objects even when excluded from translation.
- Illustration association is currently geometric, not semantic.
- Stage 1 image/drawing candidates are retained; a conservative fallback can
  recover a large low-confidence OCR region as an illustration candidate.
- Text *inside* an illustration is marked `translation_status: exclude`; captions are `content_context: caption` and are translated.
- Mixed regions are marked `review` rather than silently discarded.

### Sample validation

On the supplied `0002.tif` stress case, illustrations are now found before OCR (six figures on page 1), so drawing fragments never reach OCR. Captions are separate translatable regions associated with their figure.

### Next work

1. Visually review Stage 3 diagnostic overlays on both supplied spreads.
2. Establish small regression fixtures for illustration boxes and line
   associations.
3. Refine the classification rules only where the reviewed examples show a
   repeatable failure.
4. Improve reading order and semantic text classification before translation.

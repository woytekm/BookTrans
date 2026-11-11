# Development status

Last updated: 2026-11-11

## Current project state

BookTrans is at an experimental early prototype stage. The current implementation is a pair of scripts rather than a packaged application. The supplied sample scans are two 1800-pixel-wide open-book TIFF spreads (`0001.tif` and `0002.tif`, approximately 600 DPI according to the initial inspection).

## Implemented / prototyped

- TIFF loading through Pillow.
- Open-spread gutter estimation and left/right page splitting.
- Coarse text-region proposals using OpenCV thresholding/morphology.
- Coarse image/drawing candidate proposals using threshold contours and simple heuristics.
- Tesseract German OCR (`deu`) within coarse text regions.
- OCR line records with page-local bounding boxes, text, confidence and glyph-height estimates.
- Initial line classification (body, heading, caption, footer/byline candidates).
- Paragraph boundary experiment based on horizontal ink bands in the original scan.
- JSON structure output and diagnostic overlay generation.

## Experimental results and observations

- On the first spread (`0001.tif`), the analysis separates a prominent photograph from surrounding text and identifies several text roles.
- The right-hand page contains four visually distinct prose paragraphs according to the user's inspection. Paragraph grouping is being tuned against this page; the current output should be visually reviewed rather than treated as ground truth.
- A gutter estimate around x=925 was used in the Stage 2.1 output. Earlier versions estimated around x=994 and clipped the inner edge of the right page. Gutter detection therefore remains an active risk and needs validation on more spreads.
- The second spread (`0002.tif`) includes technical drawings with labels. Current threshold-based region detection can confuse dense drawing content with text-bearing areas. Distinguishing illustrations from text and retaining drawing-associated labels is a key next task.
- OCR and typography heuristics are sample-specific. The presence and amount of Fraktur in the book still need systematic inspection and OCR-engine comparison.

## Known limitations

- Gutter/page-boundary detection is heuristic and not yet robust to arbitrary book geometry.
- Coarse image/drawing detection can misclassify technical illustrations.
- Text regions may contain multiple semantic units.
- Paragraph detection is experimental and requires a labelled set of expected paragraph boundaries.
- Reading order and multi-column layouts are not robustly modelled.
- Italic, bold, small caps and typeface identification are not implemented reliably; `style` is currently unknown.
- Tesseract confidence is not a calibrated probability of correctness.
- No human-review UI exists.
- No translation client, translation cache, typography fitting or PDF renderer exists yet.
- The current code has not yet been reorganized into a tested library/API.

## Immediate next development

**Improve text-versus-illustration classification, especially for technical drawings.** The second supplied spread is a useful stress case because labels and linework inside architectural drawings can be mistaken for prose. The model should support a drawing region with associated text labels rather than forcing every detected text fragment into a normal paragraph.

Then:

1. Create a small manually reviewed ground-truth annotation for both spreads: page boundaries, illustration boxes, text blocks and paragraph/line membership.
2. Stabilize gutter detection against both scans and add regression tests.
3. Improve paragraph grouping on the four-paragraph right page of `0001.tif` using visual line bands, indentation and spacing evidence.
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
- Illustration-associated lines are marked `translation_status: exclude`.
- Mixed regions are marked `review` rather than silently discarded.

### Sample validation

On the supplied `0002.tif` stress case, Stage 3 recovered the large technical
drawing as an illustration candidate and associated four OCR-detected fragments
inside it with that illustration. Those fragments remain in the JSON but are
excluded from translation. Ordinary figure captions/labels outside the drawing
are not automatically excluded by the current geometry rule.

### Next work

1. Visually review Stage 3 diagnostic overlays on both supplied spreads.
2. Establish small regression fixtures for illustration boxes and line
   associations.
3. Refine the classification rules only where the reviewed examples show a
   repeatable failure.
4. Improve reading order and semantic text classification before translation.

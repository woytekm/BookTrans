# Processing pipeline

This document distinguishes the intended pipeline from the currently implemented prototype.

## Current prototype flow

1. Load an RGB representation of the TIFF using Pillow.
2. Estimate the central gutter from a vertical brightness profile and split the open spread into left and right page images.
3. On each page, use OpenCV thresholding and morphology to propose coarse text regions.
   The outer side of a page is ignored (65 px, `illustrations.edge_margins`), widened over a detected band of
   **stacked page edges** (`illustrations.book_edge`: long vertical lines starting at the border, up to 15 % of the
   width; since 2026-11-19) - near the front and back of a thick book that band is wider than 65 px and was read as
   text and figures. Short candidates whose ink lies in a hairline (85 % in 2 rows: a crease, a scanner line;
   `stage1.thin_line`) are not text. Photographs are grown over their light parts (`illustrations.grow_halftone`:
   strips with few pixels as light as the paper beside that side); before, a light pavement or sky under / over a photo
   became a separate "line drawing" that swallowed the caption.
4. Propose large image/drawing regions using threshold contours and simple size/density/overlap heuristics.
5. Run Tesseract on text crops.
6. In Stage 2, retain OCR lines with page-local bounding boxes, text, confidence and glyph-height estimates.
7. Classify lines heuristically (body, heading, caption, footer/byline candidates). A line next to a picture is a
   caption only when it is clearly smaller than the body (`stage2.caption_sized`: below 0.88x, or capitals up to 1.15x);
   before 2026-11-18 the body text of a page that starts under a plate became one 24-line "caption" (page 59).
7a. **Captions** (`captions.py`, since 2026-11-18). The paper mask painted over each figure before OCR stops at a
    caption set right against the picture (`clip_mask`; it used to cover the caption's tops - page 55 read as
    `Br U, MAR AL DER MKCHE`). Low-confidence lines inside a figure are dropped. Caption pieces on one row under one
    figure are joined into one line when the gaps between them hold ink (letters the OCR dropped) - captions side by side
    under different figures, or with blank paper between them, stay apart. Every caption line is then read again from
    its own strip (enlarged so the glyphs are ~32 px, replicated border, `--psm 7`); the new reading is kept when it
    scores better (confidence, less for few letters and junk characters; the first reading stays in `ocr_first`).
    Short pieces that still make no sense are dropped. On the test book this repaired page 55 (`45 RAUDEN OS,
    WESTPORTAL DER KIRCHE`; the printed number is 43 - a 3/5 confusion left to the correction window), page 62
    (`49 ZIEGENHALS OS, HAUPTPORTAL ...`, whose first word had been taken for a rule) and several letters elsewhere.
8. Infer paragraph boundaries per text column of the page (short last line, indent, spacing; never after a hyphen).
   Stray marks read as lines (narrower than 12 % of the widest line, at most 2 letters or digits: `)`, `5`, `Ef` from a
   mark in the margin) decide nothing: among the text lines they stay in the paragraph around them (`stray_mark`), apart
   from it (a page number below) they are a region of their own (`isolated_mark`). Before 2026-11-18 such a mark between
   two lines counted as an indented first line and cut the paragraph (page 62: "... im Jahre 1900 | erfolgten ...").
8a. Stage 4: link paragraphs across page/spread breaks, dehyphenate, build translation units. Since 2026-11-18 it
    also repairs stage 2 region errors before linking (found on page 62 of the first real book): *noise regions*
    (fewer than 3 letters once stray marks are removed: `|`, `N`, a page or figure number; or a "paragraph" narrower
    than a quarter of the page's widest) get `translation_status: exclude`, `exclude_reason: noise` (no unit, nothing
    translated); *stray-mark lines* (`junk_line`: narrower than 12 % of the region, at most 2 letters or digits, e.g.
    `)`, `Ef`, `5` from margin marks) are left out of the unit text (`unit_lines`; `pageindex.replay` skips the same
    lines, so word marks stay exact); *paragraphs split on a page* are joined: consecutive body paragraphs of one
    column are linked when the first does not end a sentence and either the second starts with a small letter or the
    first's last line reaches the column's right edge (in this book a paragraph ends with a short line; German nouns
    start with a capital, so the small-letter test alone misses `mit tiefen | Hohlkehlen`). Across a page break the
    small-letter test alone still applies (scans may skip pages). A caption of 4+ lines at body glyph height counts as
    a body paragraph for linking (stage 2 sometimes takes the text under a plate for its caption).
9. Serialize page/region/line information to JSON and draw a diagnostic overlay.
10. `docmodel.py` builds `document.json` (layout, typography, reading-ordered blocks, figure crops, page marks/rules, translation units).
    Figure crops stop at a caption touching the picture (`captions.clip_mask`), so no old caption is pasted back.
    A rule (`marks.is_rule`) must be a hairline: 70 % of its ink in 4 rows, running through 85 % of its length;
    faint, letter-spaced capitals (`ZIEGENHALS`) passed the morphological test and were kept as a "rule" crop.
11. Translation (not implemented; contract in `TRANSLATION.md`) produces a translations JSON.
12. `render.py` rebuilds each page (see `RENDERING.md`).

At high resolution (`--scale auto` detects it from the body line pitch) steps 2-4, 7-8 run on an area-averaged working copy at the sample scale; OCR crops and figure crops come from the full-resolution page, which gets the same split and deskew.

## Intended full flow

### A. Ingest

- Accept TIFF initially; consider PDF and other raster formats later.
- Record source file identity, pixel dimensions, color mode, DPI and page/spread count.
- Keep original files immutable.

### B. Normalize and split

- Detect page edges and gutter.
- Split spreads without clipping text near the binding.
- Preserve source-to-page coordinate transforms.
- Optionally dewarp curved pages and correct rotation/illumination.

### C. Segment

- Find text blocks, illustrations, photographs, drawings, captions, marginalia, page numbers and decorative objects.
- Permit nested/associated regions, e.g. labels belonging to a drawing.
- Record confidence and allow corrections.

### D. OCR

- OCR each suitable text-bearing area.
- Preserve word/line geometry, OCR confidence and engine/version/language metadata.
- Compare engines/models on historical German, including Fraktur if present.

### E. Structure

- Group lines into paragraphs without deleting line objects.
- Infer headings, captions, footnotes, labels, bylines and other semantic roles.
- Determine reading order, columns and image-caption relationships.
- Estimate typography from measured scan properties.

### F. Review

- Provide a visual overlay and an interface for editing boxes, text, line grouping and classifications.
- Prioritize low-confidence or conflicting results.

### G. Translate

- Send paragraph or other semantic text units to a local LLM endpoint.
- Include terminology/domain context and neighboring text where helpful.
- Keep stable transaction IDs, cache responses and support retranslation.

### H. Reconstruct

- Retain original images/drawings.
- Fit translated text using configured fonts and inferred styles.
- Preserve columns, alignment, spacing and page furniture as closely as feasible.
- Flag overflow or geometry conflicts for human review.

### I. Export

- Generate a PDF with embedded fonts, vector text and original raster illustrations.
- Keep a searchable/selectable text layer.
- Optionally export the structured document model independently of PDF.

## Stage 3 — classification and association

Stage 3 consumes Stage 2 JSON plus the corresponding Stage 1 JSON. It separates
normal page text from illustration objects and associates OCR lines with
illustrations using geometry.

The current policy is deliberately conservative: text inside/overlapping an
illustration is preserved in OCR data but is not translated or reconstructed.
The original illustration is retained unchanged. This avoids attempting to
redraw technical figures while keeping the information available for a future
feature.

Stage 3 also produces a diagnostic overlay showing illustration boundaries and
OCR-line translation eligibility.

Example:

```bash
python src/booktrans/stage3.py output/stage2/page_structure_stage2.json \\
  --stage1-json output/stage1/page_structure.json \\
  -o output/stage3
```

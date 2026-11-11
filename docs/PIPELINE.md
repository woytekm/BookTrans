# Processing pipeline

This document distinguishes the intended pipeline from the currently implemented prototype.

## Current prototype flow

1. Load an RGB representation of the TIFF using Pillow.
2. Estimate the central gutter from a vertical brightness profile and split the open spread into left and right page images.
3. On each page, use OpenCV thresholding and morphology to propose coarse text regions.
4. Propose large image/drawing regions using threshold contours and simple size/density/overlap heuristics.
5. Run Tesseract on text crops.
6. In Stage 2, retain OCR lines with page-local bounding boxes, text, confidence and glyph-height estimates.
7. Classify lines heuristically (body, heading, caption, footer/byline candidates).
8. Infer paragraph boundaries per text column of the page (short last line, indent, spacing; never after a hyphen).
8a. Stage 4: link paragraphs across page/spread breaks, dehyphenate, build translation units.
9. Serialize page/region/line information to JSON and draw a diagnostic overlay.

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

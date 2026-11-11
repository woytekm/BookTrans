# Architecture

## Purpose

BookTrans is intended to convert scanned historical book pages into a structured, editable document model, translate text through a separately hosted local LLM service, and reconstruct a PDF that retains the original page design as closely as practical.

The initial use case is a German pre-war architecture book translated into Polish. Language pairs, fonts and rendering parameters should ultimately be configurable rather than hard-coded.

## Architectural approach

This is a **document reconstruction pipeline**, not a simple OCR-and-replace operation. The original scan is the visual source of truth. Analysis produces structured geometry and text; translation changes textual content; rendering combines translated text with retained image assets and page geometry.

Conceptual pipeline:

```text
Original TIFF scan (single page or open spread)
        |
        v
Input normalization and page geometry
        |
        v
Page splitting / dewarping (where required)
        |
        v
Coarse layout segmentation
   |                    |
   v                    v
Text-bearing areas    Images / drawings
   |
   v
OCR words and lines (line geometry retained permanently)
   |
   v
Paragraph grouping and typography analysis
   |
   v
Semantic classification and reading order
   |
   v
Human review / correction
   |
   v
Translation transactions to local LLM service
   |
   v
Translated document model
   |
   v
Page reconstruction and typography fitting
   |
   v
Searchable, vector-text PDF with original raster illustrations
```

## Component responsibilities

### Input and geometry

- Read TIFF and preserve original pixel data and resolution metadata.
- Detect whether an input is a single page or a two-page spread.
- For spreads, estimate the gutter and split into page images.
- Keep transformations and offsets explicit so page-local coordinates can be mapped back to source-spread coordinates.
- Future work: page curvature/dewarping, uneven illumination correction and rotation/perspective correction.

### Layout analysis

- Detect coarse text-bearing areas and image/drawing candidates.
- Distinguish text regions from illustrations, while allowing text-bearing subregions inside drawings.
- Preserve the hierarchy between page, region, line and paragraph.
- Determine reading order independently of raw OCR return order.

### OCR and typography

- OCR should be replaceable through an engine adapter. Tesseract is the current prototype engine; Kraken and other historical OCR options remain to be evaluated.
- Preserve individual OCR lines, bounding boxes and confidence values.
- Infer paragraph breaks from visual evidence, not solely OCR engine line/block grouping.
- Estimate relative typography (size, weight, italic, small caps, alignment and spacing) before attempting exact font identification.

### Translation

- Translation is a separate service boundary, called over HTTP using a configurable URI.
- Each translation request should have a stable transaction ID and include source/target language, text, semantic role and useful neighboring context.
- Cache results and retain source text, translated text, model/service metadata and review state.
- Translation must not alter geometry or overwrite OCR source text.

### Rendering

- Reuse original photographs and drawings at source quality where possible.
- Render translated text as vector text in a configurable font family and style mapping.
- Fit translated text to available regions with explicit limits and warnings; do not silently shrink text excessively.
- Produce PDF only after the intermediate model and translation are stable.

## Design boundaries

- Computer vision handles geometry and region proposals.
- OCR engines produce text hypotheses and line geometry.
- Heuristics or ML classifiers infer paragraph and semantic structure.
- The LLM is primarily responsible for translation, not deterministic page geometry.
- Human review is an expected capability for uncertain OCR, region boundaries and translation.

## Current implementation versus target architecture

Input, layout analysis, OCR, the document model and rendering (page reconstruction with font fitting) are prototyped. Translation, review and export are not. The conceptual components above are target architecture, not claims that all are implemented. See `HANDOFF.md` and `DEVELOPMENT.md` for exact status.

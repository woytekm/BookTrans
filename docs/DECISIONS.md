# Architectural decision record

This file records durable decisions. New entries should be appended; superseded decisions should be marked rather than silently deleted.

## ADR-001 — Treat the task as document reconstruction

**Status:** Accepted

The project will not simply OCR a page and replace text in a raster image. It will create an intermediate document model containing page geometry, regions, text lines, semantic groupings and image assets, then reconstruct the page from that model.

**Reason:** This separates analysis, translation and rendering; allows retranslation and font changes without repeating OCR; and preserves original illustrations.

## ADR-002 — Preserve OCR lines as permanent objects

**Status:** Accepted

Every OCR line and its geometry must remain available even after lines are grouped into paragraphs or other semantic units. Higher-level structures reference or contain line IDs rather than destructively merging/deleting the lines.

**Reason:** Line geometry is required for review, text correction, layout reconstruction and later changes to paragraph grouping.

## ADR-003 — Separate pipeline responsibilities

**Status:** Accepted

Image geometry, segmentation, OCR, semantic analysis, translation and PDF rendering are separate components. The local LLM service is primarily a translation service, not the authority for deterministic geometry or OCR layout.

**Reason:** Components can be tested and replaced independently; translation models can change without rewriting document analysis.

## ADR-004 — Use a versioned intermediate representation

**Status:** Accepted

Persist intermediate results as JSON with a schema version. Keep source text separate from translated text and retain source geometry.

**Reason:** Enables repeatable processing, caching, debugging and continuation across development sessions.

## ADR-005 — Support human review instead of requiring perfect automation

**Status:** Accepted

Detection/classification should expose confidence and diagnostic evidence where practical. Uncertain regions and translations should be correctable by a human.

**Reason:** Historical print, Fraktur, mixed typography, drawings and scan defects make fully automatic interpretation unreliable.

## ADR-006 — Keep the original scan immutable

**Status:** Accepted

All image normalization and page transformations operate on derived data. The original TIFF remains the canonical visual source.

**Reason:** Avoids cumulative quality loss and allows new algorithms to be tested against the same source.

## ADR-007 — Prototype open-spread input first

**Status:** Accepted for initial prototype

The initial input path handles a TIFF containing two facing pages, estimates the gutter and splits it into left/right page images.

**Reason:** The supplied book scans are open-book spreads. Single-page input and robust page-edge detection remain future generalizations.

## ADR-008 — Resolution is handled by a scale factor measured from the body line pitch

**Status:** Accepted

Thresholds are tuned in pixels of the 1800 px sample spreads. Larger scans are analysed on an area-averaged working copy; the factor comes from the median distance between consecutive text-line blobs (reference 23.3 px, factors below 1.25 snap to 1). Full-resolution pixels are used only for OCR crops and figure crops. **Reason:** page-height-based scaling failed on a crop of a page; the line pitch is a property of the text and works for crops, pages and spreads.

## ADR-009 — The page-recreation model is `document.json`, separate from the stage JSON

**Status:** Accepted

`document.json` is nested (pages, blocks, lines), carries layout and typography, and links figures; the stage JSONs stay in `stages/`. Coordinates are working pixels with `scale` and `image_full` recording the mapping. **Reason:** the renderer and a future review tool need one self-contained description of a page.

## ADR-010 — Rebuild pages on a clean canvas by default; the scan background is optional

**Status:** Accepted

`--background paper` draws on the paper colour; `scan` keeps the scan and wipes the translated blocks. Figures are pasted, page furniture is multiplied in. **Reason:** a clean canvas has no ghost text; `scan` keeps the paper texture and gutter shading when wanted.

## ADR-011 — Typeset with an optimum-fit justified line breaker, not the browser or PDF layout engine

**Status:** Accepted

Own Knuth-Plass style breaker over PIL fonts with pyphen hyphenation and Polish typographic rules (no line ends in a one-letter word). **Reason:** exact control of frames, line pitch and flow across pages, and it yields measurable fit data for the report.

## ADR-012 — Font fitting by letter-spacing and size, not by changing the line count

**Status:** Accepted

The translation is fitted to the original line widths by per-block tracking (cap 0.12 em) and a size within 4 % of the glyph-height size. **Reason:** language length is not the issue (Polish is about 3 % shorter); the original typeface is wider than the available fonts. Choosing among several fonts automatically is a later step.

## ADR-013 — The translation service is a separate stage with a JSON contract

**Status:** Accepted (service not built)

Input `document.json` units; output a translations JSON in the format of `examples/*.pl.json`. A local LLM behind an OpenAI-compatible endpoint is the intended backend. See `TRANSLATION.md`. **Reason:** keeps geometry deterministic, allows hand editing and re-rendering, and lets the backend change.


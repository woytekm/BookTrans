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

# Changelog

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

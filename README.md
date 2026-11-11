# BookTrans

A Python-based document reconstruction and translation project for historical books. The initial target is a pre-war German architecture book to be translated into Polish while retaining original illustrations, page composition and an approximate historical typographic character.

**Current status:** early prototype, document analysis only. The current code is experimental and tuned against two supplied 1800-pixel-wide open-book TIFF scans. It is not yet a general-purpose book OCR or translation application.

## Current prototype

- `src/booktrans/stage1.py` — open-spread TIFF loading, gutter estimation, page splitting, per-page deskew, illustration detection and coarse text regions.
- `src/booktrans/deskew.py`, `illustrations.py`, `imgutil.py` — helpers for per-page skew/shear correction, geometric illustration detection (before OCR) and OCR crops.
- `src/booktrans/stage2.py` — OCR line extraction (padded, 2x upscaled crops), line classification, geometric caption model, page-level paragraph detection, JSON output and diagnostic overlay.
- `src/booktrans/stage3.py` — illustration objects, caption/line association and translation status.
- `src/booktrans/booktrans.py` — **one-command wrapper**: runs stages 1–4 on a TIFF and writes the result folder described below.
- `src/booktrans/docmodel.py` — builds the nested page model (`document.json`) and crops the figures.
- `src/booktrans/stage4.py` — cross-page paragraph links, dehyphenation and translation units (`units.json`).
- `examples/` — representative JSON and diagnostic outputs from the Stage 2.1 experiments. These are examples, not golden test fixtures.
- `docs/` — architecture, pipeline, data model, decisions and development status.

## Environment

Python 3.10+ is expected. The scripts are plain standalone files meant to be run from the source directory; no virtual environment or package install is needed. Install the modules listed in `requirements.txt` (numpy, opencv-python, Pillow, pytesseract; `pytest` for the tests) by hand.

Tesseract is an external system dependency. Install Tesseract and German language data (`deu`). With MacPorts:

```bash
sudo port install tesseract tesseract-deu
```

## Running the current prototype

One command per TIFF (spread or single page):

```bash
cd src/booktrans
python booktrans.py /path/to/0001.tif -o /path/to/output
```

Useful options: `--layout spread|page|crop` (open book split at the gutter / one whole page / part of a page), `--scale auto|N` (how much larger than the sample scans the input is; auto measures the line pitch, so 600 DPI scans and crops are handled by analysing a downscaled working copy while OCR and figures use the full-resolution pixels), `--ocr-scale`.

creates `output/0001/` with `document.json` (nested model: pages → blocks → lines, layout, typography, figure links, translation units), `pages/` (deskewed page images; all coordinates refer to them), `figures/` (every illustration cropped to a PNG), `debug/` (overlays) and `stages/` (raw JSON of stages 1–4). Each TIFF is processed on its own; to link paragraphs across spreads, run `stage4.py` over the `stages/s3` JSON files of consecutive spreads. The individual stages can still be run separately:

Stage 1:

```bash
python src/booktrans/stage1.py /path/to/open_spread.tif -o output/stage1
```

Stage 2:

```bash
python src/booktrans/stage2.py /path/to/open_spread.tif \
  --stage1-json output/stage1/page_structure.json \
  -o output/stage2
```

Check each script's `--help` output for available options. Stage 2 currently defaults to German Tesseract OCR (`deu`).

## Development principles

1. Preserve source scans; never destructively modify them.
2. Preserve OCR lines as individual objects with their own geometry and text. Paragraphs and semantic regions are higher-level groupings that reference/contain those lines; they must not erase line-level data.
3. Keep geometric coordinates in a documented coordinate system. Stage 2 region coordinates are page-local pixels; page offsets locate pages in the original spread.
4. Keep detection, OCR, translation and rendering as separate pipeline stages.
5. Treat automatic classifications as hypotheses with reasons/confidence where possible. Human correction will be supported rather than pursuing brittle 100% automation.
6. Keep intermediate representations serializable and versioned.

## Project documentation

Start with `docs/DEVELOPMENT.md` for the current state and next task. See `docs/DECISIONS.md` for durable architectural decisions and `docs/DATA_MODEL.md` for the current intermediate representation.

### Stage 3

`src/booktrans/stage3.py` classifies OCR lines as normal page text or
illustration-associated text and keeps illustration objects separate. Text
inside illustrations is currently preserved but excluded from translation and
reconstruction.

```bash
python src/booktrans/stage3.py output/stage2/page_structure_stage2.json \\
  --stage1-json output/stage1/page_structure.json \\
  -o output/stage3
```

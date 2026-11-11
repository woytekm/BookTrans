# BookTrans

A Python-based document reconstruction and translation project for historical books. The initial target is a pre-war German architecture book to be translated into Polish while retaining original illustrations, page composition and an approximate historical typographic character.

**Current status:** early prototype, document analysis only. The current code is experimental and tuned against two supplied 1800-pixel-wide open-book TIFF scans. It is not yet a general-purpose book OCR or translation application.

## Current prototype

- `src/booktrans/stage1.py` — open-spread TIFF loading, gutter estimation, page splitting, coarse text/image candidate detection and initial OCR.
- `src/booktrans/stage2.py` — OCR within coarse text regions, OCR-line geometry, basic line classification, paragraph grouping based on visual horizontal ink bands, JSON output and diagnostic overlay.
- `examples/` — representative JSON and diagnostic outputs from the Stage 2.1 experiments. These are examples, not golden test fixtures.
- `docs/` — architecture, pipeline, data model, decisions and development status.

## Environment

Python 3.10+ is expected. Install the Python requirements:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Tesseract is an external system dependency. Install Tesseract and German language data (`deu`) using your operating system's package manager. For Debian/Ubuntu:

```bash
sudo apt install tesseract-ocr tesseract-ocr-deu
```

## Running the current prototype

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

# !Coded by AI  
This project aims for recreation of a pre war, public domain books with translation to another language in the process. 
Translated book should keep a look and feel of an original (font and layout wise).
I am focusing on a German pre war literature related to architecture and art history of Lower Silesia. 
Coded using Claude AI @2026

# BookTrans

A Python-based document reconstruction and translation project for historical books. The initial target is a pre-war German architecture book to be translated into Polish while retaining original illustrations, page composition and an approximate historical typographic character.

**Current status:** working prototype for one book. Layout analysis, OCR, the page-recreation model (`document.json`) and page reconstruction with fitted fonts work on the two supplied sample spreads (translated by hand). The automated translation service is the next piece and is not built yet. The code is tuned to the sample scans and is not yet a general-purpose application. **New session? Read `docs/HANDOFF.md` first.**

## Current prototype

- `src/booktrans/stage1.py` — open-spread TIFF loading, gutter estimation, page splitting, per-page deskew, illustration detection and coarse text regions.
- `src/booktrans/deskew.py`, `illustrations.py`, `imgutil.py` — helpers for per-page skew/shear correction, geometric illustration detection (before OCR) and OCR crops.
- `src/booktrans/stage2.py` — OCR line extraction (padded, 2x upscaled crops), line classification, geometric caption model, page-level paragraph detection, JSON output and diagnostic overlay.
- `src/booktrans/stage3.py` — illustration objects, caption/line association and translation status.
- `src/booktrans/booktrans.py` — **one-command wrapper**: runs stages 1–4 on a TIFF and writes the result folder described below.
- `src/booktrans/docmodel.py` — builds the nested page model (`document.json`) and crops the figures.
- `src/booktrans/render.py` — **page assembly**: rebuilds each page from `document.json` plus a translations JSON (clean paper canvas, figures and page furniture pasted back, translated text typeset into the original blocks).
- `src/booktrans/marks.py` — page numbers, printer's marks and rules (kept as image crops).
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

Rebuilding the pages with a translation (one command, or `render.py` separately):

```bash
python booktrans.py 0001.tif -o output --translations ../../examples/0001.pl.json
python render.py output/0001/document.json translations.json -o output/0001/render   # same, on its own
```

`render/` gets `page_NN_X.png` (full resolution when the scan was high-resolution), `compare.png` (original | reconstruction) and `report.json` (line counts, overflow warnings). The translations file maps each unit id of `document.json` to its Polish text. `prompts/translate_pl.txt` holds the translation prompt; `examples/0001.pl.json` is the translation of the first sample spread. The default font is Crimson Text (SIL OFL, in `fonts/`); `--font`, `--bold-font` and `--italic-font` take any serif TTFs with Polish letters (also accepted by `booktrans.py`). `pip install pyphen` enables Polish hyphenation.

**Font fitting.** The original's letter-spacing is wider than most book fonts', so the translation would set denser than the scan. By default (`--tracking auto`) the renderer fits the letter-spacing per block so the original lines, set in the new font, would be as wide as on the scan (capped at 0.12 em), with the size kept near the glyph height. `report.json` gives `tracking_em` and `lines_original` per unit. Manual controls: `--tracking EM` (0 = none), `--size-scale F`, `--word-space F`.

Useful options: `--layout spread|page|crop` (open book split at the gutter / one whole page / part of a page), `--scale auto|N` (how much larger than the sample scans the input is; auto measures the line pitch, so 600 DPI scans and crops are handled by analysing a downscaled working copy while OCR and figures use the full-resolution pixels), `--ocr-scale`, `--lang` (default `deu`), `-v` (print every region and OCR line with its confidence). Progress with timings is printed to the terminal while it runs.

This creates `output/0001/` with `document.json` (nested model: pages → blocks → lines, layout, typography, figure links, translation units), `pages/` (deskewed page images; all coordinates refer to them), `figures/` (every illustration cropped to a PNG), `debug/` (overlays) and `stages/` (raw JSON of stages 1–4). Each TIFF is processed on its own; to link paragraphs across spreads, run `stage4.py` over the `stages/s3` JSON files of consecutive spreads (a batch mode is planned, see `docs/HANDOFF.md` section 8). The individual stages can still be run separately:

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

Start with `docs/HANDOFF.md` (what works, how to run it, repository map, open issues, roadmap). Then:

- `docs/TRANSLATION.md`: contract and design for the translation service (input units, output JSON, glossary and prompt rules).
- `docs/RENDERING.md`: page assembly, line breaking and font fitting.
- `docs/PIPELINE.md`, `docs/DATA_MODEL.md`: stages and every JSON schema (including `document.json` and the translations/report files).
- `docs/DECISIONS.md`: durable decisions (ADR-001 ... ADR-013). `docs/DEVELOPMENT.md`: status log. `CHANGELOG.md`: what changed.

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

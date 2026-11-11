# Handoff: start here

Read this first in a new session. It states what works, how to run it, where everything is, how the
pieces fit, what was decided, and what is open. Details live in the other documents in this folder;
this one is the map.

Last updated: 2026-11-11 (branch `stage2-rework`).

## 1. What the project is

A Python pipeline that turns a scanned page or spread (TIFF) of an old German art-history book on
Silesian church portals into a Polish edition with the same layout:

```
TIFF -> layout + OCR -> document.json -> translation -> render -> Polish page PNG
        (stages 1-4)    (docmodel.py)     (NOT BUILT)    (render.py)
```

Goals: keep the layout, drawings and photographs exactly, set the Polish text in a similar font so the
page has the same look and density, and make the whole thing an automated workflow (the owner prefers
automation over by-hand tuning).

**Status:** detection, OCR, the document model and page reconstruction work on the sample pages
(`0001.tif`, `0002.tif`, pages 50-53). The translation service (a local LLM) is the next big piece and
does not exist yet; the two samples were translated by hand in a chat session
(`examples/0001.pl.json`, `examples/0002.pl.json`).

## 2. Working with the owner (preferences)

- Plain standalone scripts run from `src/booktrans/` (`python booktrans.py ...`). No virtualenvs, no
  package install, modules are installed by hand (`requirements.txt`).
- The owner is on a Mac and uses MacPorts for system packages (`sudo port install tesseract
  tesseract-deu`; the traindata then lives in `/opt/local/share/tessdata`). The development sandbox is a
  Linux container, so anything touching Tesseract data there needs `TESSDATA_PREFIX` pointing at a
  folder with `deu.traineddata`.
- Explanations should be precise and technical.
- Never type or store tokens or passwords. Pushing works through the attached-repo mechanism of the
  session; if a token is ever needed it must be fine-grained, limited to this repository and revoked
  afterwards.
- Every meaningful change updates `CHANGELOG.md` and the relevant document here, adds or adjusts tests
  and is committed with a clear message.

## 3. Quick start

```bash
cd src/booktrans
python booktrans.py /path/to/0001.tif -o /path/to/output                       # stages 1-4 -> document.json
python booktrans.py 0001.tif -o output --translations ../../examples/0001.pl.json   # ... and rebuild the pages
python render.py output/0001/document.json translations.json -o output/0001/render  # rebuild only
python booktrans.py /scans/mybook/ -o output [--resume]                        # a whole book: one document.json
python -m pytest -q ../../tests                                                # 43 tests, a few seconds
```

Useful options: `--layout spread|page|crop`, `--scale auto|N`, `--ocr-scale`, `--lang`, `-v`,
`--background paper|scan`, `--font/--bold-font/--italic-font`, `--tracking auto|EM`, `--size-scale`,
`--word-space`. Progress with timings goes to stderr.

Output folder `<out>/<tiff stem>/`: `document.json` (the page-recreation model), `pages/` (deskewed
pages, the coordinate system of everything), `figures/` (every illustration and page mark cropped,
from the full-resolution scan when there is one), `debug/` (overlays), `stages/s1..s4` (raw stage
JSON), and `render/` when translations were given (`page_NN_X.png`, `compare.png`, `report.json`).

## 4. Repository map

| Path | Role |
|---|---|
| `src/booktrans/booktrans.py` | one-command wrapper: runs stage1-4 as subprocesses, builds `document.json`, optional render |
| `src/booktrans/stage1.py` | page split (spread/page/crop), scale detection, deskew, illustration detection, coarse text regions |
| `src/booktrans/stage2.py` | Tesseract OCR lines, classification, caption model, paragraph detection |
| `src/booktrans/stage3.py` | illustration objects, caption association, translation status |
| `src/booktrans/stage4.py` | cross-page paragraph links, dehyphenation, translation units (`units.json`) |
| `src/booktrans/docmodel.py` | builds `document.json` (layout, typography, blocks, figures, units), crops figures |
| `src/booktrans/marks.py` | page numbers, printer's marks, rules (kept as image crops) |
| `src/booktrans/render.py` | page assembly: typesetting of Polish text, font fitting, compositing |
| `src/booktrans/scale.py` | resolution detection from the body line pitch |
| `src/booktrans/glossary.py`, `glossary/` | German -> Polish names and terms: lookup before translation, check after (`TRANSLATION.md`) |
| `src/booktrans/illustrations.py`, `deskew.py`, `imgutil.py`, `log.py` | helpers |
| `fonts/` | bundled Crimson Text (SIL OFL) |
| `prompts/translate_pl.txt` | the translation prompt |
| `examples/` | `0001.pl.json`, `0002.pl.json` (hand translations), render comparisons, older stage JSONs |
| `tests/` | pytest, synthetic data only (the sample scans are not in the repository) |

## 5. How each part works (short)

Full detail: `PIPELINE.md`, `RENDERING.md`, `DATA_MODEL.md`.

- **Scale.** The sample spreads are 1800x1315 px (about 135 DPI; the TIFF's 600 DPI tag is wrong).
  The original scans are about 8965x6534 at 600 DPI. All thresholds are tuned in sample pixels, so
  larger input is analysed on an area-averaged working copy (`scale.py` estimates the factor `f` from
  the body line pitch, reference 23.3 px) and the full-resolution pixels are used only for OCR crops and
  figure crops. JSON coordinates are always working pixels; `scale` and `image_full` map back.
- **Page split / deskew.** `--layout spread` splits at the gutter, `page` is one whole page, `crop` is a
  part of a page. Two-parameter affine deskew (baseline slope and edge slope).
- **Illustrations before OCR.** Tonal detector for photographs, "ink that is not in text lines" for
  line art. They are masked out of text analysis. Text inside a drawing is excluded from translation.
- **OCR and structure.** Tesseract `deu` on padded, upscaled crops; lines are permanent objects.
  Paragraphs come from a column-level short-last-line rule (the signal in this book); a line ending in a
  hyphen never ends a paragraph.
- **Stage 4.** Links a paragraph that continues on the next page or spread, dehyphenates
  (`Spitz-`+`bogen` joined, uppercase continuation keeps the hyphen), and writes translation units.
- **Document model.** `document.json` holds everything needed to rebuild a page: layout, columns, per
  block typography (glyph height, line pitch, alignment, indent, shortfall), figures with file links,
  page marks and rules, translation units.
- **Render.** Optimum-fit line breaking with justification and Polish hyphenation, original line
  pitch and anchoring, body text flowing through the original columns and across pages, headings,
  captions and footnotes in fixed frames. The font size and letter-spacing are fitted per block so the
  Polish text fills the original line widths (font fitting, see `RENDERING.md`).

## 6. Known limits (things that will bite)

- Tuned to one book. Two-column pages, same-size captions, ragged-right or indented paragraphs,
  running heads and multi-line footnotes are not covered (the owner will supply more samples).
- Reading order and multi-column layouts are not modelled.
- A paragraph that starts as the only line at the bottom of a page (an orphan) is not avoided: the body
  flows through the page capacity without widow/orphan control.
- OCR at ~135 DPI makes slips: digit confusions (3/5), lost spaces ("Portalmiteiner"), `F`/`E`, `k`/`E`
  ("Fin", "Kinfassung"), "s ist" for "Es ist". Higher resolution did not measurably improve clean
  text; its gain is in figure crops.
- Page numbers and printer's marks are pasted back as image crops, not re-typeset.
- Fonts: the original is a light, widely letter-spaced Antiqua (no Fraktur). Crimson Text is denser, so
  tracking is added. There is no automatic choice between several fonts yet.
- A synthetic 5x upscale of a drawing page splits the main plate (merge gap limit 40 px); not seen on
  real data.
- Tesseract confidence is not a calibrated probability; there is no human review tool.

## 7. Translation: what the next session builds

See `TRANSLATION.md` for the contract. In short: read `document.json` units, call a local LLM with the
prompt in `prompts/translate_pl.txt`, write a translations JSON in the format of `examples/*.pl.json`,
which `render.py` consumes unchanged.

## 8. Batch mode (books) and units cut at a spread boundary

`booktrans.py` takes a folder of TIFFs (name order) or several files and builds **one** `document.json`:
stages 1-3 run per spread (`stages/<tiff name>/s1..s3`, `--resume` skips finished ones), stage 4 runs
once over all spreads, pages are numbered consecutively (`page`; `spread`, `page_in_spread`,
`source_tiff` say where they came from), block ids keep Stage 4's `sN-` prefix so they stay unique,
and image/figure files are prefixed with the TIFF name. A paragraph cut at a spread boundary is a single
unit (on the samples `0001` u0009 + `0002` u0008 = book unit `u0009`, one sentence:
"(Dolny Śląsk i Republika Czechosłowacka) ..."). Translate such a unit whole; the renderer flows it across
the pages. Sample: `examples/book_0001-0002.pl.json` (both spreads as one book; unit numbers differ
from the per-spread files because they are renumbered across the book).

Render output for a book: `page_NNN_X.png` for every page and one `compare_page_NNN.png` per page
(`compare.png` only up to 4 pages).

## 9. Roadmap

1. Translation service (`translate.py`), see `TRANSLATION.md`.
2. Glossary content: import the place-name CSV, review `proposed` terms, resolve ambiguous places; wire the
   glossary into the translation service (the tooling itself exists, see `TRANSLATION.md`).
3. Automatic font choice among supplied fonts (score x-height ratio, advance width, stem darkness
   against the scan; the tracking fit already exists).
4. More samples: two-column pages, ragged right, footnotes, running heads; tune and add tests.
5. OCR post-correction (LLM pass or dictionary) before translation.
6. Human review of units and blocks; export (PDF / per-page images).
7. DPI-independent thresholds beyond the line-pitch scale factor.

## 10. Verification habits that worked

- Run `python -m pytest -q` before every commit (43 tests).
- After any layout change, render both samples and look at `render/compare.png` (original | rebuilt
  per page). Most bugs here are visual (overlapping captions, grey patches, wrong tracking).
- Check `render/report.json`: line counts against `lines_original`, `warnings`, `tracking_em`.
- Native-resolution output must stay unchanged when only the high-resolution path is modified.

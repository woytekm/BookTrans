# Handoff: start here

Read this first in a new session. It states what works, how to run it, where everything is, how the
pieces fit, what was decided, and what is open. Details live in the other documents in this folder;
this one is the map.

Last updated: 2026-11-18. **Newest: the render window in the GUI** (section 11, `GUI.md` section 8, ADR-022) and the
**rendering rework** (section 12: layout plan + per-page drawing, body text through
line slots, flow kept in step with the pages; ADR-021). New since 2026-11-11: **a GUI** (projects, Run pipeline, word boxes and marks on the page image, the
correction editor, Re-run OCR correction, Run translation; section 11, `GUI.md`) and **translation through Claude Code**
(`translate.py --provider claude-code`, the owner's Claude subscription; section 7b). Neither has been tried on a real book yet.

## 1. What the project is

A Python pipeline that turns a scanned page or spread (TIFF) of an old German art-history book on
Silesian church portals into a Polish edition with the same layout:

```
TIFF -> layout + OCR -> document.json -> OCR correction -> translation -> layout plan -> draw -> Polish page PNG
        (stages 1-4)    (docmodel.py)     (ocrfix.py, GUI)  (translate.py)  (layout.py)  (render.py)
```

Goals: keep the layout, drawings and photographs exactly, set the Polish text in a similar font so the
page has the same look and density, and make the whole thing an automated workflow (the owner prefers
automation over by-hand tuning).

**Status:** detection, OCR, the document model and page reconstruction work on the sample pages
(`0001.tif`, `0002.tif`, pages 50-53). OCR correction works offline (`ocrfix.py`) and by hand in the GUI.
Translation: `translate.py` was run once with a local model (Gemma 4 12B in LM Studio; quality too low,
section 7a); the owner now translates through **Claude Code** (section 7b), which has **not yet been run with a
real login**. The two samples were translated by hand in a chat session (`examples/0001.pl.json`,
`examples/0002.pl.json`); they are the reference to compare a machine translation with.

## 2. Working with the owner (preferences)

- Plain standalone scripts run from `src/booktrans/` (`python booktrans.py ...`). No virtualenvs, no
  package install, modules are installed by hand (`requirements.txt`).
- The owner is on a Mac and uses MacPorts for system packages (`sudo port install tesseract
  tesseract-deu`; the traindata then lives in `/opt/local/share/tessdata`). The development sandbox is a
  Linux container, so anything touching Tesseract data there needs `TESSDATA_PREFIX` pointing at a
  folder with `deu.traineddata`.
- **OCR model: use `tessdata_best` `deu`.** On the 600 DPI test spread the default (fast) `deu` read the
  heading as `MIEDELÄLTERLICHE KIRCHENPORTÄALE` (confidence 28); `tessdata_best` reads it correctly and
  fixes `Kigenart`/`besonders der`. The owner copied `deu.traineddata` from
  <https://github.com/tesseract-ocr/tessdata_best> into `/opt/local/share/tessdata` (replacing the
  MacPorts fast one), so the pipeline picks it up without any option. Remaining OCR errors on that
  spread: `Finzelheiten` (E/F), `desursprünglichen` (glued), `OBERSCHLESTEN` (I/T), footnote mark `¹`
  read as `‘`.
- Explanations should be precise and technical.
- GUI toolkit: **Tkinter** (proposed 2026-11-15, ADR-015; the owner has used the GUI on his Mac since 2026-11-17 and switched
  booktrans to MacPorts Python 3.12 with `py312-tkinter` (Tk 8.6), because the system Python 3.9's Tk 8.5 drew empty windows;
  no formal confirmation of the toolkit). It needs a Pillow with `ImageTk`; Dear PyGui (already used for the sound
  meter app) was rejected for the editor part, PyQt earlier because of its dependencies. `python envcheck.py` says what is missing.
- Machines: the owner works on booktrans from a MacBook Air; LM Studio (when used) runs on another computer on the LAN. The
  local models he can run (16 GB) translate too poorly, so translation goes through Claude Code for now (section 7b).
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
python translate.py output/book/document.json -o output/book/translations.json   # LM Studio, glossary, checks
python translate.py output/book/document.json --provider claude-code             # Claude Code CLI, your subscription (section 7b)
python render.py output/0001/document.json translations.json -o output/0001/render  # rebuild only (--pages 3,5-7: draw some pages)
python booktrans.py /scans/mybook/ -o output [--resume]                        # a whole book: one document.json
python -m pytest -q ../../tests                                                # 278 tests, ~40 s (the 41 GUI window tests need tkinter and a display; they skip otherwise)
python envcheck.py [PROJECT]                                                   # is the environment intact? (Tesseract + deu model, modules, dictionaries, glossary)
python gui.py --demo /tmp/demo                                                 # the GUI on a synthetic project (no scans, no Tesseract needed)
python gui.py /path/to/project                                                 # a project made with File > New project; Render pages… (Cmd/Ctrl+Shift+P)
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
| `src/booktrans/typeset.py` | fonts, pieces, optimum-fit line breaking (no page model) |
| `src/booktrans/layout.py` | the layout plan of the book: line slots, page-synchronised body flow, per-page fit, fixed frames (`RENDERING.md`) |
| `src/booktrans/render.py` | drawing a page from the plan (canvas, figures, kept text, lines), `render()`, the CLI |
| `src/booktrans/fontcheck.py` | which font matches the scan: specimens and a score per candidate (`RENDERING.md`) |
| `src/booktrans/render_session.py`, `gui_render.py` | the render window: toolkit-free session (layout, drawing, adjustments) and the Tk window |
| `src/booktrans/render_overrides.py` | toolkit-free: human adjustments of blocks and pages (`review/render_overrides.json`), hash-tied |
| `src/booktrans/scale.py` | resolution detection from the body line pitch |
| `src/booktrans/ocrfix.py` | stage 5: offline OCR correction of the units (dictionaries, confusions, lost spaces), corrections log and review file (`OCR_CORRECTION.md`) |
| `src/booktrans/ocrreview.py` | stage 5b: LLM proposals for the flags `ocrfix.py` left open (`propose` / `show` / `accept`), prompt `prompts/ocr_review_de.txt` |
| `src/booktrans/translate.py` | translation through the Claude Code CLI, an OpenAI-compatible endpoint (LM Studio) or the Anthropic API: glossary block, checks, retries, cache, `--pages`, `--force`, token usage (`TRANSLATION.md`) |
| `src/booktrans/glossary.py`, `glossary/` | German -> Polish names and terms: lookup before translation, check after (`TRANSLATION.md`) |
| `src/booktrans/illustrations.py`, `deskew.py`, `imgutil.py`, `log.py` | helpers |
| `src/booktrans/gui.py` | **GUI main window**: projects, page list with OCR-correction counts, the page JSON in three views (`GUI.md`) |
| `src/booktrans/gui_dialogs.py`, `gui_correction.py`, `gui_widgets.py` | new-project / environment / command dialogs; the OCR correction window (page image with marks, zoom, review-JSON editor, decisions); shared Tk widgets, colours, dark theme |
| `src/booktrans/gui_run.py` | Tk: run windows: `RunWindow` (Run pipeline; mode `ocrfix`: Re-run OCR correction), `TranslateWindow` (Run translation) |
| `src/booktrans/project.py` | toolkit-free: project folder, `project.json` (pipeline parameters, translation settings), the `booktrans.py`, `ocrfix.py` and `translate.py` commands |
| `src/booktrans/unit_edits.py`, `ocr_decisions.py` | the GUI's human work in `review/`: edited unit texts (hash-tied) and decisions on OCR flags; applied by `ocrfix.py run --edits/--decisions` |
| `src/booktrans/envcheck.py` | toolkit-free: the environment check (also `python envcheck.py` in a terminal) |
| `src/booktrans/pageindex.py` | toolkit-free: traces `ocrfix` offsets back to pages/lines (`replay` of stage 4), page rows, the page JSON views, marks |
| `src/booktrans/gui_demo.py` | a synthetic demo project built with the real stage 4 and `ocrfix.run` |
| `fonts/` | bundled Crimson Text and Crimson Pro (SIL OFL) |
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
- **Render.** Two steps: `layout.py` makes a plan of every line of the book (`layout.json`), `render.py` draws
  a page from it. Optimum-fit line breaking with justification and Polish hyphenation; body text poured into
  the original line slots (so it never overprints a figure; two columns and text beside a picture work), kept in
  step with the pages (a paragraph never starts before its original page), size and letter-spacing fitted per
  page around one book size, orphans and widows avoided; headings, captions and footnotes in fixed frames
  (`RENDERING.md`).

## 6. Known limits (things that will bite)

- Tuned to one book. Two-column pages, same-size captions, ragged-right or indented paragraphs,
  running heads and multi-line footnotes are not covered (the owner will supply more samples).
- Reading order is not modelled beyond the renderer's column order (columns found per page from the body
  blocks; untested on a real two-column page).
- A page whose Polish is much shorter keeps empty lines at its foot (the flow is kept in step with the pages
  rather than pulling the next page's text back; `report.json` warns).
- OCR at ~135 DPI makes slips: digit confusions (3/5), lost spaces ("Portalmiteiner"), `F`/`E`, `k`/`E`
  ("Fin", "Kinfassung"), "s ist" for "Es ist". Higher resolution did not measurably improve clean
  text; its gain is in figure crops.
- Page numbers and printer's marks are pasted back as image crops, not re-typeset.
- Fonts: the original is a light, widely letter-spaced, low-contrast Antiqua with a large x-height (no Fraktur). Crimson
  Text is denser, so tracking is added. `fontcheck.py` scores candidates; the choice is not automatic yet.
- A synthetic 5x upscale of a drawing page splits the main plate (merge gap limit 40 px); not seen on
  real data.
- Tesseract confidence is not a calibrated probability.
- The GUI and the Claude Code translation have only been run on the synthetic demo and under Linux/Xvfb (section 11).

## 7. Translation

`translate.py` reads the units of the book (from `document.corrected.json` when it exists), looks each up in
the glossary, sends one request per unit (the prompt in `prompts/translate_pl.txt` plus rules, glossary list,
kind of text and the preceding paragraph as context) to the chosen engine (`--provider claude-code`, `openai`
for LM Studio, or `anthropic`), checks and retries, caches every answer, and writes the translations JSON that
`render.py` consumes (`TRANSLATION.md`). A repeated run only sends units whose prompt changed; `--pages` /
`--only` update part of the output and keep the rest; `--force` asks again despite the cache. In the GUI:
*Run translation* (section 11). Next: the first real run with Claude Code (section 7b), then tune the rules
prompt and the checks on what the model actually does, and fill the glossary.

## 7a. First real translation run (2026-11-11, `google/gemma-4-12b` in LM Studio)

Findings, to guide the next session: (1) Gemma 4 thinks by default; switch "Enable Thinking" off in
LM Studio or every answer is empty (`TRANSLATION.md`). (2) Speed was about 5.5 tok/s, a paragraph takes
about a minute. (3) **Translation quality of the 12B model is mediocre**: grammatical slips and
non-words ("Uwarunkom", "kościojachach", "przedejejsze"), one sentence translated to nonsense
("Bezczynności odłupania tynku" for "Ohne Abschlagen des Putzes"), "podesty" for Sockel, "tężni" for
Fürstentümern, a caption in broken Polish. Ideas not yet built: a second "Polish editor" pass, a larger
model, a Polish spell-check (hunspell) to flag non-words, the model-comparison mode.

## 7b. Translation through Claude Code (2026-11-17, merged in PR #16; ADR-019)

The owner decided to set the local models aside for a while: what runs on his machines is too weak for a
quality translation. `translate.py --provider claude-code` runs the Claude Code CLI (`~/.local/bin/claude` on the
Mac, logged in with his Claude subscription; no API key) headless, one call per unit, with the same prompt,
glossary, checks, retries and cache as before. A usage limit stops the run cleanly; re-running continues from the
cache. Details, flags and why not `--bare`: `TRANSLATION.md`, section "Claude Code as the translation engine".
**Not yet run with a real login.** Next: translate the two sample spreads (one page first: `--pages 1`, or the GUI's
*Run translation* with pages), compare with the hand translations in `examples/`, then a whole book; Opus vs
Sonnet on the same units (a different model is a different cache key, so both stay cached).

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

1. First real run of `translate.py --provider claude-code` (section 7b; from the GUI: *Run translation*); tune prompt, rules and checks on the output; a Polish spell-check (hunspell) to flag non-words. (LM Studio was run once, section 7a; set aside for now.)
2. Glossary content: import the place-name CSV, review `proposed` terms, resolve ambiguous places; (the glossary is already wired into
   `translate.py`).
3. **Rendering (in progress, section 12):** checks in `report.json`, render settings and per-block overrides, ink and
   font (light Antiqua), then the GUI render window. Automatic font choice among supplied fonts (score x-height ratio, advance width, stem darkness
   against the scan; the tracking fit already exists).
4. More samples: two-column pages, ragged right, footnotes, running heads; tune and add tests.
5. OCR post-correction: dictionary stage done (`ocrfix.py`, `OCR_CORRECTION.md`); `ocrreview.py` asks an LLM about each flag (not yet run against a real model: try it on LM Studio / Gemma and tune `prompts/ocr_review_de.txt`); next: tune on more pages, fill `glossary/vocabulary.txt`.
6. Human review in the GUI (section 11, `GUI.md`): done: word boxes, marks, the correction editor, decisions, running the pipeline,
   the OCR correction and the translation. Open: the GUI's Next list (section 11), a translation review view (German and Polish side by
   side, flags), render and export (PDF / per-page images) from the GUI.
7. DPI-independent thresholds beyond the line-pitch scale factor.

## 10. Verification habits that worked

- Run `python -m pytest -q` before every commit (278 tests). On a headless Linux machine the GUI tests need a display:
  `Xvfb :99 &` and `DISPLAY=:99 python -m pytest -q` (or `xvfb-run -a python -m pytest -q`).
- After any layout change, render both samples and look at `render/compare.png` (original | rebuilt
  per page). Most bugs here are visual (overlapping captions, grey patches, wrong tracking).
- Check `render/report.json`: line counts against `lines_original`, `warnings`, `tracking_em`.
- Native-resolution output must stay unchanged when only the high-resolution path is modified.

## 11. GUI (started 2026-11-15): start here for GUI work

Design, decisions, schemas and the stage plan are in **`docs/GUI.md`** (read it, section 2 and 10 first). ADR-015 ... ADR-018 in
`DECISIONS.md`. In short: Tkinter; a project folder with `project.json`, `pipeline/<book>/` and `review/`; the GUI reads the pipeline
output and edits only `review/`; the editable thing is the unit text, shown per page as a "review JSON".

**What the owner asked for** (2026-11-15): a Python GUI with a main window (set up a project: input folder with TIFFs, output
directory, initial parameters; check that the environment is intact, e.g. dictionaries; list of processed pages; JSON of each page)
and, as a *separate* window per page, an OCR correction window: the original page with detected errors marked in two or three colours
(autocorrected / uncertain), and at the bottom the page's OCR document model as JSON to correct by hand and save. Work in stages.

**Done in stage A** (71 new tests): `project.py`, `envcheck.py`, `pageindex.py` (toolkit-free); `gui.py`, `gui_dialogs.py`, `gui_widgets.py`,
`gui_correction.py` (Tk); `gui_demo.py`. The main window has: new/open/recent project, environment check (also `python envcheck.py`),
the pipeline command with a warning before a destructive re-run, the page list (sortable; counts of auto-corrected / uncertain / watch /
human-decided words per page), three read-only JSON tabs per page (page model, review JSON, marks) with search and *Copy JSON*, and
the per-page correction window as a shell (page image fitted, counts, opens/reuses/closes correctly). The GUI never writes pipeline output;
the only file it writes is `project.json` (and `~/.booktrans_gui.json` for recent projects).

**Not verified on real data.** Everything was run on the synthetic demo and under Linux/Xvfb, never on a Mac and never on a real
book's `document.json`. First things to do: `python envcheck.py`, `python gui.py --demo /tmp/demo`, then a real project. To open an existing
pipeline result in the GUI: create a project with the same input folder and the book's name, then move or symlink the result folder
(`output/<book>/`) to `<project>/pipeline/<book>/`. Expect to adjust fonts and spacing on macOS (the layout was tuned on Linux Tk 8.6).

**Assumptions to confirm with the owner** (each can still change at small cost; the toolkit-free modules do not depend on them):
1. Tkinter instead of Dear PyGui (needs `py3NN-tkinter` from MacPorts). 2. The editable text is the *unit* (paragraph, possibly across
pages), not the OCR line; line texts stay raw. 3. Stage 2 should keep word boxes (stage B; books processed earlier need a re-run of
stages 1-2 to get exact boxes). 4. Flags of an edited unit count as reviewed. 5. Colours: blue auto, red uncertain, amber watch, green human.

**Added 2026-11-17** (after the owner tried stage A on a Mac and merged it): the page list is readable again (explicit colours; macOS
dark mode gave white on white); **Run pipeline** (Project menu or the button: parameters, `--resume`, live log, Stop; `gui_run.py`,
`project.PipelineRun`); **stage B done**: `stage2.py` stores `words` per OCR line, document schema 1.1, `pageindex.line_box`; **marks on
the page image** in the correction window (`gui_correction.compose_marks`, hover, show/hide per class). The TIFF is never changed:
the marks are drawn over the deskewed page PNG the pipeline made, whose pixels are the coordinates of the model. A book processed
*before* schema 1.1 has no word boxes: its marks are estimated (outlines only) until stages 1-2 are re-run (`booktrans.py` without
`--resume`, which deletes the result folder, see the pitfalls). Not yet verified on a real book: marks on real word boxes.

**Also 2026-11-17: dark theme** (`gui_widgets.apply_dark_theme`, the single palette at the top of `gui_widgets.py`; do not hard-code colours elsewhere, a test fails if you do) and **zoom** in the correction window (buttons, Ctrl/Cmd + wheel, drag, scroll bars; `set_zoom`, `zoom_by`, `zoom_fit`).

**Also 2026-11-17 (evening): stage C editor and the edit part of D.** The correction window has Prev/Next (PageUp/PageDown), the editable
review JSON with Save/Revert (`unit_edits.validate_review`, `CorrectionWindow.save`), click a mark to put the cursor on its word
(`locate`), right-click decisions on flags (`menu_for`, `decide`, `accept_page`; stored by `ocr_decisions.py` in `review/ocr_decisions.json`, applied by `ocrfix.py run --decisions` on the next `--resume` run; `MainWindow.update_decisions`), a find box over the editor text (`find_next`, `_collect`; Cmd/Ctrl+F), unsaved-changes guards. Edits live in `review/unit_edits.json` (`unit_edits.py`: hash of `text_raw`, stale handling,
`apply_edits`); `MainWindow.commit_edits` writes the file first, then applies in memory and re-marks; `ocrfix.py run --edits` and
`booktrans.py --edits` reproduce them in `document.corrected.json` (the GUI's Run command always passes `--edits`). To get saved edits
into the file `translate.py` reads: *Re-run OCR correction* (since 2026-11-17 late; before, Run pipeline with `--resume`). Not yet tried
on a real book.

**Also 2026-11-17 (late): Re-run OCR correction** (header button, Project menu, Cmd/Ctrl+Shift+O): runs only `ocrfix.py` with the saved
edits and decisions, so a Save reaches `document.corrected.json` (the file `translate.py` reads) in seconds. And `--resume` now works for a
project whose input folder holds a single TIFF (before, the wrapper silently redid stages 1-4 and deleted the result folder).

**Then 2026-11-18: Run translation** (header button, Project menu, Cmd/Ctrl+Shift+T; `gui_run.TranslateWindow`): the project keeps
translation settings in `project.json` (schema 1.1: engine, default `claude-code`; model; endpoint; glossary files; source
corrected/raw; timeout; retries; no-think) and runs `translate.py` -> `<project>/translation/translations.json`, for the whole
book or some pages (`--pages`, new), with a dry-run option. It warns when `document.corrected.json` is missing or older than the
saved edits/decisions. `translate.py` fixes found on the way: a partial run (`--only`/`--pages`) now keeps the other units in the
output and uses the whole book's context (same cache keys as a full run), and the output folder is created.
Also: `--force` (GUI: *re-translate* checkbox) asks the model again for the selected pages, ignoring the cache.

**Then 2026-11-18: the render window** (header button *Render pages…*, Project menu, Cmd/Ctrl+Shift+P; `gui_render.py` over the
toolkit-free `render_session.py`; `GUI.md` section 8, ADR-022): page list with check counts, the rebuilt page beside the scan at any
zoom, click a block to select it; tabs *Book* (fonts, letter-spacing, size, word space, indent, page fit, background, ink), *Page*
(the page's fit, its checks) and *Block* (frame adjustments, keep original, the Polish text); every change saved at once and the book
laid out again; *Render book…* runs `render.py` (pages, resolution, PDF) -> `<project>/render/`. Tried on the owner's 14-page book
(layout 3.7 s). `gui.py --demo` writes a synthetic translation so the window works on the demo.

**Also 2026-11-18: input folder** of an open project shown in the header and the Run pipeline window, changeable (Project >
Change input folder…). **Clear project data** (Project menu): pipeline output, translation and rendered pages removed for a fresh start;
optionally the translation cache and the review work; settings kept (`GUI.md` section 8).

**Next** (`GUI.md` sections 6, 8, 10), suggested order:
1. ~~A "re-run OCR correction" button~~ **done 2026-11-17** (*Re-run OCR correction…* next to *Run pipeline…*, Cmd/Ctrl+Shift+O: stage 5
   alone with the saved edits and decisions, `project.ocrfix_command`, the run window in mode `ocrfix`). Still open: stale edits
   (`MainWindow.stale_edits`) are named in the status line after loading, but there is no way yet to look at or re-attach them.
2. Mark list next to the image, next/previous mark, keyboard shortcuts for the decisions (they exist only as right-click and menu today).
   Decisions do not need a backup of `ocr_review.json` any more: they live in `review/ocr_decisions.json`.
3. In an edited unit, the words that differ from `text_ocrfix` get the `human` class (today only its flags turn green).
4. Page status (todo / reviewed) in `review/page_status.json` and a column in the page list.

**Pitfalls found in this session:**
- **Never touch Tk from a thread, and never let the cyclic GC run in one.** A `Variable`/`PhotoImage` finalised in the loading thread hung the
  test suite (thread dump with `pytest -o faulthandler_timeout=5` showed `Variable.__del__` / `PhotoImage.__del__` in the worker). `MainWindow`
  disables the collector and collects from the main loop; images are `SafePhoto`. See `GUI.md` section 11.
- `ImageTk.PhotoImage(img)` without `master=` binds to tkinter's default root, which may be a destroyed one (`pyimage6 doesn't exist`).
- `booktrans.py` deletes its result folder before a full run. Until 2026-11-17 the single-TIFF path (`process`) *always* did, ignoring `--resume`
  (and the GUI showed no warning); it now honours `--resume` like a book (stages 1-3 kept when `stages/s3` is complete).
  Hence `review/` is outside `pipeline/` (ADR-016); `ocr_review.json` inside the book folder is still at risk (stage E backs it up).
- Unit ids (`u0001` ...) are renumbered on every run; a human edit must be keyed by id *and* a hash of `text_raw` (`GUI.md` section 6).
- An `ocr_corrections[].offset` for `abbrev_space` is the *end* of the abbreviation (`B.` in `B.in`), a zero-length insertion; every
  other rule's offset is the start of the replaced token. `pageindex` handles both (test in `test_pageindex.py`).
- Word marks must survive a hyphenated word at a line end (`Spitz-` / `bogen`): one mark, two fragments, possibly on two pages.
  Done in `pageindex.replay` / `UnitMap.locate`.
- The tests found two bugs that only running reveals: widgets used before they exist (menu state before the toolbar) and a dialog that
  overwrote a name the user had set. Run the GUI tests under a display before declaring anything done.
- Sandbox notes (not for the Mac): `tkinter` needed `apt-get update && apt-get install python3-tk` for `/usr/bin/python3.12`; headless
  screenshots work with `Xvfb :99` and `PIL.ImageGrab.grab(xdisplay=':99')`; the default `python3` of the sandbox has OpenCV but no Tk.

## 12. Rendering rework (started 2026-11-18; start here for render work)

The owner asked to work on output page rendering, viewing and adjusting layout and font, and agreed that the render
engine should be complete before a GUI goes on top. Agreed order (2026-11-18):

1. ~~Split layout from drawing~~ **done**: `typeset.py` / `layout.py` (plan, `layout.json`) / `render.py` (`draw_page`).
2. ~~Line slots~~ **done**: body text into the original line positions (column order, figure wrap).
3. ~~Flow in step with the pages~~ **done**: per-page fit with lookahead, orphan/widow control.
4. ~~Checks in `report.json`~~ **done**: collision (figure, graphic, kept text, other block, page edge), overflow, drift, blank, loose/tight (`layout.check_plan`).
5. ~~Settings~~ **done**: `project.json` → `render` (schema 1.2, `Project.render_command` → `<project>/render/`), adjustments per
   block / page in `review/render_overrides.json` (`render_overrides.py`, hash-tied, `render.py --overrides`).
6. ~~Ink and font~~ **done**: `--ink auto` (measured), font families without mixing, variable fonts (`path@300`), bundled Crimson
   Pro (light weights), **`fontcheck.py`** (specimens + width / x-height / darkness score per candidate font on the real scan).
   **Owner to do:** run `fontcheck.py` on the 600 DPI sample with candidate fonts and pick one.
7. ~~PDF, emphasis, tests~~ **done**: `render.py --pdf` (page by page, physical size), `<i>`/`<b>`/`<sp>` runs in translations
   (renderer side; detection in stage 2 and the prompt rule are open), render no longer keeps every full-resolution page in
   memory. 250 tests.
8. ~~The GUI render window~~ **done** (`gui_render.py`, `render_session.py`; section 11).
9. ~~Captions~~ **done** (2026-11-18, branch `captions`; `CHANGELOG.md`): renderer (one size, free space, figure alignment, no
   hyphenation, ink-only line drawings, 300 DPI output), pipeline (`captions.py`: mask stops at captions, pieces joined, strips read
   again; `marks.is_rule`; figure crops clipped; `stage2.caption_sized`), correction window *Captions…*. Needs stages 1-2 again
   (`booktrans.py` without `--resume`) and a re-translation of the changed units. Still open: digit confusions in caption numbers
   (page 55 reads 45 for 43: fix by hand in *Captions…*); a translation much longer than its narrow slot (page 52's `59 POLITZ
   SCHAFTRING` -> 4 lines at 9.7 px: shorten the translation or set the block's size / max width by hand); a stage 1 "caption"
   region on blank margin paper (page 58) now reads `[REEL LER ENT SER` / `TEEN` (also on main's stage 2).
   Next: automatic font choice from `fontcheck.py`'s score; emphasis detection in stage 2 + prompt rule; vector PDF; incremental
   layout for long books; glossary reuse between projects (discussed, paused by the owner).

Decided by the owner (2026-11-18): **Crimson Text Regular is the default font**, configurable in `config/fonts.json`
(or `$BOOKTRANS_FONTS`); **adjustments are book-wide plus per block / per page** (as built). First real pipeline output
(14 pages, `test` project, Claude translation) rendered 2026-11-18; findings below.

**Findings on the real book (2026-11-18).** Renderer fixes made: scan noise, stray marks as lines, overlapping blocks,
even line grid, a whole page of body text classified as a caption, the ink colour (see `RENDERING.md`), a slow line
breaker for long ragged frames (memo ignored the line number: 23 s -> 8 s for the book). **Upstream problems:** (1)-(3) are now
repaired in **stage 4** (and the cause of (2), a margin mark taken for an indented first line, in **stage 2**: the stray
mark no longer takes part in paragraph detection; needs a run of stages 1-2) (2026-11-18, `PIPELINE.md` 8a; rerun with `booktrans.py --resume`, which keeps stages 1-3):
(1) stage 2 made paragraphs of scan noise at page edges (`|`, `N`; page 62) that got units and were translated ->
excluded as noise; (2) margin marks (`)`, `Ef`, `5`, `L`) became first lines of paragraph blocks and split
paragraphs: page 62's "Dem als zweischiffige Halle ... neu eingebaut." was 4 units translated as fragments ("w roku
1900" / ") przeprowadzonej ..."), and the noise strip at the page edge took the cross-page link, so "... Während" /
"hier die Gewändepfosten" became two units and the translation repeated "Podczas gdy" -> marks left out, split
paragraphs joined, links correct (the 14-page test book: 74 units -> 56); (3) page 59's body text is one 21-line
*caption* (u0042): stage 4 links into it, the renderer sets it as body text (stage 2 itself still misclassifies); (4) the caption of plate 40 lost
"ZIEGENHALS" in OCR, the model then read "OS" (Oberschlesien) as Oels and translated "Oleśnica" (should be Głuchołazy):
the glossary should know "OS" = Oberschlesien and captions need a check that the place name was read.
**Not yet run on the real sample pages** (`0001.tif`, `0002.tif` are not in the repository): render both and compare
`compare.png` with `examples/0001_render_compare.jpg` / `0002_render_compare.jpg`; expect the same look on those
simple pages, the slots matter on pages with figures in the column.

# Handoff: start here

Read this first in a new session. It states what works, how to run it, where everything is, how the
pieces fit, what was decided, and what is open. Details live in the other documents in this folder;
this one is the map.

Last updated: 2026-11-17 (late evening). New since 2026-11-11: **a GUI**: stage A, Run pipeline, word boxes (schema 1.1) and marks on the page image (section 11, `GUI.md`). **New: translation through Claude Code** (`--provider claude-code`, section 7b).

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
exists as `translate.py` but has **not been run against a real model yet**; the two samples were
translated by hand in a chat session (`examples/0001.pl.json`, `examples/0002.pl.json`).

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
- GUI toolkit: **Tkinter** (proposed in session of 2026-11-15, ADR-015, **owner has not confirmed yet**). It needs the MacPorts
  package `py3NN-tkinter` (NN = Python minor version) and a Pillow with `ImageTk`; Dear PyGui (already used for the sound
  meter app) was rejected for the editor part, PyQt earlier because of its dependencies. `python envcheck.py` says what is missing.
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
python render.py output/0001/document.json translations.json -o output/0001/render  # rebuild only
python booktrans.py /scans/mybook/ -o output [--resume]                        # a whole book: one document.json
python -m pytest -q ../../tests                                                # 207 tests, ~10 s (the 12 GUI window tests need tkinter and a display; they skip otherwise)
python envcheck.py [PROJECT]                                                   # is the environment intact? (Tesseract + deu model, modules, dictionaries, glossary)
python gui.py --demo /tmp/demo                                                 # the GUI on a synthetic project (no scans, no Tesseract needed)
python gui.py /path/to/project                                                 # a project made with File > New project
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
| `src/booktrans/ocrfix.py` | stage 5: offline OCR correction of the units (dictionaries, confusions, lost spaces), corrections log and review file (`OCR_CORRECTION.md`) |
| `src/booktrans/ocrreview.py` | stage 5b: LLM proposals for the flags `ocrfix.py` left open (`propose` / `show` / `accept`), prompt `prompts/ocr_review_de.txt` |
| `src/booktrans/translate.py` | translation through an OpenAI-compatible endpoint (LM Studio), the Claude Code CLI or the Anthropic API: glossary block, checks, retries, cache, token usage |
| `src/booktrans/glossary.py`, `glossary/` | German -> Polish names and terms: lookup before translation, check after (`TRANSLATION.md`) |
| `src/booktrans/illustrations.py`, `deskew.py`, `imgutil.py`, `log.py` | helpers |
| `src/booktrans/gui.py` | **GUI main window**: projects, page list with OCR-correction counts, the page JSON in three views (`GUI.md`) |
| `src/booktrans/gui_dialogs.py`, `gui_correction.py`, `gui_widgets.py` | new-project / environment / command dialogs; the OCR correction window (a shell in stage A); shared Tk widgets and colours |
| `src/booktrans/project.py` | toolkit-free: project folder, `project.json`, the `booktrans.py` command for a project |
| `src/booktrans/envcheck.py` | toolkit-free: the environment check (also `python envcheck.py` in a terminal) |
| `src/booktrans/pageindex.py` | toolkit-free: traces `ocrfix` offsets back to pages/lines (`replay` of stage 4), page rows, the page JSON views, marks |
| `src/booktrans/gui_demo.py` | a synthetic demo project built with the real stage 4 and `ocrfix.run` |
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
- Tesseract confidence is not a calibrated probability. The GUI (stage A) shows the page list, the page JSON and the OCR-correction
  counts per page, but cannot mark words on the page image or edit yet (section 11).

## 7. Translation

`translate.py` reads the `document.json` units, looks each up in the glossary, calls an LM Studio
(OpenAI-compatible) endpoint with the prompt in `prompts/translate_pl.txt` plus rules and glossary list,
checks and retries, and writes the translations JSON that `render.py` consumes (`TRANSLATION.md`). Next:
run it for real (Gemma 12B on the Mac), tune the rules prompt and the checks on what the model actually
does, and fill the glossary.

## 7a. First real translation run (2026-11-11, `google/gemma-4-12b` in LM Studio)

Findings, to guide the next session: (1) Gemma 4 thinks by default; switch "Enable Thinking" off in
LM Studio or every answer is empty (`TRANSLATION.md`). (2) Speed was about 5.5 tok/s, a paragraph takes
about a minute. (3) **Translation quality of the 12B model is mediocre**: grammatical slips and
non-words ("Uwarunkom", "kościojachach", "przedejejsze"), one sentence translated to nonsense
("Bezczynności odłupania tynku" for "Ohne Abschlagen des Putzes"), "podesty" for Sockel, "tężni" for
Fürstentümern, a caption in broken Polish. Ideas not yet built: a second "Polish editor" pass, a larger
model, a Polish spell-check (hunspell) to flag non-words, the model-comparison mode.

## 7b. Translation through Claude Code (2026-11-17, branch `translate-claude-code`)

The owner decided to set the local models aside for a while: what runs on his machines is too weak for a
quality translation. `translate.py --provider claude-code` runs the Claude Code CLI (`~/.local/bin/claude` on the
Mac, logged in with his Claude subscription; no API key) headless, one call per unit, with the same prompt,
glossary, checks, retries and cache as before. A usage limit stops the run cleanly; re-running continues from the
cache. Details, flags and why not `--bare`: `TRANSLATION.md`, section "Claude Code as the translation engine".
**Not yet run with a real login.** Next: translate the two sample spreads (`--only` one unit first), compare with
the hand translations in `examples/`, then a whole book; Opus vs Sonnet on the same units.

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

1. First real run of `translate.py --provider claude-code` (section 7b); tune prompt, rules and checks on the output. (LM Studio was run once, section 7a; set aside for now.)
2. Glossary content: import the place-name CSV, review `proposed` terms, resolve ambiguous places; (the glossary is already wired into
   `translate.py`).
3. Automatic font choice among supplied fonts (score x-height ratio, advance width, stem darkness
   against the scan; the tracking fit already exists).
4. More samples: two-column pages, ragged right, footnotes, running heads; tune and add tests.
5. OCR post-correction: dictionary stage done (`ocrfix.py`, `OCR_CORRECTION.md`); `ocrreview.py` asks an LLM about each flag (not yet run against a real model: try it on LM Studio / Gemma and tune `prompts/ocr_review_de.txt`); next: tune on more pages, fill `glossary/vocabulary.txt`.
6. Human review: the GUI, stages B-F (section 11, `GUI.md`): word boxes in stage 2, the correction window with marks and a JSON
   editor, edits applied by `ocrfix.py`, running the pipeline from the GUI. Then export (PDF / per-page images).
7. DPI-independent thresholds beyond the line-pitch scale factor.

## 10. Verification habits that worked

- Run `python -m pytest -q` before every commit (207 tests). On a headless Linux machine the GUI tests need a display:
  `Xvfb :99 &` and `DISPLAY=:99 python -m pytest -q` (or `xvfb-run -a python -m pytest -q`).
- After any layout change, render both samples and look at `render/compare.png` (original | rebuilt
  per page). Most bugs here are visual (overlapping captions, grey patches, wrong tracking).
- Check `render/report.json`: line counts against `lines_original`, `warnings`, `tracking_em`.
- Native-resolution output must stay unchanged when only the high-resolution path is modified.

## 11. GUI (stage A done 2026-11-15): start here for GUI work

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
into the file `translate.py` reads: Run pipeline with `--resume` (stage 4 onwards, includes ocrfix). Not yet tried on a real book.

**Next** (`GUI.md` sections 6, 8, 10), suggested order:
1. A "re-run OCR correction" button (runs `ocrfix.py run <document.json> --edits <file>` in the Run window's log) instead of a
   whole `--resume`; stale edits (`MainWindow.stale_edits`) are named in the status line after loading, but there is no way yet to look at or re-attach them.
2. Mark list next to the image, next/previous mark, keyboard shortcuts for the decisions (they exist only as right-click and menu today).
   Decisions do not need a backup of `ocr_review.json` any more: they live in `review/ocr_decisions.json`.
3. In an edited unit, the words that differ from `text_ocrfix` get the `human` class (today only its flags turn green).
4. Page status (todo / reviewed) in `review/page_status.json` and a column in the page list.

**Pitfalls found in this session:**
- **Never touch Tk from a thread, and never let the cyclic GC run in one.** A `Variable`/`PhotoImage` finalised in the loading thread hung the
  test suite (thread dump with `pytest -o faulthandler_timeout=5` showed `Variable.__del__` / `PhotoImage.__del__` in the worker). `MainWindow`
  disables the collector and collects from the main loop; images are `SafePhoto`. See `GUI.md` section 11.
- `ImageTk.PhotoImage(img)` without `master=` binds to tkinter's default root, which may be a destroyed one (`pyimage6 doesn't exist`).
- `booktrans.py` deletes its result folder before a full run, and the single-TIFF path (`process`) *always* does (it has no `--resume`).
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

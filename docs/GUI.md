# GUI: design, decisions and stages

Status: **stage A done** (2026-11-15); **stage B done and the marks part of stage C** (2026-11-17: marks on the page image, hover text, show/hide by class); **running the pipeline from the GUI (stage E) done**; **dark theme and zoom** (2026-11-17). **Stage C (prev/next, JSON editor, save, click a mark) and the edit part of D (`ocrfix.py run --edits`) done 2026-11-17.** Still open: a mark list, accepting a candidate with one key, `human` class for the words that differ in an edited unit, backup of `ocr_review.json` (E), F. Planned stages are below. This document is the contract for the GUI work;
`HANDOFF.md` section 11 says where to start in a new session.

## 1. What the GUI is for

1. Set up a **project** for a book: input folder with the TIFF scans, an output directory, the pipeline parameters;
   check that the environment is intact (Tesseract and its `deu` model, Python modules, dictionaries, glossary).
2. Show the **processed pages** and, for each, the JSON the pipeline made (read-only), with a count of what the OCR
   correction did on that page.
3. For every page an **OCR correction window**: the original page with the detected errors marked in colour, and below it
   the page's text as JSON that a human corrects against the scan and saves.

Not in scope (for now): running the pipeline from the window (command shown instead, stage E), editing layout/blocks
(roles, merging or splitting paragraphs), translation review, rendering preview.

## 2. Decisions

Each decision has a reason and, where it matters, the alternative that was rejected. Entries D1-D3 are the ones that most
deserve the owner's confirmation (they shape everything after them); they are also summarised in `DECISIONS.md`
(ADR-015 ... ADR-018).

**D1. Toolkit: Tkinter (+ Pillow `ImageTk`), not Dear PyGui, not PyQt.**
Reason: the central widget is a *text editor* with programmatic positioning (jump to the JSON of the word that was clicked on
the page, highlight a range, undo, find) and the correction window must be a real, separate OS window. Tk's `Text` widget does
all of it (tags, marks, `see`, undo stack); Dear PyGui's multiline input has no tags, no selection or scroll control and all
its "windows" live inside one viewport. PyQt was rejected earlier for its MacPorts dependency list. Cost: on MacPorts Tk is a
separate package (`sudo port install py3NN-tkinter`, NN = your Python minor version); Pillow must have Tk support. A Tk
canvas has no alpha fill, so translucent marks are composited into the page image with PIL (stage C). The GUI logic that
matters is toolkit-free (`project.py`, `envcheck.py`, `pageindex.py`), so a different toolkit would only replace the thin
`gui*.py` layer.

**D2. The editable unit is the translation *unit* (a paragraph), not the OCR line.**
`ocrfix.py` and `translate.py` work on units (dehyphenated, possibly joined across a page break); that text is what ends
up translated. The line texts in `document.json` are raw OCR evidence (ADR-002) and are never edited. So the JSON a human edits
is a *review view* of a page (section 5): its units with `text_raw` (read-only) and `text` (editable). A unit that
continues on the next page appears, whole, on both pages; one stored text per unit means two windows can never disagree.

**D3. Errors are marked by word, on the page image; the mark classes are four colours.**
`auto` (blue: corrected by `ocrfix.py`), `uncertain` (red: flag with severity high or medium: no or weak candidate),
`watch` (amber: flag with severity low: probably a name or a rare word, or a suspect known word) and `human` (green: decided
by a human). Three "error" colours plus the human state; the owner asked for two or three.

**D4. Human work lives outside the pipeline's output folder.**
`booktrans.py` deletes its result folder before a full (non `--resume`) run. Therefore a project is a folder with
`project.json`, `pipeline/<book>/` (what the pipeline writes; disposable) and `review/` (what a human decided; never touched
by the pipeline). Edits of unit texts go to `review/unit_edits.json` (section 6). Existing hazard, not new: the human
decisions in `pipeline/<book>/ocr_review.json` die with a full re-run; the GUI warns before such a run (stage A) and will back
the file up (stage E).

**D5. The GUI is a viewer/editor over files; it never imports pipeline stages and never changes pipeline output.**
Pipeline runs are subprocesses (`booktrans.py`, `ocrfix.py`), so the GUI starts without OpenCV or Tesseract (you can review a
book on a machine that cannot run the pipeline) and a crash in a stage cannot take the window down. Stage A writes only
`project.json`.

**D6. Offsets are traced back to lines by replaying stage 4, not by fuzzy matching.**
Unit text is a deterministic function of line texts (`stage4.dehyphenate` / `join_pair`). `pageindex.replay` rebuilds it with
the same functions while recording the span of every line, then checks the result against the stored text; a unit that does
not reproduce is flagged `unaligned` and shown without positions instead of with wrong ones. See section 7.

**D7. Word boxes: store them in stage 2 (stage B); fall back to estimates for existing data.**
Stage 2 already receives a box for every word from Tesseract and throws it away (only `word_count` is kept). Storing
`words: [{text, bbox, conf}]` per line is a ~10-line change and gives exact marks. Books processed before that change get
estimated boxes (pixel gaps in the line, then proportional character widths); the marks are then approximate and the GUI says so.

**D8. Plain files, no database, atomic writes.**
Every file the GUI writes is JSON, UTF-8, written to a temp file and renamed (`project.write_json`), so a crash or a full disk
never leaves a half-written project or review file.

**D9. Background work never touches Tk.**
Loading a big book, the environment check and (later) pipeline runs run in threads and report through a queue polled with
`after()` (`gui_widgets.run_async`).

**D10. Scripts, not a package; no virtual environment.**
Like the rest of the project: flat modules in `src/booktrans/`, started with `python gui.py` from that folder; modules installed
by hand (`requirements.txt`). GUI modules are prefixed `gui_`.

**D11. Colours and fonts are set explicitly** for the text areas (light editor colours, system fixed font), so a system dark mode
cannot recolour half of a widget; mark colours are in `gui_widgets.CLASS_COLORS`.

**D12. Windows are per page and reused.** The main window keeps at most one correction window per page; selecting the same page
again raises it; closing the project closes them.

## 3. Project folder and who writes what

```
<output directory>/<project name>/
    project.json                      GUI: name, input folder, pipeline parameters           (written by the GUI)
    pipeline/<book>/                  booktrans.py -o <project>/pipeline --name <book>      (written by the pipeline; disposable)
        document.json  document.corrected.json  ocr_corrections.json  ocr_review.json  pages/  figures/  stages/ ...
    review/                           human decisions                                          (written by the GUI, stage C onwards)
        unit_edits.json               edited unit texts (section 6)
        page_status.json              per page: todo / reviewed                                (stage C)
        backup/                       copies of ocr_review.json before a destructive re-run    (stage E)
```

`<book>` is the project name, except when the input folder holds a single TIFF: `booktrans.py` then names the folder after the
file (`Project.book_dir` knows). The GUI shows `document.corrected.json` when it exists (it carries `text_raw` and
`ocr_corrections`), else `document.json`; `ocr_review.json` supplies the open flags.

`project.json`:

```json
{"kind": "booktrans-project", "schema": "1.0", "name": "silesia", "created": "2026-11-15T14:20:00",
 "input_dir": "/scans/silesia",
 "params": {"lang": "deu", "layout": "spread", "scale": "auto", "ocr_scale": 2.0, "ocrfix": "strict", "ocr_suggest": false}}
```

`Project.command()` turns the parameters into the exact `booktrans.py` command line (shown by Project > Pipeline command).

## 4. Data flow

```
TIFFs -> stages 1-4 -> document.json -> ocrfix.py -> document.corrected.json + ocr_corrections.json + ocr_review.json
                                                         |                              |
                                                         +---------- GUI (reads) -------+---> review/unit_edits.json (stage C)
                                                                                              |
                         document.corrected.json  <---- ocrfix.py run applies the edits (stage D) ---+
                                  |
                              translate.py (unchanged: reads unit `text`)
```

## 5. The three JSON views of a page (`pageindex.DocIndex`)

All three are shown read-only in the main window (stage A); the second is what stage C makes editable.

1. **Page model**: the page exactly as in `document.json` (geometry, layout, blocks with typography and OCR lines).
2. **Review JSON** (`page_review`), the editable one:

```json
{"schema": "1.0", "page": 2, "side": "R", "source_tiff": "demo_0001.tif", "image": "pages/demo_0001_page_02_R.png",
 "units": [
   {"unit_id": "u0004", "role": "paragraph", "blocks": ["s1-p3"], "pages": [1, 2],
    "continues_from_previous_page": true, "continues_on_next_page": false,
    "text_raw": "Das Portal ... (what the OCR read, read-only)",
    "text": "Das Portal ... (the corrected text: edit this)"}]}
```
   Only `text` is meant to be edited. Saving (stage C) validates the JSON, refuses a changed `unit_id`/`role`/`blocks`/`pages`/
   `text_raw`, and stores every changed `text` as an edit. Structural changes (merge/split units, change roles) are out of scope.
3. **Marks** (`page_marks_json`): every automatic correction and open flag on the page with `cls`, `token`, `after`/`candidates`,
   `rule`/`reason`, `severity`, and `where`: `[{block, line, c0, c1}]`, the character range in the raw text of an OCR line.
   All marks have the same keys. A word split by a hyphen at a line end has two entries in `where`; a unit that crosses a page
   break puts the part on page N in page N's list.

## 6. Edits (stages C and D)

`review/unit_edits.json`:

```json
{"schema": "1.0", "units": {"u0004": {"base": "<sha1 of text_raw when edited>", "text": "...", "edited": "2026-11-15T15:02:11"}}}
```

* `apply_edits(doc, edits)` (one function, used by the GUI right after saving and by `ocrfix.py run`) sets `unit.text` and marks
  the unit `ocr_edited`. A re-run of `ocrfix.py` therefore reproduces the edits instead of overwriting them.
* **Staleness.** `base` is a hash of the unit's `text_raw`. After a re-run that changed the OCR, an edit whose hash no longer
  matches is *not* applied; the GUI lists it as stale. If unit numbers shifted, an edit is re-attached to the unit with the same
  hash.
* **Flags of an edited unit are treated as reviewed**: a human read the paragraph. The words that differ from the corrected text
  get the `human` class.

## 7. From a character offset in a unit to a place on a page

`ocrfix.py` records every correction and flag as an offset in the unit's `text_raw` (`ocr_corrections[].offset`, `flags[].offset`).

1. `pageindex.replay(parts)` rebuilds the unit text from `[(block_id, [line texts])]`, mirroring stage 4: lines are stripped and
   joined with a space; a line ending in a hyphen is joined with `join_pair` (`removed_hyphen`: the hyphen is dropped;
   `kept_hyphen_uppercase`; `suspended_compound`: a space is inserted), first inside a block, then across the blocks of the unit.
   It records, per line, the span it occupies in the unit text and how many blanks were stripped from its start.
2. The rebuilt text must equal `text_raw`; otherwise the unit is `aligned = False` (e.g. a pipeline version that changed the joining).
3. `UnitMap.locate(start, end)` gives fragments `(block, line, c0, c1)` in the raw line text. `abbrev_space` (inserting a blank in
   `B.in`) is recorded at the *end* of the abbreviation and handled as the range before the offset.
4. Fragments become pixels: with `words` (stage B) the boxes of the words that cover `c0..c1`; without, an estimate inside the
   line's box (stage B fallback).

Tests: `tests/test_pageindex.py` replays the real `stage4.build_units` for every join type; `tests/test_gui_demo.py` checks on
the demo (real `ocrfix.run` output) that the fragments of every mark spell exactly the token `ocrfix` reported.

## 8. Windows

**Main window** (`gui.py`): menu (File: new/open/recent/close; Project: environment check, pipeline command, reload, show
folder; Page: OCR correction, find); header with project name, input summary, pipeline state and the colour legend; left the page
list (page, side, source TIFF, text blocks, figures, then counts per class; sortable; rows tinted red when a page has uncertain
words, amber when only watch words); right the three JSON views with a search box (Find: highlights all matches, Return = next,
Shift+Return = previous) and *Copy JSON*; double-click or Return on a page opens its correction window.

**New project** (`gui_dialogs.NewProjectDialog`): name (filled from the input folder, never overwritten once you changed it), input
folder with a live TIFF count (warns about an empty folder, notes the single-TIFF naming), output directory with the resulting project
folder, the parameters (language, layout, scale, OCR scale, OCR correction mode, Hunspell suggestions), *Check environment*, *Create*.

**Environment check** (`gui_dialogs.EnvWindow`, also `python envcheck.py [PROJECT]` in a terminal): Python and Tk; the pipeline
modules; the Tesseract program (also looked for outside `PATH`, where a MacPorts install hides from an app started in Finder), the
language models (the fast `deu` model, about 1.5 MB, is detected by size and warned about: HANDOFF section 2 says `tessdata_best` is
needed; the size cannot tell `tessdata_best` from the standard model, and the report says so); the OCR-correction
dictionaries (present, non-empty and plausible: entry counts, a Hunspell header that matches the file, affix rules present),
`glossary/vocabulary.txt` and the glossary CSVs (header has `de` and `pl`); fonts and prompts; for a project its input folder (exists,
TIFF count and size, first TIFF opens, free disk space). Statuses `ok`, `warn`, `fail`, `info`; missing OCR-correction data is only
`info` when the project switched the correction off. No network access.

**OCR correction window** (`gui_correction.py`): target design for stage C, one window per page, top to bottom:
1. *Header*: page, side, source TIFF, counts, legend, previous/next page, "next mark".
2. *Page image* (about two thirds of the height): fit / zoom / pan; marks as translucent boxes in the four colours with a thin outline
   (PIL-composited, since a Tk canvas has no alpha); hovering a mark shows a tooltip (token, rule or reason, candidates), clicking it
   selects it in the list and puts the cursor on the word in the JSON below; an optional layer for low-confidence lines.
3. *Mark list* (narrow, right of the image): the marks of the page in reading order with the class colour; Enter on one jumps to it.
4. *JSON editor* (bottom): the review JSON of the page in a `Text` widget with undo; **Save** (validates, writes the edit, re-marks),
   **Revert**, a status line with JSON errors (line/column). Accepting a candidate of a flag with one key press writes it into the
   text.
Stage A already has the window as a separate, per-page, reused toplevel with the page image fitted to it.

## 9. Modules

| File | Toolkit-free | Role |
|---|---|---|
| `project.py` | yes | project folder, `project.json`, parameter validation, `book_dir`, the `booktrans.py` command, atomic JSON writes |
| `envcheck.py` | yes | the environment check (also a terminal command) |
| `pageindex.py` | yes | `replay`, `UnitMap`, `DocIndex`: page rows, the three JSON views, marks with their lines |
| `gui_demo.py` | yes | synthetic demo project (real stage 4 units, real `ocrfix.run`) |
| `gui.py` | no | main window, menu, page list, JSON tabs, recent projects |
| `gui_dialogs.py` | no | new project, environment window, command dialog |
| `gui_correction.py` | no | the OCR correction window: page image with marks (editor in stage C); `compose_marks` needs only PIL |
| `unit_edits.py` | no | the edit store: hash-tied edits, `apply_edits`, `validate_review` (also used by `ocrfix.py run --edits`) |
| `gui_run.py` | yes | the Run pipeline window (parameters, Start/Stop, live log) |
| `gui_widgets.py` | no | colours, `JsonView` (read-only JSON with colouring and search), `run_async`, helpers |

Tests: `test_project.py` (22), `test_envcheck.py` (13), `test_pageindex.py` (17), `test_gui_demo.py` (6), `test_gui_smoke.py` (12; needs
`tkinter` and a display, skipped otherwise, windows are kept withdrawn).

## 10. Stages

| Stage | Content | State |
|---|---|---|
| **A** | this document; project and environment check; page list with counts; page JSON in three views; correction window as a shell; demo project; tests | **done** |
| **B** | word boxes: stage 2 keeps `words` per line, `docmodel.py` passes them on (document schema 1.1), `pageindex.line_box` maps a mark to word boxes, with the estimate as fallback; the demo gets words | **done** 2026-11-17 |
| **C** | the correction window: image with marks (**done**), prev/next page (**done**), JSON editor with validation and save to `review/unit_edits.json` (**done**), click a mark to find the word (**done**); open: mark list, next/previous mark, accept a candidate with one key, page status (todo / reviewed) | mostly done |
| **D** | `unit_edits.apply_edits` in `ocrfix.py run --edits` (**done**; staleness by hash; `booktrans.py --edits`, passed by the GUI); open: accept-a-candidate writes a `resolution` into `ocr_review.json`, a "re-run OCR correction" button (today: Run pipeline with `--resume`) | partly done |
| **E** | run the pipeline from the GUI (**done** 2026-11-17: `gui_run.RunWindow`, `project.PipelineRun`; parameters edited in the window and saved to `project.json`, live log, Stop, `--resume`, reloads the result when done); open: progress per stage, backup of `ocr_review.json` before a destructive run | partly done |
| **F** | later: translation review, export, anything the first real use shows is missing | |

Acceptance for stage B: on a project with words, every mark of the demo lands on the box of its word; on one without, the GUI
says the positions are estimated. For stage C: an edit made in the window is in `unit_edits.json`, in `document.corrected.json`,
survives `ocrfix.py run`, and the page re-marks itself.

## 11. Risks and open points

- **Threads and the garbage collector (found 2026-11-17).** A tkinter `Variable` or image finalised in a worker thread calls Tk from
  that thread and hangs the program. The main window disables the cyclic collector and collects from a timer in the main loop
  (`gc.disable()`, `_gc_tick`); page images are `gui_widgets.SafePhoto`. Never touch a widget, a Variable or an image from a thread
  (`run_async` does the work in the thread and the result in the main loop). Do not call `gc.enable()` elsewhere.

* **Tk on macOS.** Needs `py3NN-tkinter` from MacPorts and a Pillow with Tk. Tk draws images 1:1 in points (no Retina sharpness):
  fine for ~135 DPI working pages; for full-resolution pages the window will show a downscaled `image_full`. Check with
  `python envcheck.py`.
* **Large books.** `document.json` of a 300-page book is large; loading and indexing run in a thread (status line "Loading..."),
  but the whole index is held in memory. Measure on a real book before optimising.
* **Marks are as good as `ocrfix.py`.** A real word turned into another real word (`Sie` for `Die`) is not marked unless it is a
  `suspect`; the page image next to the JSON is the human's defence against exactly that.
* **Aggressive mode** makes many `auto` marks, some wrong (`Piasten` -> `Plasten`); the `auto` class shows the rule and cost so the
  doubtful ones can be looked at first.
* **Tests of the window** run only where a display exists (an `Xvfb` on a headless Linux machine).

## 12. Run it

```bash
cd src/booktrans
python envcheck.py                  # what is missing?
python gui.py --demo /tmp/demo      # synthetic project: no scans, no Tesseract needed
python gui.py /path/to/project      # a project made by File > New project
python -m pytest -q ../../tests
```

MacPorts: `sudo port install py312-tkinter` (replace 312 by your Python version), then `pip install Pillow` if `ImageTk` is missing.

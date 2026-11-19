# Changelog

## Unreleased — stage 1: stacked page edges, light parts of photographs, hairlines

- Owner's report (scan 0008, page 66): the stacked page edges at the outer side (wider than the fixed 65 px margin) were
  OCR'd into junk blocks and drawn; the photo's light lower part plus its caption became one "line drawing", so the
  caption was lost. `illustrations.book_edge` / `page_ignore_margins` widen the outer margin over the edge band;
  `grow_halftone` grows photographs over their light parts (local paper grey per side); `stage1.thin_line` drops
  hairline "text" candidates.
- Effect on the 14-page test book: page 58's junk caption on the margin (`[REEL LER ENT SER`, a crease) is gone; page 55's
  upper photo includes its light sky and the sky is no longer a junk "caption"; no other region changes.
- Takes effect when stage 1 runs again: run the pipeline without *resume* (or delete the scan's `stages/<name>` folder).
- Tests: 3 new.

## Unreleased — cache misses explained; OCR decisions follow their text

- Owner's report: after adding one TIFF the whole book was translated again. The translation cache key contains the
  prompt and the rules file, so a rules change since the last run (here most likely the titles rule, PR #28) re-translates
  every unit - by design, but silently. Now every cached answer keeps short hashes of its parts, the cache remembers the
  last run's prompt / rules / model, and the run says why a unit was not cached (per unit, a `NOTE` at the start, a summary
  at the end). `cache_explain.py <project>`: for an older cache, which git version of the prompt / rules / glossary the
  cached answers were made with.
- OCR decisions (`review/ocr_decisions.json`) keep a fingerprint of their unit's OCR text; `ocr_decisions.remap` (GUI load,
  `ocrfix.py run --decisions`) moves them to renumbered units and sets aside those whose text changed, instead of
  applying them to whatever unit has the old number.
- Tests: 2 new.

## Unreleased — fix: clicks on the page views

- The real cause of "a click zooms the page and selects nothing" (owner's event log, macOS Tk 8.6.17): the zoom keys
  were bound as `'<%s>' % key`, and `'<1>'` is Tk's short form of **mouse button 1**. That binding replaced the click
  handler of the render window's and the correction window's page canvas: every click zoomed to 100 %, no click selected a
  block, and a drag never started. Now `'<Key-1>'` (and `Key-0`, `Key-plus`, ...). It was on every platform; the GUI
  tests called the click handlers directly and missed it - a new test sends real button events to a mapped window.

## Unreleased — macOS: clicks in the page views

- Owner's report (macOS): a click on the render window's page zoomed it in and never selected a block; no pan either.
  Buttons 4 / 5 were bound as the scroll wheel everywhere; that is right only on X11. Now `gui_widgets.bind_wheel` binds
  them on X11 only (render and correction windows).
- `BOOKTRANS_DEBUG_EVENTS=1`: the page canvases print every mouse event they get (type, button, modifiers, wheel delta,
  position) and the Tk version to the terminal (`gui_widgets.debug_events`).

## Unreleased — render window: block clicks

- Owner's report: clicking a block in the render window selected nothing (not reproduced on Linux with real X clicks).
  A click may now move up to 8 px (was 4: a trackpad click often moves a little and counted as panning); the status line
  says when there is no text block under the click, and an error while selecting is shown there instead of only on the
  terminal.

## Unreleased — correcting the translation by hand

- Render window, Block tab: the block's Polish is editable; *Save text* / *Machine translation*; the German original
  below it; the translation checks warn (punctuation, markup) but do not block; the Book tab counts corrections and
  lists orphaned ones (*Show…*, *Delete them*).
- `text_edits.py`: corrections in `review/text_edits.json`, one whole text per unit, keyed by a fingerprint of the German
  it was translated from (unit numbers change on a pipeline run, the German does not); unit and time only for reading;
  a correction whose German changed is orphaned, not applied and not deleted; one backup per save.
  `translations.json` is never touched, so a new translation cannot overwrite a correction.
- `render.py --text-edits`, passed by the project's render command; `RenderSession.set_text` / `revert_text`.
  *Clear project data* counts the file as review work.
- Tests: 6 new.

## Unreleased — one central glossary for all books

- **Central glossary** (`glossary_store.py`): a folder outside the repository, default `~/BookTrans/glossary`
  (`~/.booktrans/config.json`, `$BOOKTRANS_GLOSSARY`), seeded from the repository's `glossary/` on first use. A project's
  own layer `review/glossary.csv` is read first and wins; *Promote to central*. Rejected words in `rejected.csv`.
  The translation setting *Glossary* blank = project layer + central (was: the repository's files); `translate.py`
  without `--glossary` reads the central glossary.
- **Harvest** (`glossary_harvest.py`, `translate.py --harvest FILE`, `Project.harvest_command`): per translated unit the
  model lists the names and terms and the Polish forms used; known, rejected and invented words are dropped; the rest
  aggregated into `review/glossary_candidates.json` (cached per unit).
- **Import** of `german,polish` name lists into the central `places.csv` (ambiguous names marked, existing entries kept).
- **GUI**: Project > *Glossary…* (`gui_glossary.py`): entries of both layers (search, add, edit, delete, promote, import,
  central folder), candidates (accept into central or this book, edit and accept, reject, collect); *Run translation*
  has "collect glossary candidates".
- Seed fix: `Barzdorf` = **Bernartice** (Bernartice u Javorníka), not Bartultovice (= Batzdorf).
- Tests never touch the real glossary or configuration (`conftest.py`). GUI tests can run in the sandbox with
  `/usr/bin/python3.12` under `xvfb-run` (HANDOFF section 10). Tests: 9 new.

## Unreleased — docs: searchable vector PDF planned

- `RENDERING.md` → *TODO: searchable vector PDF*: the agreed approach (real text drawn from the layout plan with embedded
  fonts via reportlab, images for figures / background / kept text), the rejected alternative (invisible text layer over
  image pages), and what to check. Not started; `HANDOFF.md` roadmap points to it.

## Unreleased — render: page position (original / centered)

- `render.py --page-position original|centered`, project `render.page_position` (default `original`), *Page position* in the
  render window's Book tab. `centered`: one page size for the book (the medians), the content centred horizontally by its
  text column and level at the book's median top, for a PDF edition; `original` keeps the scan's left / right page
  margins, for print (`render.page_frame`). The GUI preview, outlines and clicks follow the shift
  (`RenderSession.shift`).
- Tests: 2 new.

## Unreleased — render: caption leading

- Owner's finding: page 52's longest caption touched the paragraph below (4 lines at the original 14.5 px pitch, the
  frame held 3). A caption set smaller than its original glyphs now closes its line pitch in proportion and counts the
  free space at that pitch; its lines go on an even grid. Page 52: all captions at 10.8 px with one leading, the long
  one fits in 4 lines without overflow, clear of the text.
- `layout.caption_geometry` also returns the free space in px.

## Unreleased — translation: titles of cited works

- Rule in `prompts/translate_rules_pl.txt`: titles of cited books, articles, journals stay in their original language
  and spelling, in italics (`<i>…</i>`, which the renderer sets in the italic font), the reference in Polish citation
  form ("Zob. Richard Hamann, <i>Deutsche und französische Kunst im Mittelalter</i>, t. II, 1923."). Claude already kept
  that title untranslated on its own; the rule makes it dependable.
- `translate.tag_problems`: only balanced `<i>` is allowed in an answer; another tag or Markdown emphasis makes a retry.
- The rules file changed: the next translation run sends every unit again.
- Tests: 1 new.

## Unreleased — translation: punctuation rules

- Owner's finding: "... z owych czasów, - chodzi tu w istocie o kościoły -, zostało ..." (the old German print's `, -`
  around an inserted phrase carried over). `prompts/translate_rules_pl.txt`: no two punctuation marks together, an
  inserted phrase set off by dashes or by commas (never both), the dash is the Polish short dash `–` with spaces (no `—`,
  no spaced hyphen), the hyphen only inside words. `translate.punctuation_problems` checks every answer; a hit makes a
  retry naming the rule, what is left is flagged.
- The rules file is part of the cache key: the next translation run sends every unit again.
- Tests: 1 new.

## Unreleased — GUI: app icon

- `assets/booktrans_icon.png` (512 px, drawn by `assets/make_icon.py`: an open book, grey type on the left page, red on
  the right, an arrow turning the page): the icon of every window and, on macOS, the Dock icon (`gui.set_app_icon`,
  `iconphoto`). With PyObjC installed (optional) the macOS menu bar says *BookTrans* instead of *Python* (`gui._mac_app_name`).

## Unreleased — captions

Captions were the weakest part of the rendered book (owner's review of the 14-page test book). Changes, in the order they
were made:

- **Renderer.** One caption size for the book (`layout.caption_bases`: the median fitted size of all captions near the
  book's median glyph height) and per page one size for all its captions (the smallest any of them needs); a caption takes room from
  the free space around it and follows its figure (`layout.caption_geometry`: flush towards a picture it stands beside,
  centred / flush under one, first within the picture's width); captions and headings are never hyphenated
  (`typeset(..., hyphenate=False)`); one line kept when 90 % of the size allows it. Line drawings are composited
  ink-only (no paper rectangles, `render.is_line_drawing`). **Final pages at 300 DPI** (`render.py --dpi`, project
  `render.output_dpi`, *Output DPI* in the Book tab): text was drawn at the working scale (~120-135 DPI) and looked pixelated.
- **Pipeline (stages 2 and docmodel; takes effect when stages 1-2 run again).** The figure mask stops at a caption touching
  the picture; caption pieces on one row under one figure are joined when the gaps hold ink; every caption is read again
  from its own enlarged strip (`--psm 7`, kept when better); junk pieces are dropped (`captions.py`). Body-size mixed-case
  text beside a picture is no longer a caption (`stage2.caption_sized`; page 59's body was one 24-line caption).
  Letter-spaced capitals are no longer taken for a rule (`marks.is_rule`; page 62's `ZIEGENHALS` and page 63's caption were
  "rules"). Figure crops stop at a caption (no old caption pasted back).
- **OCR correction window: Captions…** — the caption row enlarged, *Read again*, *Join with next*, *Apply* into the review
  JSON (`caption_tools.py`, `gui_correction.CaptionDialog`). An emptied unit is skipped by `translate.py` and neither drawn
  nor kept by the renderer.
- On the test book: page 55 `Br U, MAR AL DER MKCHE` -> `45 RAUDEN OS, WESTPORTAL DER KIRCHE` (printed 43), page 62
  `OS, HAUPTPORTAL ...` + a rule -> `49 ZIEGENHALS OS, HAUPTPORTAL DER KATHOLISCHEN KIRCHE` without the old caption showing,
  page 52 captions one size, drawings without paper patches, sharp caption text. Page 59's caption (beside its picture, one
  full line in German) needs two lines or 90 % size in Polish: it now stays one line at the shared size when possible.
- Tests: 17 new.

## Unreleased — GUI: clear project data; input folder of an open project

- **Input folder** shown in the header (path, TIFF count and size, or "folder not found") and at the top of *Run pipeline*; **changeable** with Project > *Change input folder…* or *Change…* in the run window. Shows what the new folder holds and warns when the project has a result from the old one (run without `--resume`, which the run window then unticks, or clear the project data). `Project.set_input_dir`, `Project.input_change_warnings`.

- **Project > Clear project data…** (`gui_dialogs.ClearDataDialog`): removes the results of processing for a fresh start, the settings in `project.json` stay. Ticked by default: pipeline output, translation, rendered pages; optional: the translation cache and the review work (unit edits, OCR decisions, render adjustments; asks again before deleting them). Shows sizes; refuses while a run is going; closes the correction and render windows first.
- `Project.data_parts()` / `Project.clear_data(keys)`: only paths inside the project folder, a symbolic link loses the link and not its target, `project.json` never.
- Fix: the Project menu's enable/disable loop now skips separators.
- Tests: 4 new (278 passed).

## Unreleased — stage 2: stray marks no longer cut paragraphs

- **Cause of the split on page 62** ("Bei der im Jahre 1900" | "erfolgten Restauration ..."): a margin mark read as a line `)` at x 800, sorted between the two lines, passed the *first-line indent* test (it starts 570 px right of the column edge) and started a new paragraph; the next line, starting in lower case, then stayed with the `)`. Same at `Ef` ("erneuert | worden") and `5` ("völlig | neu eingebaut").
- `stage2.mark_paragraph_breaks`: stray marks (narrower than 12 % of the widest line, at most 2 letters or digits) take no part in the break tests and the column's edges and pitch are measured without them; among the text lines they stay in their paragraph (`stray_mark`), apart from it (a page number below the text) they are a region of their own (`isolated_mark`).
- Takes effect when stages 1-2 run again (`booktrans.py` without `--resume`). Without that, stage 4 (since the previous change) joins the split parts into one unit, so translation and rendering are right, but the blocks in `document.json` stay split.
- Tests: 2 new.

## Unreleased — GUI: the render window

- **Render window** (`gui_render.py`; header button *Render pages…*, Project menu, `Cmd/Ctrl+Shift+P`): page list with each page's fit and check counts; the rebuilt page beside the scan (or either alone), zoom with sharp text (drawn at the zoom on screen), drag, click a text block to select it; outlines for the selection, collisions / overflow, adjusted blocks, optionally all blocks. Tabs: **Book** (font, bold, italic, letter-spacing, size, word space, paragraph indent, page fit, background, ink, final resolution; fonts in use, ink match, base size), **Page** (the page's fit, size factor / letter-spacing change by hand, the page's checks), **Block** (how the block is set, size / letter-spacing / word space / max width / alignment for frames, keep original, the Polish text). Every change is saved at once (`project.json` -> `render`, `review/render_overrides.json`) and the book laid out again in a worker thread. Reloads after a pipeline or translation run.
- **Render book…** (`gui_run.RenderRunWindow`): pages, resolution, PDF -> `<project>/render/`; *Output folder*.
- `render_session.py` (toolkit-free): load, lay out, draw a page at any scale, block under a point, adjustments, settings.
- `render.draw_page(..., scale=)`: draw a page at any scale. `render.py --pages` with no matching page is an error (pages are the document's numbers). `Project.render_command(..., pdf=True)`.
- `gui.py --demo` writes a synthetic translation (`gui_demo.make_demo(translate=True)`).
- `ADR-022`: the preview runs in the GUI process, the full render is a subprocess.
- Tests: 11 new (`test_render_session.py`, `test_gui_render.py`; 272 passed).

## Unreleased — stage 4: repair of stage 2 region errors (page 62)

- **Noise regions** (`|`, `N`, page and figure numbers, strips at the page edge) are excluded from translation (`exclude_reason: noise`).
- **Stray-mark lines** (`)`, `Ef`, `5` from margin marks) are left out of the unit text; `pageindex.replay` skips the same lines (word marks stay exact).
- **Paragraphs split on a page are joined**: consecutive body paragraphs of one column, when the first does not end a sentence and the second starts with a small letter or the first's last line is full. Across pages the small-letter rule stays (scans may skip pages).
- A **caption of 4+ lines at body size** counts as a body paragraph for linking.
- On the 14-page test book: page 62's paragraph translated in four fragments is one unit again, the page 62 -> 63 link goes to the right paragraph (the translation no longer repeats "Podczas gdy"), 74 -> 56 units. Rerun: `booktrans.py --resume` (keeps stages 1-3), then translate the changed units.
- Tests: 5 new (261 passed).

## Unreleased — render: fixes from the first real book

- Rendered the owner's 14-page test book (Claude translation). Fixes:
  - **Scan noise read as text** (`|`, `N`, page or figure numbers as paragraphs) is never typeset: dropped outside the text column, kept as pixels inside; a unit joined to such a block starts at its real text.
  - **Stray marks read as lines** (`)`, `Ef`, `5`) get no slot; the line pitch is measured from the real lines.
  - **Overlapping blocks** give one slot per line; each run of slots is put on **one even line grid**.
  - A **caption / footnote of 4+ lines at body size** is set as body text (`reclassified`); paragraph blocks narrower than 30 % of the column are fixed frames.
  - **Ink**: `typecolour.match_ink` matches the scan's darkness (hue from the median ink colour, shade solved from a coverage mask); the "darkest pixels" rule gave near-black because the scanner clips stroke cores. `fontcheck.py` now sets candidates in black (darkness >= 1 needed).
  - **Speed**: the line breaker's memo ignores the line number for uniform widths (a 21-line ragged frame took 20 s); the 14-page book renders in 8 s.
- `typecolour.py`: darkness, scan metrics, block setting, ink matching (shared by `render.py` and `fontcheck.py`).
- Tests: 5 new (256 passed).

## Unreleased — render: configurable default font

- **Default font in `config/fonts.json`** (Crimson Text Regular / SemiBold / Italic, as before); `BOOKTRANS_FONTS` points to another file. Used by `render.py`, `booktrans.py`, projects without `render.font`, and as `fontcheck.py`'s reference. `envcheck.py` checks the configured default instead of a fixed list of Crimson Text files.
- Tests: 1 new (251 passed).

## Unreleased — render: PDF, emphasis runs, memory

- **`render.py --pdf [NAME]`** (`render(pdf=, pdf_dpi=)`): the drawn pages as one PDF, written page by page (JPEG), page size from the pixels at 120 DPI x the drawing scale (the book's physical size for the 600 DPI scans) or `--pdf-dpi`.
- **Emphasis inside a paragraph**: translations may use `<i>`, `<b>`, `<sp>` (letter-spaced Sperrsatz, +0.16 em), also inside a word; measured per run, carried across pages with their tags, drawn in the run's font; headings in capitals keep the tags. Plan words in another style carry `{style, track, join}`. Not yet produced by stage 2 / the translation prompt.
- `render()` no longer holds every original and rebuilt page in memory (a whole book at full resolution would not fit): per-page comparisons are written as each page is drawn, `compare.png` from small copies.
- Tests: 4 new (250 passed).

## Unreleased — render: ink and fonts; fontcheck.py

- **`--ink auto`** (default; project `render.ink`): the text colour is measured from the scan (darkest tenth of the ink pixels in the text blocks), stored in `report.json` and `layout.json`.
- **Font families**: without `--bold-font` / `--italic-font` the siblings of `--font` are used, else the regular font (before, any `--font` was mixed with Crimson Text's bold and italic). **Variable fonts**: `path@weight`.
- **Crimson Pro** bundled (Light, LightItalic, Regular, Italic, SemiBold; static instances, SIL OFL, `fonts/OFL-CrimsonPro.txt`).
- **`fontcheck.py`**: specimen sheets (scan vs each candidate setting the original text in the original line boxes) and per-font width / x-height / darkness / tracking with a score, to choose the font on the real scans.
- Font ratios include the x-height and are cached; the font cache is bounded.
- Tests: 2 new (246 passed).

## Unreleased — render: settings and human adjustments

- **Render settings in `project.json`** (schema 1.2; older files load with the defaults): `render` = background, resolution, fonts, tracking, size scale, word space, paragraph indent, page fit, ink; `project.check_render`; `Project.render_command(pages, resolution)` → `<project>/render/` (outside `pipeline/`), with `review/render_overrides.json` when it exists.
- **Adjustments** (`render_overrides.py`, `render.py --overrides FILE`): per block `size_scale`, `tracking_em`, `word_space`, `anchor`, `max_width`, `keep_original`; per page `size_factor`, `track_delta_em` (replace the automatic body fit). Tied to a hash of the OCR text: stale entries are not applied and are named. `report.json`: `adjusted` per fixed frame, `kept_by_hand`.
- Tests: 6 new (244 passed).

## Unreleased — render: checks

- **`report.json` checks** (`layout.check_plan`): `collision` (a line over a figure, graphic, kept text, another block's lines, or off the page; also a warning), `overflow`, `drift` (paragraph on a later page than in the original), `blank` (empty slots at a page foot), `loose` / `tight` justified lines (word space > 1.6x / < 0.8x natural); `check_summary` per page. The CLI prints the totals.
- Plan lines carry `stretch` (justified word space / natural).
- Tests: 3 new (238 passed).

## Unreleased — render: layout plan, line slots, page-synchronised flow

- **Rendering in two steps** (ADR-021): `layout.py` computes a **layout plan** of the whole book (every line: unit, block, style, size, letter-spacing, x, baseline, words with widths, justification gap; per page the kept and wiped blocks and the body fit), written as `layout.json`; `render.py` draws a page from it (`draw_page(doc, plan, page_index)`). `typeset.py` holds the line breaking (moved out of `render.py`, which re-exports the old names). `render()` keeps its signature.
- `render.py --pages 3,5-7`: draw only these pages (the layout still covers the book). `--no-page-fit`: one body size for the book (shrink only).
- **Body text through line slots**: every original body line is a slot (regularised top, column edges, narrowed by a figure beside it); the Polish lines go into the slots in column order. Fixes: a figure between two paragraphs was overprinted by the second paragraph; a text label inside a figure (a text block without a unit) moved the top of the body; two-column pages and text beside a picture now work; an untranslated body paragraph's slots were reused by the flow and overprinted.
- **Flow kept in step with the pages**: a paragraph never starts before its original page; size and letter-spacing are fitted per page (cost of delay, empty slots, orphan/widow, carried lines, change from the previous page; one page of lookahead). Orphans and widows are avoided. Warnings: paragraphs pushed to a later page, empty slots at a page foot, overflow after the last page.
- Text blocks without a unit outside figures keep their original pixels (on a paper background they vanished).
- Paper colour estimated from the page when `layout.paper_color_rgb` is missing.
- Speed: piece widths are measured once per text at a reference size and scaled; a 60-page synthetic book lays out in 3.5 s.
- `report.json`: body units have `original_page`; pages have `blank`, `size_px`, `tracking_em`, `delayed`.
- Tests: 12 new in `test_layout.py` (235 passed).
- Docs: `RENDERING.md` rewritten; `DECISIONS.md` ADR-021; `HANDOFF.md`.

## Unreleased — translate: --force

- **`translate.py --force`**: ignores cached answers for the selected units (`--only` / `--pages`; otherwise the whole book) and asks the model again; the new answer replaces the cached one. For a translation you do not like: without it a repeated run returns the same cached answer.
- GUI, *Run translation*: per-run checkbox *re-translate* (`--force`); with no pages given it asks first, since the whole book would be sent again. `Project.translate_command(..., force=True)`.
- Tests: 223 passed (was 221).
- Docs: `HANDOFF.md` brought up to date (status, pipeline diagram, repository map, translation section, roadmap, GUI section in date order); `DECISIONS.md` ADR-019 (Claude Code as translation engine) and ADR-020 (translation output in the project, outside `pipeline/`).

## Unreleased — gui: translation settings and Run translation

- **Run translation…** (button next to *Re-run OCR correction…*, Project menu, `Cmd/Ctrl+Shift+T`; `gui_run.TranslateWindow`): runs `translate.py` on the book with the project's translation settings -> `<project>/translation/translations.json` (outside `pipeline/`, so a full pipeline run cannot delete it; the answer cache is `translations.cache.json` beside it). Per run, not saved: pages (`3`, `1,4-6`, blank = whole book) and dry run. Live log, Stop; the header then shows "translation: N of M units, K flagged".
- **Translation settings in `project.json`** (schema 1.1; a 1.0 file loads with the defaults): `provider` (`claude-code` default, `openai`, `anthropic`), `model` (blank = the engine's default), `endpoint` (openai/anthropic), `glossary` (CSV files separated by `;`, relative to the project; blank = `glossary/*.csv`), `source` (`corrected` / `raw`), `timeout`, `retries`, `no_think` (openai). Validated (`project.check_translation`); `Project.translate_command()` builds the command.
- The window warns, and asks before starting, when the translation would not see your work: no `document.corrected.json`, or `review/unit_edits.json` / `ocr_decisions.json` newer than it (`Project.translation_warnings`). Unsaved correction windows are asked about as for the other runs. One run window per project: pipeline, OCR correction and translation never run together.
- `translate.py --pages 1,3-5`: translate the units on these pages.
- Fix: a partial `translate.py` run (`--only`, `--pages`) rewrote `translations.json` with only the selected units; it now keeps the other units (document order). And it took the paragraph context only from the selected units, so the first one got a different prompt and cache key than in a full run; the context now comes from the whole book.
- Fix: `translate.py` failed when the output folder did not exist; it is created.
- `gui_run.py` refactored: `ProcessWindow` (Start/Stop, log) under `RunWindow` and `TranslateWindow`.
- Tests: 221 passed (was 212).

## Unreleased — gui: re-run OCR correction; --resume for a single TIFF

- **Re-run OCR correction…** (button next to *Run pipeline…*, Project menu, `Cmd/Ctrl+Shift+O`): runs only stage 5, `ocrfix.py run <book>/document.json` with the project's OCR-correction mode and `--edits review/unit_edits.json --decisions review/ocr_decisions.json` (`project.ocrfix_command`), in the run window (mode `ocrfix`: live log, Stop, the page list reloads at the end). Saved edits and decisions reach `document.corrected.json`, the file `translate.py` reads, in seconds instead of a stage-4-onwards pipeline run. Errors are named (no `document.json` yet, OCR correction switched off).
- Both run modes ask before starting when a correction window has unsaved changes (they are not in `review/` yet, so the run would not see them). Only one run window per project: a running pipeline or correction is never replaced, an idle window of the other mode is.
- Fix: **`booktrans.py --resume` with a single TIFF** (a project whose input folder holds one TIFF) was ignored: the single-file path deleted the result folder and redid stages 1-4, and the GUI showed no warning because `--resume` was ticked. `process()` now keeps stages 1-3 when `stages/s3` is complete and redoes stage 4, `document.json` and stage 5, as for a book.
- Tests: 5 new (212 with the OCR dictionaries fetched).

## Unreleased — translation through Claude Code

- **`translate.py --provider claude-code`**: translates through the Claude Code CLI with your Claude subscription (no API key): one headless call per unit (`claude -p --output-format json --model opus --system-prompt … --tools "" --strict-mcp-config --no-session-persistence`, prompt on stdin, in an empty temporary folder). Same prompt, glossary, checks, retries and cache as the other providers. `--model` takes an alias (`opus`, the default, or `sonnet`) or a full id; `--claude-bin` (default `$CLAUDE_BIN`, `claude` on PATH, `~/.local/bin/claude`); `--claude-args`; `ANTHROPIC_API_KEY` is removed from the CLI's environment so the subscription is used (`--claude-use-api-key` keeps it).
- Errors: a missing login stops at once with a hint to `/login` (the CLI calls it a "temporary network issue", so it is recognised before the retry rule); overload / 5xx is retried; a **usage limit stops the run**, keeps `translations.json` and the cache, and the same command continues later. Token counts are logged and totalled; the CLI's `total_cost_usd` is reported as a notional `reported_cost`.
- `envcheck.py`: new group *Translation* shows the Claude Code CLI and its version (only `info` when it is missing).
- Docs: `TRANSLATION.md` (new section), `HANDOFF.md` section 7b. Tests: 207 passed (was 201).

## Unreleased — fix: deskew on pages with too few text lines

- `deskew.estimate_geometry` returned early without `edge_model` when a page had fewer than `MIN_LINES` usable lines, so `stage1.py` failed with `KeyError: 'edge_model'` while logging (seen on a sparse right-hand page). The key is now always present (`'none'` for an untouched page); test extended.

## Unreleased — gui: approve or replace flagged words

- **Decide about a flagged word in the correction window**: right-click (Ctrl+click on a Mac) a red or amber mark: *Accept as it is*, *Replace with* one of its candidates, *Accept „word" everywhere*; a decided flag offers *Undo my decision*. The **Accept as is** menu above the page accepts all watch words, or all uncertain and watch words (after a confirmation), of the page at once. The mark turns green at once and the page counts follow; the hover text says what was decided.
- Decisions are stored in `review/ocr_decisions.json` (new `ocr_decisions.py`, toolkit-free), outside the folder `booktrans.py` deletes. `ocrfix.py run --decisions FILE` merges them with `ocr_review.json` (the decisions win; `"="` accepts a word as it is), `booktrans.py --decisions FILE` passes it on and the GUI's Run pipeline always does, so the next run with `--resume` puts them into `document.corrected.json`; until then the GUI shows them as decided only. A flag key is `unit|token|occurrence`; unit ids are stable unless stages 1-4 are re-run on different input.
- Marks JSON: new key `decision` (`null`, `accepted`, `replaced:<word>`, `word accepted`).
- Tests: 201 passed (was 192).

## Unreleased — gui: search in the editor

- **Find** box above the editor text of the OCR correction window: every match (case-insensitive) is highlighted, `k of n` is shown, Enter / Shift+Enter (or the arrow buttons) go to the next / previous match and wrap around, `Cmd/Ctrl+F` focuses the box, Esc returns to the text. Matches are collected again on every step, so it keeps working while the text is being edited, and after Prev/Next page or Save refilled the editor.
- Tests: 192 passed (was 190).

## Unreleased — gui: page navigation, text editor (stage C), edits applied by ocrfix (stage D)

- **Prev / Next** in the OCR correction window (buttons, PageUp / PageDown): the window moves to the neighbouring page, the page list follows; a page that already has its own window is brought to the front instead.
- **Editor** below the page image: the page's review JSON (units with `text_raw`, read-only, and `text`), with undo. *Save* (`Cmd/Ctrl+S`) checks the JSON (only a unit's `text` may change; errors are named), stores the changed texts in `review/unit_edits.json` and re-marks the page: the open flags of an edited unit turn green (reviewed). *Revert* drops unsaved changes. Unsaved changes are asked about before leaving a page, closing the window, switching or closing the project, or quitting. **Clicking a mark** puts the cursor on its word in the text.
- **`unit_edits.py`** (new, toolkit-free): the edit store (an edit is tied to the sha1 of the unit's `text_raw`, survives renumbered unit ids, is *stale* and not applied when the OCR text changed), `apply_edits`, `revert`, `validate_review`. Edited units carry `ocr_edited` and `text_ocrfix` (the pipeline's text) in the corrected document.
- **`ocrfix.py run --edits FILE`** applies the saved edits after the automatic correction; `booktrans.py --edits FILE` passes it on and the GUI's Run pipeline always does, so a re-run (`--resume` redoes stage 4 onwards) reproduces the human texts in `document.corrected.json`, which `translate.py` reads.
- Fix: `booktrans.py` accepted `--aggressive-ocr-fix` but never passed it to `ocrfix.py`; it does now.
- Fix: a GUI hang. Python could finalise a tkinter variable or image (of a closed window) in the loading thread, and a Tk call from another thread blocks. The cyclic garbage collector now runs only in the main loop (`gc.disable()` + a timer), and page images are `SafePhoto`s that are released by the main thread.
- Tests: 190 passed (was 167).

## Unreleased — gui: dark theme, zoom

- **Dark theme everywhere** (`gui_widgets.apply_dark_theme`): one palette (`BG`, `PANEL`, `FIELD`, `FG`, `MUTED`, ...), the `clam` theme on every platform (the native macOS theme ignores most colours), Tk widgets and drop-down lists through the option database. Row tints of the page list, status colours and the JSON colours are dark-mode variants; a test checks the contrast of every text/background pair and that no other module hard-codes a colour. The faint rows in the page list are readable now.
- **Zoom in the correction window**: − / + / Fit / 100% buttons, Ctrl (Cmd) + wheel, keys `+` `-` `0` `1`, scroll bars, drag to move; the marks scale with the page, hover still works. Limits 10%-600%.
- The Run pipeline button moved into the header; the buttons of the page bar no longer get cut off in a narrow window.

## Unreleased — gui: marks on the page, run the pipeline from the window

- **Marks on the page image** (`gui_correction.py`): every automatic correction and flag of the page is drawn on the page image in the colour of its class (blue auto, red uncertain, amber watch, green human), translucent for exact positions and outline-only for estimated ones; hovering names the mark (token, correction and rule, or reason and candidates); a check box per class shows/hides it. The page image is the deskewed image the pipeline made from the TIFF; the TIFF is not changed.
- **Word boxes** (document schema 1.1): `stage2.py` keeps `words` (`text`, `bbox`, `conf`) for every OCR line, `docmodel.py` passes them on, `pageindex.line_box` turns a character range of a line into the union of the word boxes it touches (exact) or, for documents without `words`, a proportional estimate. Books processed before need stages 1-2 re-run for exact positions. The marks JSON view now has `bbox` and `exact` per fragment.
- **Run pipeline** (`gui_run.py`, `project.PipelineRun`): Project > Run pipeline… (and a button): parameters are edited in the window and saved to `project.json`, `--resume` check box with the warning and a confirmation before a run that deletes the previous result, live log, Stop (ends the whole process group), the page list reloads when the run ends.
- Fix: the page list was white on white with macOS dark mode; colours of the list are set explicitly.
- Tests: 167 passed (was 151).

## gui (stage A)

### GUI: projects, environment check, page list and page JSON (`gui.py`, design in `docs/GUI.md`)

- New `src/booktrans/gui.py` (+ `gui_dialogs.py`, `gui_correction.py`, `gui_widgets.py`), Tkinter: `python gui.py [PROJECT]`,
  `python gui.py --demo DIR`. File > New project (name, input folder with a live TIFF count, output directory, pipeline parameters,
  environment check), open/recent; the page list (page, side, TIFF, blocks, figures and per-page counts of words corrected
  automatically / uncertain / watch / decided by a human; sortable); three read-only JSON tabs per page (page model, review JSON,
  marks) with search and *Copy JSON*; Project > Pipeline command (the exact `booktrans.py` line, with a warning that a run
  without `--resume` deletes the result folder including `ocr_review.json`); the OCR correction window per page as a shell (page
  image, counts; marks and the JSON editor are stage C). The GUI changes no pipeline output; the only file it writes is `project.json`.
- New `src/booktrans/project.py`: project folder (`project.json`, `pipeline/<book>/`, `review/`: human work lives outside the folder
  `booktrans.py` deletes), parameter validation, `book_dir` (single-TIFF naming), the pipeline command, atomic JSON writes.
- New `src/booktrans/envcheck.py` (also `python envcheck.py [PROJECT]`): Python/Tk, pipeline modules, the Tesseract program (also
  outside `PATH`) and its language models (detects the fast `deu` model by size), the OCR-correction dictionaries (present, non-empty,
  plausible entry counts and headers), vocabulary and glossary files, fonts and prompts, and for a project its input folder (TIFF
  count and size, first TIFF opens, free disk space).
- New `src/booktrans/pageindex.py`: traces every `ocrfix` correction/flag offset back to page, block, line and character range by
  replaying stage 4's joining with position tracking (checked against the stored text; units that do not reproduce are marked
  unaligned), including words split by a hyphen at a line end and units that cross a page break; page rows and the three page JSON
  views.
- New `src/booktrans/gui_demo.py`: a synthetic demo project built with the real `stage4.build_units` and `ocrfix.run`.
- 71 new tests (151 in total): the replay equals `stage4.build_units` for every join type, the fragments of every demo mark spell the
  token `ocrfix` reported, project/environment logic, and window tests (skipped without tkinter or a display).
- Docs: `docs/GUI.md` (decisions, schemas, windows, stages A-F), ADR-015 ... ADR-018 (proposed), `HANDOFF.md` section 11.

## stage2-rework

### Aggressive OCR correction

- `ocrfix.py run --aggressive` / `booktrans.py --aggressive-ocr-fix`: looser decision policy (`POLICIES`): changes
  almost everything that has a candidate (cost up to 1.5, margin 0.1 or 10x frequency, 4-letter words, no name
  guard); flags only words without candidates, weak candidates, dead heats, 3-letter words and suspects. The mode
  is stored in `ocr_correction.mode`. 2 new tests (80 in total).

### OCR correction in the pipeline, LLM-assisted review (`ocrreview.py`)

- `booktrans.py` runs `ocrfix.py` after writing `document.json` (single page and book mode); the files
  (`document.corrected.json`, `ocr_corrections.json`, `ocr_review.json`) are written next to it. Skipped with a
  message when `symspellpy` or the dictionaries are missing. New options `--no-ocrfix`, `--ocr-suggest`.
- New `src/booktrans/ocrreview.py` (stage 5b), a separate script: for every open flag one model request with the
  whole unit text (flag marked `⟦ ⟧`, other flags `‹ ›`), reason, candidates and glossary names
  (`prompts/ocr_review_de.txt`); JSON answer (`replace` / `keep` / `unsure`, replacement, confidence, reason),
  validated, retried once, replacement checked against the dictionaries (unknown word: never `high`); stored as
  `llm` in `ocr_review.json`, cached. Subcommands `propose`, `show`, `accept --min-confidence`. Model options as in
  `translate.py` (LM Studio or `--provider anthropic`). `ocrfix.py run` keeps the proposals.
- `translate.py` uses `document.corrected.json` when it exists next to `document.json` (`--raw` to opt out).
- 78 tests (10 new, fake model; full loop also run against a mock OpenAI-compatible server).

### OCR correction stage (`ocrfix.py`, stage 5, before translation)

- New `src/booktrans/ocrfix.py`: checks the German text of every unit offline and corrects OCR errors
  conservatively. Known words = SymSpell `de-100k` + Hunspell `de_DE_frami` (via `spylls`) +
  `glossary/vocabulary.txt` + glossary names + compounds; old spelling (`daß`, `Thür`, `Cultur`) is accepted.
  Candidates for unknown words: OCR confusions (`Binfassung` -> `Einfassung`, `fi`/`B` -> `ß`, `O8` -> `OS`),
  edit distance, lost spaces (`nichtmehr`, `dieChorportale`, glued `z. B.in`), Hunspell suggestions (flag only).
  A change is applied only when its cost is low and it clearly beats the runner-up; short words and possible
  names are flagged instead. Known-but-suspicious words (`Fin` for `Ein`) are flagged, never changed.
- Output: `document.corrected.json` (units keep `text_raw`, get `text` and `ocr_corrections`; `translate.py`
  reads `text`, so it just works on the corrected file), `ocr_corrections.json` (every change),
  `ocr_review.json` (flags with candidates and context; fill `resolution`, rerun; decisions persist).
- `python ocrfix.py fetch` downloads the dictionaries into `dictionaries/` (git-ignored).
- 16 tests with a synthetic lexicon (`tests/test_ocrfix.py`); `docs/OCR_CORRECTION.md`.

### Documentation

- OCR with `tessdata_best` `deu` documented as the recommended model (HANDOFF section 2): clearly better
  than the fast model on 600 DPI input (heading, capitals, glued words).

### Anthropic API provider and token accounting

- `translate.py --provider anthropic` (Messages API, `$ANTHROPIC_API_KEY`, default model `claude-sonnet-5-5`),
  retries on 429/5xx/529, API error messages shown, per-run token totals (stored as `usage` in
  `translations.json`), optional cost (`--price-in`, `--price-out`), token estimate in `--dry-run`.
  Tested against a mock server only (53 tests).

### Fixes after the first real run (LM Studio, Gemma 4 12B)

- Paragraph detection: a line that starts in lower case after a line that does not end a sentence can
  no longer start a paragraph (a word lost in OCR made the line before it look short, so "geklaert."
  became a unit of its own and the translation was cut mid-sentence). New test.
- `translate.py`: the digit check converts Roman numerals and superscript footnote marks
  ("13." = "XIII", "1" = "¹") before comparing; empty answers are never cached.

### Translation script

- Per-request diagnostics (time, token counts, tok/s, finish reason, hidden reasoning, why an answer was empty).

- `src/booktrans/translate.py`: one request per unit to an OpenAI-compatible endpoint (LM Studio), with
  the glossary block of the matching entries, rules (`prompts/translate_rules_pl.txt`), the kind of text and
  the previous sentence as context; answer cleaning; checks (glossary terms, length ratio, digits) with up to
  two retries; cache (`<output>.cache.json`); `--dry-run`, `--only`, `--model`, `--endpoint`.
  Tested with a mock HTTP server (5 tests, 48 in total); not yet run against a real model.

### Glossary

- `src/booktrans/glossary.py` and `glossary/terms.csv`: German -> Polish place names and terms. Lookup
  before translation (normalised, compound- and OCR-tolerant matching), `prompt_block`, post-translation
  `check` (inflected forms accepted), `import-places` for a `german,polish` CSV (duplicates dropped,
  names with several Polish forms marked `ambiguous`), `match` and `candidates` reports. 6 new tests (43).
  On the sample book `check` flagged a caption translated more briefly than the glossary term.

### Batch mode (books)

- `booktrans.py` takes a folder of TIFFs or several files and writes one book folder: stages 1-3 per
  spread in `stages/<tiff>/`, stage 4 once over all spreads (paragraphs cut at a spread boundary become
  one unit), one `document.json` with consecutive page numbers and unique block ids.
  Options `--name`, `--resume`, `--separate`.
- `docmodel.build_document(book=True)`; `render.py` writes `page_NNN_X.png` and per-page
  `compare_page_NNN.png` for books (`compare.png` up to 4 pages).
- `examples/book_0001-0002.pl.json`: both sample spreads as one book. 2 new tests (37).
- Docs: glossary design (lookup, not RAG) in `docs/TRANSLATION.md`.

### Documentation

- New `docs/HANDOFF.md` (start here), `docs/TRANSLATION.md` (translation service contract), `docs/RENDERING.md`.
  README, DEVELOPMENT, PIPELINE, ARCHITECTURE, DATA_MODEL (translations JSON, render report, scale fields) and
  DECISIONS (ADR-008 to ADR-013) updated.

### Font fitting (render)

- Letter-spacing is fitted per block (`--tracking auto`, default): the translation fills the
  original line widths; capped at 0.12 em, size at most 4 % above the glyph-height size.
  0001: body 49 -> 54 lines (original 56), with 0.08 em tracking.
- New options `--tracking EM`, `--size-scale`, `--word-space`; `booktrans.py` passes
  `--font/--bold-font/--italic-font` and these to the renderer. `--tracking 0` = previous look.
- `report.json` gains `tracking_em` and `lines_original`.
- Fixed frames (captions, headings) no longer widen into a neighbouring block on the same
  rows and no longer overflow their width: tracking, then size, is reduced until every line fits
  (fixes side-by-side captions colliding on 0002).
- `examples/0002.pl.json`: translation of the second sample spread (joins the sentence cut at the end of 0001).

### OCR crops and Stage 1 clean-up

- New `src/booktrans/imgutil.py` with `crop_for_ocr`: OCR crops now include
  10 px of real context and are surrounded by 24 px of paper-coloured padding.
  Bare crops made Tesseract drop or misread line-initial letters.
- Stage 1: the hard-coded 65 px ink-ignore margin is now side-aware (65 px on the
  outer edge, 10 px at the gutter). On the right-hand page the text starts only
  ~60 px from the gutter, so the old margin clipped the first letters of lines
  (`hen`, `var`, `ceit`, `lelt`, `ischen`, `ondern`, and a lost first word).
- Stage 1: removed unused `ocr_lines` / `merge_lines_into_regions` and a leftover
  `y1 if False else y` expression.
- Sample result (`0001.tif`, page 2): 12 of 40 OCR lines changed, the large
  majority of them line-initial fixes.

### Per-page deskew (skew + shear)

- New `src/booktrans/deskew.py`. Each page is corrected on its own right after the
  spread is split, before any analysis. Two numbers are estimated per page: the
  text-baseline slope and the column-edge slope (from full-width justified
  lines), and one small affine map makes baselines horizontal and edges vertical.
  A pure rotation was not enough: on the left page of `0001.tif` the baselines
  slope 0.45 deg while the column edges are already vertical, so rotating alone
  tilted the edges by the same amount. Pages with fewer than 8 full-width lines
  fall back to rotation; pages with fewer than 6 text lines are left untouched.
- Measured on the sample spreads (baseline / edge tilt before correction):
  0001 L 0.45 / 0.00 deg, 0001 R 1.01 / -0.94, 0002 L 0.75 / 0.01, 0002 R 0.94 / -1.30.
  After correction all residuals are < 0.05 deg; right-page column-edge drift fell
  from about -16 and -24 px per 1000 px to under 1 px.
- Stage 1 saves the deskewed page (`page_image`) and records `skew_deg` and a
  `deskew` block (angles, edge model, source-to-destination matrix) per page.
  Stages 2 and 3 now analyse those saved pixels; all JSON coordinates refer to
  them. Debug overlays are drawn on the deskewed spread.
- OCR text accuracy is roughly unchanged by deskew at the current scan resolution
  (some line-initial and word fixes, some new digit confusions such as 3/5); the
  gain is geometric and matters for paragraph detection and reconstruction.
- Tests: `tests/test_deskew.py` (synthetic page with known skew/shear),
  `tests/conftest.py` puts the stage directory on the path; `test_stage3.py`
  imports `stage3` directly.

### Illustration and caption model

- New `src/booktrans/illustrations.py`: illustrations are now detected *before*
  text OCR, from geometry alone. Photographs/halftones use the existing tonal
  contour detector; line drawings are "everything that is not part of a text
  line" (a text line = a row of glyph-sized pieces; leftover ink that forms a
  sizeable cluster is a figure; nearby pieces merge into one figure).
- Stage 1 masks illustration boxes (padded per detector) out of text-region
  detection, so drawing fragments no longer become text regions. Overlapping
  coarse text boxes are merged before OCR.
- Stage 2 OCRs from a copy of the page with illustrations painted over (crop
  context used to pull neighbouring drawing edges in as junk "captions").
- Captions are found by geometry (small text beside or below a figure, plus
  continuation lines), OCR lines are split at wide horizontal gaps (side-by-side
  captions), low-confidence symbol-only tokens are dropped, and captions are
  grouped per column. The old "caption band to the right of the image" hack is gone.
- Body glyph size is estimated from running text only (captions can outnumber
  body lines on a drawing page).
- Stage 2 removes duplicate OCR lines (IoU) and each line belongs to exactly one
  coarse text box.
- Stage 2 `--ocr-scale` (default 2.0) upscales crops before OCR. Measured on lines
  with known text: small captions improve clearly (e.g. `41 BARZDORF` 0.91 -> 1.00,
  caption 37 0.89 -> 0.96 similarity), body text is unchanged; 3-4x is no better.
- Stage 3: the "large region with low OCR confidence" illustration fallback is
  removed (it swallowed the captions under a drawing group). Caption lines are
  `content_context: caption`, `translation_status: translate`, with an
  `associated_illustration_id`; geometry alone never excludes them.
- Sample result: `0002.tif` page 1 went from one detected illustration plus about
  eight junk "text" regions (`ä faf`, `{ LEA l" A`, ...) sent to translation, to six
  illustrations (main plate + five profile drawings), zero junk regions and seven
  separate two-line captions, plus the two plate captions 38/39.
- Tests: `tests/test_illustrations.py` (synthetic pages); `test_stage3.py` updated.

### Paragraph detection (page-level)

- Paragraphs are now built per text *column of the whole page*, not per coarse
  Stage 1 box (box edges cut paragraphs at arbitrary places). The visual
  "horizontal ink band" heuristic and the footnote-marker split are removed.
- Break evidence, first match wins: a line that stops short of the column's right
  edge (threshold max(8 px, 5 x std of full-line shortfall); after deskew full
  lines are within ~3 px and real paragraph ends are >= 19 px short), a
  first-line indent, extra line spacing. A line ending in a hyphen never ends a
  paragraph. Each line records `paragraph_break` and `paragraph_break_reason`.
- Sample result: paragraph counts 3 / 4 / 3 / 4 on the four sample pages (page 51
  has four paragraphs; an earlier note claiming five was wrong), no cuts inside
  hyphenated words. Stage 2 schema_version is now 0.3.0.
- Tests: `tests/test_paragraphs.py`.

### Cross-page links and translation units (Stage 4)

- New `src/booktrans/stage4.py`: reads Stage 3 JSONs in book order and links the
  last body paragraph of a page to the first of the next (also across spreads)
  when the first ends in a hyphen or lacks sentence-final punctuation and the
  second starts lower-case. Sets `continues_from` / `continues_to`.
- Dehyphenation: `Spitz-`+`bogen` -> `Spitzbogen`; upper-case continuation keeps
  the hyphen; `Werk-`+`und Form-` keeps the hyphen with a space. Every hyphen join
  is recorded in `joins`. Output `units.json` (one unit per paragraph chain or caption).
- Sample result: three cross-page chains found, matching the ones identified by
  hand (`Werk- und Form-`/`steinen`, `(Niederschlesien`/`und die tschechoslowakische
  Republik)` across the spread boundary, `Spitz-`/`bogen`).
- Tests: `tests/test_stage4.py`.

### Documentation

- Corrected the DPI claim (effective ~135 DPI), described the new detectors,
  paragraph rule and limitations; data model additions; README lists
  hand-installed modules and MacPorts `tesseract` / `tesseract-deu`.

### Translation of the first spread and page assembly (`render.py`)

- `prompts/translate_pl.txt` (the translation prompt) and `examples/0001.pl.json`:
  Polish translation of the ten units of `0001.tif` (post-1945 place names:
  Byczyna, Głuchołazy, Domaszkowice, Pakosławice, Korfantów, Górny/Dolny Śląsk;
  obvious OCR slips were corrected before translating, listed in the file's notes).
- New `src/booktrans/render.py`: rebuilds pages from `document.json` + translations.
  Clean paper canvas (or `--background scan`: scan with text blocks wiped and filled
  with the local paper tone), figures and page furniture pasted back, text typeset by
  optimum-fit line breaking with justification, Polish hyphenation (optional `pyphen`),
  no line ending in a one-letter word, original line pitch and anchoring.
  Body paragraphs flow through the page columns and over page breaks (a shorter
  translation leaves space at the end instead of gaps inside the page); headings,
  captions, bylines and footnotes keep their own frames; capitals are letter-spaced up
  to the original line width. Font size comes from the original line widths (same
  typographic colour) within 15 % of the size implied by the glyph height; if the
  text needs more lines than the page has, the size shrinks up to 10 %, then overflows
  and a warning is reported. Units without translation keep their original pixels.
- Default font Crimson Text (SIL OFL) bundled in `fonts/`.
- New `src/booktrans/marks.py`: page numbers, printer's marks and horizontal rules
  (including faint hairlines such as the footnote rule) are detected and stored as
  `graphic` blocks with image crops in `document.json`, pasted back by the renderer.
- Wrapper: `--translations FILE` also runs the page assembly.
- Result on `0001.tif`: both pages reproduce the original layout (photo, caption,
  byline, heading, justified body, footnote with rule, page number); the Polish body
  needs 49 lines where the German set 56, so page 2 ends 7 lines early. The paragraph
  cut at the spread boundary (`(Dolny Śląsk`) needs the next spread.
- Tests: `tests/test_render.py`, `tests/test_marks.py`.

### Progress and debug output

- New `src/booktrans/log.py`: timestamped progress lines on stderr (one shared clock
  for all stages). Every stage reports what it is doing: scale estimate, deskew
  angles, illustrations and text regions found, OCR progress per region, line
  counts by type, regions built, classification counts, paragraph links, units.
- Wrapper prints stage headers (`=== stage 2/4 ... ===`) and a final summary;
  `-v` / `--verbose` also lists every detected region and every OCR line with its
  confidence. Stage stdout JSON summaries are shown only when a stage fails.

### High-resolution scans, crops and single pages

- Scale is measured from the text, not the page height: `src/booktrans/scale.py`
  closes ink horizontally into line blobs and takes the dominant vertical distance
  between consecutive lines (the body line pitch; ~23.4 px on the sample scans).
  `f = pitch / 23.3`, scans below 1.25 are used as they are. Measured: samples
  23.4 px (f = 1), the samples upscaled 5x 116.5 px (f = 5.0), a real 600 DPI crop
  113-116 px (f = 4.87). Sizing by page height (my first version) was wrong for
  crops: text came out twice too big and the body text was detected as a drawing.
- A scan with f > 1 is analysed on an area-averaged working copy (all thresholds
  are pixel values tuned at the sample size); the same split and affine deskew are
  applied to the full-resolution page (`page_NN_X_full.png`; `scale` and
  `scale_info` in the JSON). `--scale auto|N` on Stage 1 and the wrapper.
- Stage 2 OCRs crops cut from the full-resolution pixels, resized so `--ocr-scale`
  keeps its meaning (OCR px per working px; default 2). CLAHE is applied only to
  the soft native-size path (it distorted thin small caps on real high-res pixels).
  Boxes and glyph heights stay in working px. Figures are cropped from the
  full-resolution page; `document.json` pages carry `scale` and `image_full`.
- `--layout spread|page|crop` (Stage 1 and wrapper). `page` keeps the 65 px edge
  margins on all sides, `crop` uses 10 px (a crop has no physical book edge), so
  a photo or text cut by the crop edge is still found. The spread default is unchanged.
- Photo mask padding below a photo reduced from 10 to 4 px: a caption sits only
  ~6 working px under a photo at this scale and the old pad painted over the top
  of its capitals. Native sample results are identical (all four pages, same lines
  and illustrations).
- Real 600 DPI crop (photo, caption, two paragraphs; 12 lines): 12/12 lines
  match a manual transcription exactly, 1 figure, 2 paragraphs, caption linked.
  The same crop downscaled to ~135 DPI and run natively also gives 12/12, so on
  clean body text and this caption full-resolution OCR gives no measurable gain;
  the gain is the full-resolution figure crops, and possibly on harder text (digits,
  tiny captions, drawing labels), which this crop does not contain.
- Still open: a synthetic 5x upscale of `0002.tif` splits the main drawing plate
  into several pieces (its gaps sit at the 40 px merge limit); not seen on real
  data yet, needs a real hi-res drawing page.
- Tests: `tests/test_scale.py`; full-resolution figure crop test in `tests/test_docmodel.py`.

### One-command wrapper and nested document model

- New `src/booktrans/booktrans.py`: runs stages 1-4 on a TIFF and writes
  `<out>/<tiff name>/` with `document.json`, `pages/`, `figures/`, `debug/` and
  `stages/` (raw stage JSON).
- New `src/booktrans/docmodel.py` builds `document.json` (schema 1.0): per page
  layout (size, paper colour, text/content area, column edges, default
  alignment), reading-ordered blocks (text: role, OCR text, unit id, continuation
  links, typography = glyph height, relative size, line pitch, alignment,
  first-line indent, last-line shortfall, gap above; lines with boxes and
  confidence; figures: cropped file, detector, caption block ids), plus the
  translation units.
- Not included yet: page numbers/printer marks, curvature correction.
- Tests: `tests/test_docmodel.py`.

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

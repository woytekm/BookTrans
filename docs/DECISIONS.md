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

## ADR-008 — Resolution is handled by a scale factor measured from the body line pitch

**Status:** Accepted

Thresholds are tuned in pixels of the 1800 px sample spreads. Larger scans are analysed on an area-averaged working copy; the factor comes from the median distance between consecutive text-line blobs (reference 23.3 px, factors below 1.25 snap to 1). Full-resolution pixels are used only for OCR crops and figure crops. **Reason:** page-height-based scaling failed on a crop of a page; the line pitch is a property of the text and works for crops, pages and spreads.

## ADR-009 — The page-recreation model is `document.json`, separate from the stage JSON

**Status:** Accepted

`document.json` is nested (pages, blocks, lines), carries layout and typography, and links figures; the stage JSONs stay in `stages/`. Coordinates are working pixels with `scale` and `image_full` recording the mapping. **Reason:** the renderer and a future review tool need one self-contained description of a page.

## ADR-010 — Rebuild pages on a clean canvas by default; the scan background is optional

**Status:** Accepted

`--background paper` draws on the paper colour; `scan` keeps the scan and wipes the translated blocks. Figures are pasted, page furniture is multiplied in. **Reason:** a clean canvas has no ghost text; `scan` keeps the paper texture and gutter shading when wanted.

## ADR-011 — Typeset with an optimum-fit justified line breaker, not the browser or PDF layout engine

**Status:** Accepted

Own Knuth-Plass style breaker over PIL fonts with pyphen hyphenation and Polish typographic rules (no line ends in a one-letter word). **Reason:** exact control of frames, line pitch and flow across pages, and it yields measurable fit data for the report.

## ADR-012 — Font fitting by letter-spacing and size, not by changing the line count

**Status:** Accepted

The translation is fitted to the original line widths by per-block tracking (cap 0.12 em) and a size within 4 % of the glyph-height size. **Reason:** language length is not the issue (Polish is about 3 % shorter); the original typeface is wider than the available fonts. Choosing among several fonts automatically is a later step.

## ADR-013 — The translation service is a separate stage with a JSON contract

**Status:** Accepted; implemented as `translate.py` (LM Studio endpoint)

Input `document.json` units; output a translations JSON in the format of `examples/*.pl.json`. A local LLM behind an OpenAI-compatible endpoint is the intended backend. See `TRANSLATION.md`. **Reason:** keeps geometry deterministic, allows hand editing and re-rendering, and lets the backend change.

## ADR-014 — Glossary is a deterministic lookup, not RAG

**Status:** Accepted

German text is matched against a CSV glossary (normalised, compound- and OCR-tolerant); only matching `confirmed`/`proposed` entries go into the prompt, and a post-check verifies them in the Polish output. **Reason:** thousands of names fit in no prompt but cost nothing as a lookup; no embedding model or large context is needed, so it runs on a small local model. Ambiguous and disputed entries are never given to the model.

## ADR-015 — GUI toolkit: Tkinter; the GUI only reads and edits files and runs the pipeline as subprocesses

**Status:** Proposed (stage A, 2026-11-15; awaiting the owner's confirmation)

The GUI (`gui*.py`) uses Tkinter with Pillow's `ImageTk`. It never imports pipeline stages and never changes pipeline output; `booktrans.py` / `ocrfix.py` run as subprocesses (stage E). Logic that matters is toolkit-free (`project.py`, `envcheck.py`, `pageindex.py`).

**Reason:** the central widget is a text editor with programmatic positioning, tags and undo, and the correction window must be a real separate OS window; Tk's `Text` provides this, Dear PyGui's multiline input does not, PyQt was rejected earlier for its MacPorts dependencies. Subprocesses keep the GUI usable without OpenCV/Tesseract and make a crashing stage harmless. **Cost:** Tk is a separate MacPorts package (`py3NN-tkinter`). See `GUI.md` D1, D5, D10.

## ADR-016 — Human work lives outside the pipeline's output folder

**Status:** Proposed (stage A, 2026-11-15)

A project is `project.json` + `pipeline/<book>/` (disposable pipeline output) + `review/` (human decisions). The pipeline never writes to `review/`; the GUI never writes into `pipeline/`. All GUI files are JSON, written atomically.

**Reason:** `booktrans.py` deletes its result folder before a full run, so anything a human typed inside it would be lost. See `GUI.md` D4, D8.

## ADR-017 — The editable unit is the translation unit; edits are an overlay applied by `ocrfix.py`

**Status:** Proposed (stage A, 2026-11-15; awaiting the owner's confirmation)

A human corrects the text of a *unit* (what is translated), shown per page as the "review JSON" (`text_raw` read-only, `text` editable). Line texts in `document.json` stay raw OCR evidence (ADR-002). Edits are stored in `review/unit_edits.json` keyed by unit id with a hash of `text_raw`; `ocrfix.py run` applies them (stage D) so a re-run reproduces them; an edit whose hash no longer matches is reported as stale, not applied. Flags of an edited unit count as reviewed.

**Reason:** the translation reads unit text; editing lines would require re-running stage 4's joining, and one stored text per unit keeps two windows consistent when a paragraph crosses a page. See `GUI.md` D2, section 6.

## ADR-018 — Marks are located by replaying stage 4; word boxes are stored by stage 2

**Status:** Proposed (stage A; the stage 2 change is stage B)

A correction or flag offset in a unit is traced to OCR lines by replaying the stage 4 joining with position tracking (`pageindex.replay`) and verifying the result against the stored text; units that do not reproduce are marked unaligned instead of mis-placed. Stage 2 will keep the per-word boxes Tesseract already returns (`words: [{text, bbox, conf}]` per line, document schema 1.1); existing data falls back to estimated boxes.

**Reason:** unit text is a deterministic function of line texts, so no fuzzy alignment is needed; only a word box can mark a single word on the page. See `GUI.md` D6, D7, section 7.

## ADR-019 — Translation through the Claude Code CLI on the owner's subscription; local models set aside

**Context.** The first real run with a local model (Gemma 4 12B in LM Studio, 16 GB Mac; HANDOFF 7a) gave
mediocre Polish: non-words, a nonsense sentence, wrong terms. Larger models do not fit the owner's machines. The
owner has a Claude subscription and Claude Code installed (`~/.local/bin/claude`), but no wish to pay per token.
**Decision.** `translate.py --provider claude-code` runs `claude -p --output-format json` once per unit with the same
prompt, glossary, checks, retries and cache as the other engines, own system prompt, no tools, no MCP servers, no
saved sessions, in an empty temporary folder; `ANTHROPIC_API_KEY` is removed from its environment so the
subscription is used. Not `--bare` (it accepts only an API key). Default engine of a GUI project (2026-11-18).
**Consequences.** Usage counts against the plan's limits: a limit stops the run cleanly and the cache makes the
re-run continue. The CLI's flags can change between versions (checked against 2.1.326). LM Studio and the Anthropic
API stay as engines; nothing in the translation contract (ADR-013) changed.

## ADR-020 — Translation output lives in the project, outside the pipeline folder

**Context.** A full `booktrans.py` run deletes `pipeline/<book>/` (ADR-016); `translate.py` wrote next to
`document.json` by default, and its cache holds paid-for (or limit-counted) answers.
**Decision.** A GUI project translates to `<project>/translation/translations.json`, the cache
`translations.cache.json` beside it; the settings live in `project.json` (schema 1.1). A partial run (`--pages`,
`--only`) merges into the existing output, and the paragraph context always comes from the whole book, so a
partial run and a full run share cache keys.
**Consequences.** Re-running the pipeline never costs a re-translation of unchanged units. The translations are
keyed by unit id, which a pipeline re-run renumbers; units whose text is unchanged are found again through the
cache, the output file itself is rebuilt by the next full translation run.

## ADR-021 — Rendering is a layout plan plus per-page drawing; body text flows through line slots in step with the pages

**Context.** `render.py` laid out and drew the whole book in one function at full resolution. The body was one
rectangle per page filled line after line from the top of its first paragraph, so a figure between paragraphs was
overprinted and other layouts (two columns, text beside a picture) could not work; size and letter-spacing were
chosen once for the book and the text flowed freely, so in a whole book paragraphs drift away from their pages
and figures. A GUI viewer and per-block adjustments need to change or show one page without redrawing the book.
**Decision.** Split into `typeset.py` (line breaking), `layout.py` (a JSON plan of every line for the whole book)
and `render.py` (draw one page from the plan). Body text is poured into the original line slots. A paragraph never
starts before its original page; size (0.90-1.03x) and letter-spacing (+-0.02 em) are fitted per page with a cost
(delay, empty slots, orphans/widows, carried lines, change from the page before) and one page of lookahead.
**Consequences.** Rebuilt pages keep the original line positions and the text-figure correspondence; a page may
end with empty slots when the Polish is shorter (reported, not filled by pulling text back). Size can differ
slightly between pages (the change cost keeps neighbours together). The plan is the interface for the GUI and for
quality checks; `render()` keeps its signature for `booktrans.py`.

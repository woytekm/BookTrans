# Rendering (`layout.py`, `render.py`, `typeset.py`)

Rebuilds each page from `document.json` and a translations JSON. All sizes below are in working pixels
of the page image unless stated; at high resolution everything is multiplied by the page's `scale` when
drawing, so the output has the full resolution of the scan.

```bash
python render.py document.json translations.json -o out [--pages 3,5-7] \
  [--font F --bold-font F --italic-font F] [--background paper|scan] [--resolution auto|work] \
  [--ink R,G,B] [--paragraph-indent EM] [--tracking auto|EM] [--size-scale F] [--word-space F] [--no-page-fit] \
  [--overrides review/render_overrides.json] [--pdf [book.pdf]] [--pdf-dpi N]
```

`--pages` takes the document's page numbers (consecutive over the book, as in the GUI's page list), not the printed ones; no
match is an error. Writes `page_NNN_X.png`, `compare.png` (original | reconstruction; one `compare_page_NNN.png` per page
for more than 4 pages, written as each page is drawn), `layout.json` (the plan), `report.json`, and with `--pdf` one
PDF of the drawn pages.

## Two steps: plan, then draw (since 2026-11-18)

| Module | Role |
|---|---|
| `typeset.py` | fonts, pieces with break kinds, optimum-fit line breaking; no page model, no drawing |
| `layout.py` | `make_plan(doc, translations, fonts, settings)`: the **layout plan** of the whole book |
| `render.py` | `draw_page(doc, plan, page_index)`: one page drawn from the plan; `render()` = plan + draw + outputs; the CLI |

The layout always covers the whole book, because body text flows across pages; computing it is cheap
(about 0.06 s per page). Drawing is the expensive part (full-resolution canvases), and it is per page:
`--pages` draws only some pages, `draw_page` redraws one from a saved `layout.json` (the GUI will use this).

### The plan (`layout.json`, schema `booktrans.layout/1`)

```
{schema, settings, fonts: {regular, bold, italic: path}, ratios: {style: {cap, asc, box, desc}},
 pages: [{index, page, side,
          lines: [{unit, block, role, style, size, track, x, baseline, width, gap, hyphen,
                   words: [[text, width], ...], upper?, overflow?}],
          keep: [block_id],        # original pixels kept (untranslated text)
          wipe: [block_id],        # translated text blocks (wiped on a scan background)
          body: {slots, used, blank, size_px, tracking_em, size_factor, track_delta_em, delayed, breaks} | null}]}
```

`x`/`baseline` are where the line starts (anchor `ls`), `track` is letter-spacing in px per character,
`gap` the word space of a justified line, `words` the drawable words with their advance widths (pieces of a
hyphenated word joined). `report.json` is `plan.report`.

## Drawing

1. **Canvas.** `--background paper` (default): a clean canvas in the page's paper colour (estimated from the
   page when the document has none). `scan`: the scan itself with each translated text block wiped (local paper
   tone via dilate + blur, gain-matched to the surrounding tone), so shading and the gutter gradient survive.
2. **Figures and furniture.** Figures are pasted from `figures/` at their place. Graphics (page numbers,
   rules, printer's mark) are composited by multiplying the crop normalised by its own paper colour (noise
   threshold 0.06), so no grey patch is left. **Line drawings** (a figure crop that is over 80 % paper with few
   half-tones, `render.is_line_drawing`, measured against the page's paper colour) are composited the same way
   since 2026-11-18: before, each drawing pasted its own paper tone as a visible rectangle.
3. **Kept text.** Untranslated units, and text blocks without a unit that are not inside a figure (a figure
   crop already carries its labels), keep their original pixels. Before 2026-11-18 a text block without a unit
   vanished on a paper background.
4. **Lines** from the plan.

**Human text corrections** (since 2026-11-19; `text_edits.py`, `--text-edits review/text_edits.json`, passed by the project's
render command when the file exists). A human's corrected Polish for a block (render window, Block tab, *Save text*) is kept in
its own file, never in `translations.json`, so a new translation run cannot overwrite it. Each correction is the whole text of
the unit, keyed by a fingerprint of the **German it was translated from** (the translation's `source`, whitespace folded) -
not by the unit number, which changes when the pipeline runs again. The unit number and save time are stored only for a human
reading the file. A correction whose German no longer exists (OCR corrected, text changed) is *orphaned*: not applied, not
deleted, listed in the Book tab (*Show…*). Saving the machine text again removes the correction; every save keeps one backup
(`text_edits.json.bak`). Applied units carry `edited` and `pl_machine` in the plan's translation view.

**Page position** (since 2026-11-18; `--page-position`, project `render.page_position`, *Page position* in the Book tab).
`original` (default): the content stays where it is on the scan, so left and right pages keep their different
margins (gutter / outer edge) and the scan's small shifts - right for a print edition. `centered`: every page gets the
book's median page size, its content is centred horizontally (by the text column, else by the content box without
scan noise) and its top is put at the book's median top (`render.page_frame`), so pages no longer jump left / right
and up / down - better for a PDF read on a screen. The page is drawn as usual and moved onto the new page; the GUI
preview and its outlines and clicks follow the shift.

**Output resolution** (since 2026-11-18). Final pages are drawn at `--dpi` (default 300, `render.DEFAULT_OUTPUT_DPI`;
the project's `render.output_dpi`, *Output DPI* in the render window's Book tab): the scale is `dpi / WORK_DPI`, the
text is drawn at that size and the scan / figures are resampled to it, from the full-resolution scan when the document
has one. Before, a document without full-resolution pages was drawn at its working scale (~120-135 DPI), so small text
(captions) looked pixelated. `--resolution work` still draws at the working scale (quick); previews in the GUI draw at
the zoom on screen.

## Body text: line slots

Every original body line (blocks with role `paragraph` whose unit is translated) is a **slot**: its top
(the OCR line tops regularised to the block's line pitch; a line clearly off the grid keeps its own top),
left and right edge. The edges are those of the block's **column**: blocks are grouped by horizontal overlap;
with one group the page's measured justified edges (`layout.columns`) are used, otherwise the median left edge
and the maximum right edge of the group's multi-line blocks. A figure, graphic or other text block that
reaches into a slot's band narrows the slot on that side (text wrapped around a picture); a slot narrower than
a quarter of the column is dropped. Slots are ordered column by column, top to bottom.

The Polish lines are poured into the slots: baseline = slot top + ascender x size, x = slot left (+ indent
on a paragraph's first line), justified to the slot width. So the text keeps the original line positions and
cannot run over a figure, a heading, a caption or a gap in the column; it flows through two columns and beside
pictures. (Before, the body was one rectangle per page from the top of its first paragraph, filled line after
line: a figure between two paragraphs was overprinted, and a text label inside a figure moved the body top.)

Untranslated body paragraphs keep their pixels and their slots are not used.

**Robustness against OCR region errors** (found on the first real book, 14 pages, 2026-11-18):

- *Scan noise read as text* (`layout.is_noise`: fewer than 2 letters, or narrower than 2.5 glyphs with fewer than 4):
  `|` and `N` at the page edges, a page number or figure number read as a paragraph. Never typeset and never part of a
  unit's flow; outside the text column it is dropped (plan `dropped`), inside it its pixels are kept. A unit that stage 4
  joined to such a block (noise at the foot of a page + the real paragraph on the next) starts at its real text.
- *Stray marks read as lines* (`_junk_line`: shorter than 12 % of the column, at most 2 letters; e.g. `)`, `Ef`, `5`
  from margin marks) get no slot, and the line pitch is measured from the real lines (median distance between
  consecutive real lines; a block's pitch more than 15 % off the page's is replaced), since such marks halve the
  measured pitch.
- *Overlapping blocks* (an OCR region read twice): one slot per line (slots closer than half a line are merged).
- *Even grid:* each run of slots in a column (up to a gap of 1.6 lines) is put on one line grid fitted to the measured
  tops, so the small gaps between the original paragraph blocks do not show up inside the new paragraphs.
- *Body text classified as a caption* (`looks_like_body`: a caption, footnote or byline of 4 lines or more at body size,
  e.g. a whole page under a plate) is set as body text (report `reclassified`).
- A paragraph block narrower than 30 % of the text column (a note beside it) is a fixed frame, not body text.

## Body text: flow kept in step with the pages

Before, size and letter-spacing were chosen once for the book and the text flowed freely: in a book, a few
percent of difference in length piled up page after page, paragraphs drifted away from their pages and
figures, and the shrink step only looked at the book's total line count.

Now a paragraph **never starts before the page it started on in the original**, and the fit is chosen
**per page**:

- The book has one base size (the median width-calibrated size of all body blocks, see below) and one base
  letter-spacing.
- For every page, candidates (`PAGE_FIT`) vary the size (0.90 ... 1.03 x) and the letter-spacing
  (+-0.02 em), each with a visual cost (letter-spacing is cheaper than size, smaller cheaper than larger).
- A candidate fills the page's slots with the rest of the paragraph from the page before and the paragraphs
  that may start here. Its cost: visual cost + 1000 per paragraph that started here in the original but
  would start later + 25 per empty slot at the foot + 40 per orphan/widow avoided + 12 per line carried to the
  next page beyond what the original carried + 500 per line beyond the last page + a change cost (300 per unit
  of size factor, 400 per em of letter-spacing that differs from the page before, so facing pages match).
- The choice looks one page ahead: a candidate is judged by its own cost plus the best cost of the next page
  after it. Candidates are tried in order of visual cost and the search stops when no later one can win.
- A paragraph that does not fit is split at a line break; the rest is typeset again with the next page's fit
  (no indent). Orphan: a paragraph's first line alone at the foot moves to the next page. Widow: its last line
  alone at the head of the next page takes one more line with it.
- When even the smallest fit cannot hold the page, the remaining paragraphs move to the next page (warning
  "started here in the original start on a later page"); the next page is back in step as soon as it can be.
  A page whose paragraphs are shorter in Polish keeps empty slots at its foot (warning when more than one)
  rather than pulling the next page's paragraph back. Text left after the last page is set below the last slot
  (warning "overflow").

`--no-page-fit` (setting `page_fit: False`) keeps one size for the book and only shrinks a page that cannot
hold its text (1.0 ... 0.9). With a fixed `--tracking` only the size varies.

Measured on a synthetic 60-page book (paragraphs 0.90-1.08 x the original length): no paragraph off its
page, 7 empty slots in 2300, most pages at the base size; 3.5 s for the layout. On the first real book (14 pages,
74 units, Claude translation): every page at the base size except the broken page 62 (0.97), letter-spacing within
+-0.02 em, 4 empty slots, one paragraph pushed (page 62, where stage 2 lost lines); 8 s for layout, ink and drawing.

## In the GUI

*Render pages…* opens the render window (`gui_render.py`, `GUI.md` section 8): the rebuilt page beside the scan, the checks,
book settings and page / block adjustments, each saved at once and the book laid out again (in the GUI process, ADR-022);
*Render book…* runs this program. `render_session.RenderSession` is the toolkit-free part (also usable from a script).
`draw_page(..., scale=S)` draws a page at any scale (the preview at the zoom on screen).

## Settings and human adjustments

**Book settings** are the CLI options; in a GUI project they live in `project.json` → `render` (schema 1.2, `project.DEFAULT_RENDER`:
`background`, `resolution`, `font`/`bold_font`/`italic_font` (relative to the project), `tracking` (`auto` or em), `size_scale`,
`word_space`, `paragraph_indent`, `page_fit`, `ink`), validated by `project.check_render`; `Project.render_command(pages, resolution)`
builds the `render.py` call → `<project>/render/` (outside `pipeline/`).

**Adjustments** (`render_overrides.py`, `--overrides FILE`; in a project `review/render_overrides.json`) are for what the automatic
layout gets wrong:

| for | keys |
|---|---|
| a block (fixed frames) | `size_scale` (x fitted size), `tracking_em` (fixed), `word_space`, `anchor` (`left`/`right`/`center`), `max_width` (x frame width, replaces the 1.0-1.3 candidates and the neighbour limit) |
| any text block | `keep_original`: keep the scan's pixels (the whole unit, also on other pages) |
| a page (body text) | `size_factor`, `track_delta_em`: replace the automatic per-page fit |

Each entry carries `base`, a hash of the OCR text it was made for (block: its line texts; page: its text blocks). Pages are keyed by
`source_tiff#page_in_spread` (or `page N`). An entry whose block or page is gone or whose text changed is *stale*: not applied and named
as a warning. Fixed frames with adjustments have `adjusted: [keys]` in `report.json`; units kept by hand are in `kept_by_hand`.

## Checks (`report.json`: `checks`, `check_summary`)

`layout.check_plan(doc, plan)` looks at every planned line (ink box = ascender to descender, x to x + width):

| kind | meaning |
|---|---|
| `collision` | a line over a figure, a graphic, kept (untranslated) text, the lines of another block (x-height band only, so ascenders and descenders of neighbours may interleave), or off the page; also a warning |
| `overflow` | lines outside their frame (a fixed frame that could not hold its text) or after the last page |
| `drift` | a body paragraph starts on a later page than in the original |
| `blank` | empty body slots at the foot of a page |
| `loose` / `tight` | a justified line with word space beyond 1.6x / below 0.8x natural |

Each entry: `page`, `kind`, `unit`, `block`, `with` (the block or `page` it hits), `detail`. `check_summary`
counts the kinds per page (the GUI will show them in its page list). The CLI prints the totals.

## Line breaking

Optimum-fit dynamic programme (Knuth-Plass style) with justification: word space stretches within
0.75-2.0x natural (a relaxed 0.5-4.0 fallback, then greedy as last resort); hyphenation penalty 35,
per-line cost 8; a last line longer than 0.92 of the width costs +150 in justified mode (it would read as
a paragraph that ends exactly at the margin). Line widths may differ per line (slots). Polish hyphenation uses
`pyphen` (`pl_PL`) when installed. One-letter Polish words (a i o u w z) are glued to the next word so no line
ends with them. Piece widths use cumulative-prefix kerning so split words keep their kerning; they are measured
once per text at a reference size (800 px) and scaled, since the layout tries many sizes of a paragraph.

## Font size and font fitting

The original is a light, widely letter-spaced Antiqua; Crimson Text is denser and darker at the same
glyph height. Language length is not the cause of an underfilled page (Polish is about 3 % shorter
than German), the typeface is. Measured on spread 1: the size that reproduces the original line widths
is 20.3 px but the glyph height implies 16.2 px; filling the widths at the glyph-height size needs about
0.10 em of letter-spacing.

Automatic fitting (`--tracking auto`, default):

- **Size** = the width-calibrated size (the size at which the *original* text would set at the original
  line widths, justified non-last lines weighted 0.95), clamped to 0.85 ... 1.04x the height-based size
  (`FIT_UP`). Uppercase blocks clamp to 0.7 ... 1.15x.
- **Tracking** = `fit_track()`: the median over the original lines of (original width - natural width)
  / (characters - 1), clamped to 0 ... 0.12 em (`FIT_MAX_EM`).
- Body text: base size and tracking for the whole book (median over its blocks), then the per-page fit above.
  Fixed frames fit per block.

## Fixed frames

Headings, captions, bylines and footnotes are set inside their original block: anchored left, right or
centre as the original, uppercase blocks letter-spaced, baselines on the block's (regularised) line tops.
Candidate widths up to 1.3x the frame width are allowed, but never into a neighbouring block on the same rows
and never beyond the column; reduce tracking then size (down to 0.82) until every line fits. Headings and captions
are never hyphenated (`typeset(..., hyphenate=False)`).

### Captions (since 2026-11-18)

- **One size.** `caption_bases`: all captions whose glyph height is within 0.7-1.6x the book's median caption form one
  group (OCR heights scatter with accents and fragments); each gets the group's median fitted size and letter-spacing.
  Captions of one page then all share the smallest size any of them needs (a dry run first; a capped caption does not
  shrink below the page's size, it takes extra lines instead).
- **Room.** `caption_geometry`: the caption's figure (`figure_ids`, else the nearest figure above / beside), its
  alignment to it (beside: flush towards the picture; under: centred when centred on it, flush right / left when
  flush), how far it may reach (neighbours on the same rows, the page content; under a figure first only the
  picture's width) and how many extra lines fit in the free space below (or above, for a caption beside its picture
  and level with its foot).
- **Leading.** A caption set smaller than its original glyphs closes its line pitch in proportion (pitch x size /
  original size), and its lines are laid on a new even grid when their number or pitch changes; the free space it may
  take (down to about a glyph height above the next block) is counted at that pitch. Before, extra lines kept the
  original pitch and a long caption ran into the paragraph below (page 52, `59 POLICE. PIERŚCIEŃ TRZONU KOLUMNY`).
- **Gap to the text below** (since 2026-11-20): a caption growing down keeps `CAPTION_KEEP_GAP` (60 %) of the printed
  gap to the next block, at least a glyph height; down to a glyph height alone, a long caption read as part of the
  paragraph under it. When it still does not fit (a narrow column at the page edge, words almost a line wide, no
  hyphenation) and a body paragraph is below, it takes up to `CAPTION_PUSH_LINES` (2) of that paragraph's lines
  instead of overflowing: captions are laid out before the body flow, and the caption's ink bottom plus the kept gap
  becomes a floor (`floors`) - body slots above it in that column are skipped, so the text starts lower and the
  page's flow absorbs the line(s). It no longer pulls the page's shared caption size down either (page 52: the five
  captions of the row went back to the book's caption size).
- **Gap to the picture** (since 2026-11-19, `layout._keep_clear_of_figure`): at least half the caption size of paper
  between a caption and the picture above (or below) it - printed captions often sit a pixel or two from the picture,
  and the set caption, with full capitals, touched it. The caption moves away from the picture only as far as the free
  space on its other side allows (never into the text); captions beside a picture keep their horizontal gap from
  `caption_geometry`.
- **Order of tries**: the original number of lines at the base size down to 90 % (first within the picture's width,
  then the content width), then the extra lines, then the usual shrink ladder.
- An emptied unit (a human joined its text into another caption) is neither drawn nor kept (`report.emptied`).

Manual control: `--tracking EM` fixes letter-spacing (`0` reproduces the pre-fitting look and the old
15 % size clamp), `--size-scale` multiplies the size, `--word-space` multiplies the natural word space.
`--font`, `--bold-font`, `--italic-font` take any serif TTFs with Polish letters (ą ć ę ł ń ó ś ź ż);
the default is bundled Crimson Text.

`report.json`: per unit `lines`, `lines_original`, `size_px`, `tracking_px`, `tracking_em`, and for body units
`hyphenated_lines`, `starts_on_page`, `original_page`, for fixed frames `capacity`, `shrink`; per page
`body_lines_used`, `body_lines_capacity`, `blank`, `size_px`, `tracking_em`, `delayed`; plus `warnings`.

## Ink and fonts

**Ink** (`--ink auto`, default; project `render.ink` = `auto`; `typecolour.match_ink`): the *hue* is the median
colour of the scan's ink pixels (darker than 0.6 x paper) in a few large body paragraphs; the *shade* (between black
and that hue) is the one at which those paragraphs, set in the chosen font with the renderer's fit, are exactly as
dark as on the scan (mean ink coverage, noise floor 0.08; solved from one coverage mask per paragraph). So a lighter
font gets a darker ink and a heavier one a lighter ink. The scanner clips stroke cores to black, so "the darkest
pixels" is not the ink colour (that first attempt gave 13,2,2). `report.json`: `ink`, `ink_match` (`darkness_scan`,
`darkness_set`, `hue`, `k`; a `note` when the font is too light even in black). On the first real book with Crimson
Text: hue (107, 90, 78), shade 0.12 -> ink (13, 11, 10): Crimson Text needs nearly black to carry as much ink as the
print. The ink is also stored in `layout.json`, so `draw_page` from a saved plan uses it.

**Default font** (owner's decision 2026-11-18: Crimson Text Regular, configurable): named in **`config/fonts.json`**
(`regular`, optional `bold`, `italic`; paths relative to the repository, absolute or `~`; `path@weight` for a variable
font). `BOOKTRANS_FONTS=/path/to/other.json` points to another file (paths relative to that file). Used whenever no
font is given: `render.py` / `booktrans.py` without `--font`, a project whose `render.font` is empty, `fontcheck.py`'s
reference. `python envcheck.py` checks that its files exist. Precedence: `--font` / project `render.font` >
`$BOOKTRANS_FONTS` > `config/fonts.json` > built-in Crimson Text.

**Fonts.** `--font` takes a TTF/OTF or `path@weight` for a variable font (`CrimsonPro[wght].ttf@300`). Without
`--bold-font` / `--italic-font` the siblings of the regular file are used (`X-Light.ttf` -> `X-LightItalic.ttf`,
`X-SemiBold.ttf`; a variable font: its `-Italic[...]` file, bold = weight + 300), else the regular font: nothing is
mixed in from another family (before, any `--font` got Crimson Text's bold and italic). Bundled: Crimson Text
(Regular, SemiBold, Italic) and, since 2026-11-18, **Crimson Pro** (Light, LightItalic, Regular, Italic, SemiBold;
static instances of the OFL variable font), its lighter successor.

**Choosing a font: `fontcheck.py`.**

```bash
python fontcheck.py output/book/document.json [-o DIR] [--pages 50-53] [--blocks 6] \
    --candidate "Spectral Light=~/fonts/Spectral-Light.ttf" --candidate "Vollkorn=~/fonts/Vollkorn[wght].ttf@400"
```

For a few large body paragraphs it sets the *original German text* into the *original line boxes* with the
renderer's own size and letter-spacing fit, in black, next to the scan (`specimen_<block>.png`, 2x), and
measures per candidate (`fontcheck.json`, table on stderr):

| metric | meaning (1.0 = like the scan) |
|---|---|
| `width` | size that reproduces the original line widths / size that reproduces the scan's ascender height; > 1: the font is narrower (gets letter-spacing or a larger size) |
| `x_height` | font x-height / ascender over the scan's (both scan values measured from the ink profile of each line, not from the OCR glyph height) |
| `darkness` | mean ink coverage of the paragraph set in *black* / the scan's (noise floor 0.08); must be >= 1 (`--ink auto` then lightens the ink); < 1: too thin even in black; far above 1: heavier strokes, greyish when lightened |
| `tracking` | the fitted letter-spacing in em |
| `score` | 3 abs(ln width) + 3 abs(ln x_height) + (2 below 1, 0.7 above) abs(ln darkness); lower is better |

Checked on synthetic scans set in a known font: that font ranks first with width, x-height and darkness within a
few percent of 1. The original book's face is a light, wide, low-contrast Antiqua with a large x-height and a `ch`
ligature; none of the bundled fonts is that, so try candidates on the real 600 DPI scans. The scoring is the basis
for an automatic choice later (roadmap).

## Emphasis inside a paragraph

A translation may mark words with `<i>…</i>` (italic), `<b>…</b>` (bold), `<sp>…</sp>` (Sperrsatz: letter-spaced,
+0.16 em, `typeset.SP_EM`), `<f2>…</f2>` (**font B**: another typeface, e.g. for a Latin quotation set in a different
face; since 2026-11-20) and `<sup>…</sup>` (superscript: a note number, `SUP_SCALE` 0.62 of the size, raised
`SUP_RAISE` 0.36 of it), also across words and inside a word (`<i>Steinmetzzeichen</i>u`, `valeat<sup>2</sup>`). Tags
may nest (`<i><sup>3</sup></i>`); font B wins over bold over italic. The line breaker measures each run in its own font;
a word that changes style inside is not hyphenated. In the plan a word in another style is `[text, width, {style,
track, join}]` (`join`: no word space after it; a superscript's style is `sup_<style>`). The rest of a paragraph carried
to the next page keeps its tags. Headings in capitals keep the tags (`upper_keep_tags`). Without tags nothing changes.

Font B is a fourth font next to regular, bold and italic: `render.py --f2-font`, project `render.f2_font`, *Font B* in
the render window's Book tab; empty = the regular font.

Where the tags come from: the translation writes `<i>` around titles of cited works (rules file); everything else is
marked **by a human** in the render window's Block tab (*Mark selection*: Italic, Bold, Spaced, Font B, Sup, Plain) and
kept as a text correction (`review/text_edits.json`). The owner decided (2026-11-20) **not** to detect emphasis or a
change of typeface in the scan: at the working resolution suggestions would bring more false positives than help.

## PDF

`--pdf [NAME]` (default `book.pdf`) writes the drawn pages as one PDF, page by page (JPEG, quality 90; nothing kept in
memory). Page size = pixels / DPI; the default DPI is the output resolution (`--dpi`; the drawing scale x `WORK_DPI`, the
working images of the 600 DPI originals being at about 120 DPI), so the PDF has the book's physical size;
`--pdf-dpi` overrides it. The text in this PDF is pixels: not searchable, not selectable.

### TODO: searchable vector PDF (agreed with the owner 2026-11-18, not started)

Goal: a PDF whose text is real text (searchable, selectable, sharp at any zoom, much smaller), as a third output next to
the PNG pages and the image PDF (`render.py --vector-pdf`, *Vector PDF (searchable)* in *Render book…*).

Chosen approach (B; the owner agreed it is the right one, deferred as a serious effort, estimated 1-2 days):
- Draw from the layout plan, not from pixels: every plan line already has its words with x positions and widths,
  baseline, font, size, letter-spacing and `<i>`/`<b>`/`<sp>` runs. Place each word at its planned x (exact
  justification), letter-spacing with the PDF character-spacing operator (`Tc`), italic / bold with the family's font
  files, hyphen at line ends as drawn.
- Page content: the paper colour (or, for `--background scan`, the scan with the translated text wiped, as an image),
  figures and graphics as images (lossless or high-quality JPEG; line drawings as ink-only images), kept original text
  blocks as image crops, then the text. Both page positions (`original` / `centered`, `render.page_frame`).
- Library: **reportlab** (pure Python, `pip install reportlab`; TrueType embedding with subsetting, so ą ć ę ł ń ó ś
  ź ż work). Writing font embedding into the existing minimal `PdfWriter` is not worth it.
- Text order in the PDF = plan line order, so copying gives body text, captions and footnotes in sequence.

Rejected alternative (A): keep the image pages and add an invisible text layer (as OCRmyPDF does). Half a day, looks
identical, searchable, but the visible text stays pixels and files stay large; B covers it.

Points to check when it is built: Pillow (layout) and reportlab (PDF) measure glyphs with their own code, so kerning
inside a word may differ slightly (each word starts at its exact x, so it does not add up); a word hyphenated at a line
end is two pieces for search - add `/ActualText` with the whole word and test search in Preview and Acrobat; compare
each vector page with the raster render (rasterise the PDF, diff against `page_*.png`); file size and speed on a
whole book.

## Not done

- Reading order across a block that spans several columns (columns are found per page from the body blocks;
  a heading across two columns is a fixed frame, so it is not affected).
- Pulling text back to fill a page whose paragraphs are much shorter in Polish (deliberately not done: it
  would break the page sync).
- Choosing the font automatically (`fontcheck.py` scores candidates; picking the best and passing it on is not wired).
- Detecting emphasis in the scan and keeping it through the translation (the renderer is ready, see above).
- A searchable vector PDF (see *TODO* under PDF).
- Re-typesetting page numbers.

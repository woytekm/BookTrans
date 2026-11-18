# Rendering (`layout.py`, `render.py`, `typeset.py`)

Rebuilds each page from `document.json` and a translations JSON. All sizes below are in working pixels
of the page image unless stated; at high resolution everything is multiplied by the page's `scale` when
drawing, so the output has the full resolution of the scan.

```bash
python render.py document.json translations.json -o out [--pages 3,5-7] \
  [--font F --bold-font F --italic-font F] [--background paper|scan] [--resolution auto|work] \
  [--ink R,G,B] [--paragraph-indent EM] [--tracking auto|EM] [--size-scale F] [--word-space F] [--no-page-fit] \
  [--overrides review/render_overrides.json]
```

Writes `page_NNN_X.png`, `compare.png` (original | reconstruction; one `compare_page_NNN.png` per page
for more than 4 pages), `layout.json` (the plan) and `report.json`.

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
   threshold 0.06), so no grey patch is left.
3. **Kept text.** Untranslated units, and text blocks without a unit that are not inside a figure (a figure
   crop already carries its labels), keep their original pixels. Before 2026-11-18 a text block without a unit
   vanished on a paper background.
4. **Lines** from the plan.

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
page, 7 empty slots in 2300, most pages at the base size; 3.5 s for the layout.

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
and never beyond the column; reduce tracking then size (down to 0.82) until every line fits.

Manual control: `--tracking EM` fixes letter-spacing (`0` reproduces the pre-fitting look and the old
15 % size clamp), `--size-scale` multiplies the size, `--word-space` multiplies the natural word space.
`--font`, `--bold-font`, `--italic-font` take any serif TTFs with Polish letters (ą ć ę ł ń ó ś ź ż);
the default is bundled Crimson Text.

`report.json`: per unit `lines`, `lines_original`, `size_px`, `tracking_px`, `tracking_em`, and for body units
`hyphenated_lines`, `starts_on_page`, `original_page`, for fixed frames `capacity`, `shrink`; per page
`body_lines_used`, `body_lines_capacity`, `blank`, `size_px`, `tracking_em`, `delayed`; plus `warnings`.

## Ink and fonts

**Ink** (`--ink auto`, default; project `render.ink` = `auto`): `render.measure_ink` takes, inside the text blocks of up to
12 pages, the pixels darker than 0.6 x the paper and of those the darkest tenth (stroke cores), median colour. The
measured colour is in `report.json` (`ink`) and in `layout.json`, so `draw_page` from a saved plan uses it. Before it
was a fixed (34, 30, 26), darker and colder than the brownish print.

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
renderer's own size and letter-spacing fit, in the scan's ink, next to the scan (`specimen_<block>.png`, 2x), and
measures per candidate (`fontcheck.json`, table on stderr):

| metric | meaning (1.0 = like the scan) |
|---|---|
| `width` | size that reproduces the original line widths / size that reproduces the scan's ascender height; > 1: the font is narrower (gets letter-spacing or a larger size) |
| `x_height` | font x-height / ascender over the scan's (both scan values measured from the ink profile of each line, not from the OCR glyph height) |
| `darkness` | mean ink coverage of the set paragraph / the scan's (noise floor 0.08); > 1: darker page |
| `tracking` | the fitted letter-spacing in em |
| `score` | 3 abs(ln width) + 3 abs(ln x_height) + 2 abs(ln darkness); lower is better |

Checked on synthetic scans set in a known font: that font ranks first with width, x-height and darkness within a
few percent of 1. The original book's face is a light, wide, low-contrast Antiqua with a large x-height and a `ch`
ligature; none of the bundled fonts is that, so try candidates on the real 600 DPI scans. The scoring is the basis
for an automatic choice later (roadmap).

## Not done

- Reading order across a block that spans several columns (columns are found per page from the body blocks;
  a heading across two columns is a fixed frame, so it is not affected).
- Pulling text back to fill a page whose paragraphs are much shorter in Polish (deliberately not done: it
  would break the page sync).
- Choosing the font automatically (`fontcheck.py` scores candidates; picking the best and passing it on is not wired).
- Bold/italic or letter-spaced emphasis inside a paragraph (the model has no run-level styles).
- Re-typesetting page numbers.

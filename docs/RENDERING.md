# Rendering (`render.py`)

Rebuilds each page from `document.json` and a translations JSON. All sizes below are in working pixels
of the page image unless stated; at high resolution everything is multiplied by the page's `scale` when
drawing, so the output has the full resolution of the scan.

```bash
python render.py document.json translations.json -o out \
  [--font F --bold-font F --italic-font F] [--background paper|scan] [--resolution auto|work] \
  [--ink R,G,B] [--paragraph-indent EM] [--tracking auto|EM] [--size-scale F] [--word-space F]
```

Writes `page_NN_X.png`, `compare.png` (original | reconstruction for every page), `report.json`.

## Steps

1. **Canvas.** `--background paper` (default): a clean canvas in the page's paper colour. `scan`: the
   scan itself with each translated text block wiped (local paper tone via dilate + blur, gain-matched
   to the surrounding tone), so shading and the gutter gradient survive.
2. **Figures and furniture.** Figures are pasted from `figures/` at their place. Graphics (page
   numbers, rules, printer's mark) are composited by multiplying the crop normalised by its own paper
   colour (noise threshold 0.06), so no grey patch is left.
3. **Body text.** Paragraph units flow through the page's text column and across pages; capacity =
   the sum of the original line counts. If the Polish text needs more lines the size is reduced in steps
   1.0 ... 0.9; beyond that the text overflows below the last frame and `report.json` warns.
4. **Fixed frames.** Headings, captions, bylines and footnotes are set inside their original block:
   anchored left, right or centre as the original, uppercase blocks letter-spaced. Candidate widths up
   to 1.3x the frame width are allowed, but never into a neighbouring block on the same rows and never
   beyond the column; reduce tracking then size (down to 0.82) until every line fits.
5. **Untranslated units** keep their original pixels.

## Line breaking

Optimum-fit dynamic programme (Knuth-Plass style) with justification: word space stretches within
0.75-2.0x natural (a relaxed 0.5-4.0 fallback, then greedy as last resort); hyphenation penalty 35,
per-line cost 8; a last line longer than 0.92 of the width costs +150 in justified mode (it would read as
a paragraph that ends exactly at the margin). Polish hyphenation uses `pyphen` (`pl_PL`) when
installed. One-letter Polish words (a i o u w z) are glued to the next word so no line ends with
them. Piece widths use cumulative-prefix kerning so split words keep their kerning.

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
- Body text uses one size and one tracking for the whole book column (median over its blocks); fixed
  frames fit per block.

Result on spread 1: body 49 -> 54 lines (original 56), tracking 0.08 em; page 2 uses 37 of 39 lines.

Manual control: `--tracking EM` fixes letter-spacing (`0` reproduces the pre-fitting look and the old
15 % size clamp), `--size-scale` multiplies the size, `--word-space` multiplies the natural word space.
`--font`, `--bold-font`, `--italic-font` take any serif TTFs with Polish letters (ą ć ę ł ń ó ś ź ż);
the default is bundled Crimson Text.

`report.json` per unit: `lines`, `lines_original` (capacity for fixed frames), `size_px`, `tracking_px`,
`tracking_em`, `hyphenated_lines`, `relaxed_spacing`, `starts_on_page`, `shrink`; per page
`body_lines_used` / `body_lines_capacity`; plus `warnings`.

## Not done

- Choosing a font automatically from a supplied set (score x-height ratio, mean advance, stem
  darkness against the scan; the tracking fit already exists).
- Bold/italic inside a paragraph (the model has no run-level styles).
- Re-typesetting page numbers.
- Multi-column body text (one body column per page is assumed).

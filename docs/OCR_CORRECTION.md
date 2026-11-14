# OCR correction (stage 5, `ocrfix.py`)

Runs between stage 4 (`document.json`) and translation. It fixes the OCR slips that can be fixed safely,
records every change, and flags everything else for a human. Fully offline.

## Setup

```bash
pip install spylls symspellpy              # pure Python, no compiled parts
cd src/booktrans
python ocrfix.py fetch                     # SymSpell de-100k + LibreOffice de_DE_frami -> ../../dictionaries/ (git-ignored)
```

## Use

```bash
python ocrfix.py run output/book/document.json          # -> document.corrected.json, ocr_corrections.json, ocr_review.json
python translate.py output/book/document.corrected.json -o output/book/translations.json
python ocrfix.py text "Niederschlesten und Überzesten"   # try a string
```

Options: `-o FILE`, `--review FILE`, `--glossary CSV...` (default: all `glossary/*.csv`), `--no-suggest` (skip the
slow Hunspell suggestions, about 3-8 s per hopeless word; a page takes ~30 s without, ~75 s with),
`--no-hunspell`.

## What is "known"

A word is known if any of these accepts it (case-insensitive): the `de-100k` frequency list, Hunspell
`de_DE_frami`, `glossary/vocabulary.txt`, the words of the glossary CSV files (names and variants), or a
compound of known parts (each at least 4 letters, no function words, with linking elements `s es n en e er`).
Old spelling is checked through its modern form: `ß` -> `ss`, `th` -> `t`, initial `c` -> `k`/`z`. Numbers,
Roman numerals and initials followed by a dot are ignored.

Add book-specific words (regional names, technical terms, old spellings) to `glossary/vocabulary.txt`.

## Candidates and cost

| rule | example | cost |
|---|---|---|
| `ocr_confusion` (table `CONFUSIONS`, up to two substitutions) | `Binfassung` -> `Einfassung`, `dafi` -> `daß`, `O8` -> `OS` | 0.5-0.9 per substitution (+0.5 if the result is only a compound) |
| `edit_distance` (SymSpell, 1 edit for 5-7 letters, 2 for longer) | `wesentlihen` -> `wesentlichen` | 1.0 per edit |
| `lost_space` (segmentation, inner capital) | `nichtmehr`, `dieChorportale`, `besondersderaufschlußreichen` | 0.5-1.5 |
| `abbrev_space` | `z. B.in` -> `z. B. in` | 0.2 |
| `hunspell` (flag only) | `Kirchenortale` -> `Kirchenportale` | edit distance + 0.2 |

An exact glossary/vocabulary name gets a bonus (`Niederschlesten` -> `Niederschlesien`, cost 0.3).

## Decision (conservative)

A candidate is applied automatically only if **all** hold: cost <= `AUTO_MAX` (1.0); the word has at least
`MIN_AUTO_LEN` (5) letters (unless the candidate is a vocabulary/glossary name); the runner-up costs at
least `MARGIN` (0.29) more, or the best is `FREQ_RATIO` (50) times more frequent; a capitalised word (possibly
a name) is changed above cost 0.6 only if the candidate is a common word. All constants are at the top of
`ocrfix.py`.

Flag reasons: `ambiguous`, `weak_candidate`, `name_risk`, `short_word`, `no_candidate` (severity high for
lower-case words), `unknown_name_or_word` (capitalised, low), `suspect` (known word that looks like a frequent
OCR slip of a much more common one, e.g. `Fin` for `Ein`; low), `stray_letter`, `mixed_digits_letters`.

## Files

- `document.corrected.json`: copy of `document.json`; every unit has `text` (corrected), `text_raw`,
  `ocr_corrections`, `ocr_flags`. Blocks and lines keep the raw OCR (layout evidence). Top-level `ocr_correction`
  has the counts. Units whose block is `translation_status: exclude` are skipped.
- `ocr_corrections.json`: `corrections[]`: `unit`, `offset` (in `text_raw`), `before`, `after`, `rule`, `cost`.
- `ocr_review.json`: `flags[]` with `key`, `unit`, `offset`, `token`, `reason`, `severity`, `candidates[]`,
  `context`, `resolution`; `accepted_words[]`; `resolved[]` (decisions kept between runs).

## Human review

Edit `ocr_review.json`: set a flag's `resolution` to the right word (or phrase), or `"="` to accept the word
as it is; add words to `accepted_words` to accept them everywhere. Run `ocrfix.py run` again: the decisions are
applied (rule `human`) and moved to `resolved`. A decision is tied to the unit, the word and its occurrence
number, so it stays valid as long as the unit text does not change.

## Known limits

- No sentence-level grammar or n-gram check (no free offline German n-grams of useful size, and LanguageTool
  needs Java and ~200 MB): ambiguous cases are flagged instead of decided by context.
- A real word produced by a slip (`Fin`, `Sie` for `Die`) is only caught by the `suspect` heuristic.
- Candidates from the frequency list are lower case; a split-off noun is not capitalised (`der mitte`).
- Names that are neither in the glossary nor in the vocabulary are flagged (low severity), never changed above
  cost 0.6. Fill the glossary/vocabulary to reduce flags.
- Digits misread as digits (3/5) are not detected.

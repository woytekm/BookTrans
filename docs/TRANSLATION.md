# Translation: contract for the translation service

This document fixes what the translation step consumes and produces, so that the backend can be swapped
without touching the rest. `translate.py` (below) implements it for an OpenAI-compatible endpoint (LM
Studio). Two spreads were translated by hand in a chat session following exactly this contract: `examples/0001.pl.json`, `examples/0002.pl.json`.

## Input

`document.json` (written by `booktrans.py`), in particular:

- `units[]`: `unit_id`, `type`, `text` (dehyphenated source text), `source_block_ids`, `dehyphenated`,
  `joins`. One unit = one paragraph, caption, heading, byline or footnote, possibly spanning pages.
- The block of a unit (`pages[].blocks[]` with the same `unit_id`) carries the context a translator
  should see: `role` (`paragraph`, `heading`, `caption`, `author_byline_candidate`,
  `footnote_or_footer`), `typography.uppercase_ratio` (>= 0.6 means the original is set in capitals) and
  `translation_status` (`translate` | `exclude` | `review`).
- Only `translate` units are sent. Text inside drawings is `exclude` and stays untouched.

## Output: translations JSON

```json
{
  "schema": "1.0",
  "target_language": "pl",
  "prompt": "<the prompt used>",
  "notes": ["free-text remarks for a human reviewer"],
  "units": {
    "u0004": {"source": "<German text, OCR-corrected>", "pl": "<Polish text>"}
  }
}
```

`render.py` reads only `units[id].pl`. A unit without a translation keeps its original pixels and the
report carries a warning. `source` and `notes` are for review.

## Prompt

`prompts/translate_pl.txt` (the owner's wording, keep it as the base):

> You are a highly educated polish art and architecture historian translating a german art history and
> architecture related literature to polish. The literature is mostly related to Lower Silesia. For
> geographical names, use names assigned to places after 1945 where this apply. Language used should be
> appropriate for first half of XX century. Translate following text to polish:

## Rules the hand translations followed (the service should reproduce them)

- **Place names** use the post-1945 form: Pitschen = Byczyna, Ziegenhals = Głuchołazy, Ritterswalde =
  Domaszkowice, Bösdorf = Pakosławice, Friedland OS = Korfantów, Münsterberg = Ziębice, Rauden OS =
  Rudy, Oberschlesien = Górny Śląsk, Niederschlesien = Dolny Śląsk. Places that lie in Czechoslovakia
  take their Czech names (Barzdorf = Bartultovice, Jauernig = Javorník). A glossary file for the book
  (German name -> Polish name) should be passed to the model and extended as the book proceeds; unverified
  entries (Politz -> "Police") must be flagged in `notes`.
- **Technical terms.** The renderings below were chosen by the model in a chat session and are **not
  verified by an art-history specialist** ("Rundstab" is probably better rendered "wałek", "Hohlkehle"
  "wklęsłe żłobienie / kehla"); the owner should confirm or correct them. Examples chosen: Abtreppung = uskok, Kämpfer = impost, Gewände = węgar,
  Schaftring = pierścień trzonu kolumny, Archivolte = archiwolta, Rundstab = pałąk, Hohlkehle = wklęska,
  Backsteingotik = gotyk ceglany. These need a persistent terminology list.
- **Register:** appropriate for the first half of the 20th century (the prompt says so).
- **Figure references** "Abb. 37" become "il. 37" (the numbering is kept; it is also in captions).
- **Footnote markers**: the OCR often reads the superscript as an apostrophe or a digit; restore the
  superscript digit (`¹`) in the Polish text. Crimson Text has `¹`.
- **Captions** are in capitals in the original; the renderer uppercases the Polish text when
  `uppercase_ratio >= 0.6`, so the service may deliver normal case.
- **OCR repair** is expected before or during translation. Slips seen on the samples: `Fin`/`Ein`,
  `Kinfassung`/`Einfassung`, `s ist`/`Es ist`, `dürfie`/`dürfte`, `Stadtimauer`/`Stadtmauer`, lost
  spaces (`Portalmiteiner`), `OS` read as `0S`/`O05`, `Abb.538,39` for `Abb. 38, 39`, a figure number
  read as `59` for `39`, the Ä of `KÄMPFER` read as `A`. Record the corrected German in `source` and the
  fixes in `notes`.
- **Do not add or drop sentences.** The renderer fits the text to the original frame, and the Polish
  text is about 3 % shorter than the German on the samples (3423 vs 3544 characters on spread 1).

## The translation script (`src/booktrans/translate.py`)

**Status:** written and tested against a mock endpoint and a dry run on the sample book; **not yet run
against a real LM Studio model** (first thing to do on the Mac).

```bash
cd src/booktrans
python translate.py output/book/document.json -o output/book/translations.json            # LM Studio on localhost:1234
python translate.py output/book/document.json --endpoint http://192.168.1.20:1234/v1 --model gemma-3-12b-it
python translate.py output/book/document.json --dry-run                                     # only write the prompts (translations.prompts.txt)
python translate.py output/book/document.json --only u0021 u0022                            # just these units
python render.py output/book/document.json output/book/translations.json -o output/book/render
```

LM Studio: load a model, open the Developer tab, "Start Server" (default `http://localhost:1234/v1`).
Without `--model` the first model the server reports is used. `$LMSTUDIO_ENDPOINT` / `$LMSTUDIO_API_KEY`
work as defaults. Standard library only.

What it does for every unit with `translation_status: translate` (excluded and review units are skipped):

1. glossary lookup on the German text (see below), only usable entries are given to the model;
2. **one request per unit** (one `user` message: it works with every chat template, including Gemma's,
   which has no system role): base prompt (`prompts/translate_pl.txt`, the owner's wording), rules
   (`prompts/translate_rules_pl.txt`, editable: answer only with the translation, silent OCR repair,
   no added or dropped sentences, "Abb." -> "il.", footnote superscripts), the glossary list, the kind of
   text (heading, caption, footnote, paragraph; capitals in the original), the last sentence of the
   previous paragraph as context, and the text;
3. cleaning of the answer (`<think>` blocks, code fences, "Tłumaczenie:", quotes);
4. checks: glossary terms present (inflections accepted), length ratio 0.65-1.45 (texts over 60 characters),
   digits equal to the source. Glossary and length problems trigger up to `--retries` (default 2) repeats of
   the request with an `IMPORTANT:` remark naming the problem; digit differences are only reported (OCR
   often misreads digits in the source);
5. output `translations.json` in the format above plus, per unit, `glossary` (what was given) and `flags`
   (problems left, glossary entries that were *not* given because they are disputed or ambiguous), and a
   `notes` summary; it is rewritten after every unit.

Cache: `<output>.cache.json`, keyed by model, both prompts, glossary block, kind, context and text, so a
repeated or interrupted run only asks for what changed (editing a glossary entry re-translates only the
units where it matched). Delete the file to force a fresh translation.

Settings: `--temperature` (0.2), `--max-tokens` (4096), `--timeout` (600 s per request), `--glossary`
(default `glossary/*.csv`), `--prompt`, `--rules`.

Known limits: units are translated independently (one sentence of context only); no batching, no
streaming; the source stored in the output is the raw OCR text, not the corrected German; a model that
ignores "answer only with the translation" is only partly caught by `clean()`; the length check cannot
tell a bad translation of the right length from a good one, so spot checks stay necessary.

## Glossary (`src/booktrans/glossary.py`, `glossary/`)

Thousands of place names plus an art-history vocabulary do **not** need RAG, embeddings or a bigger
model. The glossary is a plain lookup applied *before* the request and a check applied *after*:

1. The unit's German text is matched against the glossary. Text and entries are normalised (case,
   umlauts, ß, accents folded: OCR drops diacritics, so `BOSDORF` finds `Bösdorf`). Terms match inside
   compounds (`Kämpfer` finds `Portalkämpfer`; terms shorter than 6 letters only as whole words). Place
   names must be capitalised, may span up to 3 words (`Friedland OS`) and tolerate one or two OCR slips
   (`Rittersvalde`, `OBERSCHLESTEN`).
2. Only matching entries with status `confirmed` or `proposed` go into the prompt
   (`prompt_block(hits)`: `- Pitschen = Byczyna (miejscowość, Kreuzburg OS)`; Polish in the nominative,
   the model inflects it). `disputed` and `ambiguous` entries are never given to the model.
3. After translation, `check()` verifies that every given term appears in the Polish text in some
   inflected form (stems compared: `Byczyna` is found in `Byczynie`) and flags the unit if not.

Cost: negligible memory, no extra model, a few hundred prompt tokens per unit, so it works with a 12B
model on 16 GB. One unit per request keeps the context small.

### File format

`glossary/terms.csv` (UTF-8, several files can be combined, first definition of a (de, kind) wins):
`de, pl, kind, status, district, variants, note, source`. `kind`: `place` | `term` | `person`; `status`:
`confirmed` | `proposed` | `disputed` | `ambiguous`; `variants`: other German spellings separated by `;`;
`district` tells apart places with the same name. The shipped `glossary/terms.csv` seeds the places and
terms of the two sample spreads (`proposed` unless verified; **the terms are model proposals, not
verified by a specialist**: `Rundstab` and `Hohlkehle` are `disputed`).

### Commands

```bash
cd src/booktrans
python glossary.py match      output/book/document.json ../../glossary/terms.csv        # hits per unit + capitalised names not in the glossary
python glossary.py check      output/book/document.json translations.json ../../glossary/terms.csv   # exit 1 if a term is missing in the Polish
python glossary.py candidates output/book/document.json ../../glossary/terms.csv        # capitalised nouns not in the glossary, by frequency
python glossary.py import-places train.csv test.csv -o ../../glossary/places.csv        # german,polish CSV -> glossary
```

`check` on the sample book caught a real inconsistency: the caption `u0015` had been translated "PIERŚCIEŃ
TRZONU" while the glossary says "pierścień trzonu kolumny".

### Sources for the place names

- **German-Polish paired place names** (Hugging Face `DebasishDhal99/german-polish-paired-placenames`, CC-BY 4.0,
  about 9,800 rows `german,polish`, scraped from the German Wikipedia list "Liste deutscher Bezeichnungen
  polnischer Orte"; whole of Poland, no district, some German names map to several places). The
  development sandbox cannot reach Hugging Face; download the CSV file(s) on a Mac (e.g.
  `pip install huggingface_hub; huggingface-cli download DebasishDhal99/german-polish-paired-placenames
  --repo-type dataset --local-dir hfpairs`) and run `import-places` on them. A German name with several
  Polish ones becomes `ambiguous` (not given to the model; add the district by hand to resolve).
- Wikidata (historical German names with district), the 1946 name decrees (Komisja Ustalania Nazw
  Miejscowości) and the state name register (PRNG/TERYT) for verification.
- Names from the 1930s renamings (e.g. Polkwitz -> Heerwegen) and old spellings go into `variants`.

### Still to do

- Resolve ambiguous places from context (neighbouring names, the district named in the text).
- A review workflow for `proposed` entries (`candidates` is the start).

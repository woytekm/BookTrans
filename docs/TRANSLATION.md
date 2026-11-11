# Translation: contract for the translation service

The translation step is **not implemented**. This document fixes what it consumes and produces so that
it can be built (and swapped) without touching the rest. Two spreads were translated by hand in a chat
session following exactly this contract: `examples/0001.pl.json`, `examples/0002.pl.json`.

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

## Service design (proposal, nothing built)

- A script `translate.py document.json -o translations.json` (plain standalone, like the others).
- A local LLM behind an OpenAI-compatible HTTP endpoint (Ollama, llama.cpp server, LM Studio); the
  endpoint and model name come from `config/` or arguments.
- One unit per request, in document order, with a short context: the previous unit's last sentence and
  the role (`heading`, `caption`, ...) plus the glossary. Headings and captions are short and
  uppercase; give the model the role so it does not write a sentence.
- A cache keyed by a hash of (model, prompt, source text, glossary) so a re-run only translates changed
  units.
- Post-checks that fail loudly into `notes`: Polish/German length ratio outside about 0.7-1.3, digits
  and figure numbers that differ from the source, leftover German words, missing place-name glossary
  hits.
- Units cut at a spread boundary are whole units in a book document (batch mode, `HANDOFF.md`
  section 8): translate them as one. Unit ids are numbered over the whole book.
- Output stays in the format above so a human can edit the JSON and re-render.

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

- Wire `prompt_block` and `check` into `translate.py` once it exists.
- Resolve ambiguous places from context (neighbouring names, the district named in the text).
- A review workflow for `proposed` entries (`candidates` is the start).

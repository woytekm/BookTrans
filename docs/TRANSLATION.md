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
- Units cut at a spread boundary: see `HANDOFF.md` section 8. Either run stage 4 over consecutive
  spreads so the unit is whole, or give the previous spread's trailing unit as context and split the
  translation at the same point.
- Output stays in the format above so a human can edit the JSON and re-render.

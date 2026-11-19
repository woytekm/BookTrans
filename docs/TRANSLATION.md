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
python translate.py output/book/document.json --pages 3,5-7                                 # the units on these pages
python translate.py output/book/document.json --pages 4 --force                             # page 4 again, ignoring cached answers
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
   digits equal to the source, punctuation (`punctuation_problems`, since 2026-11-18: a comma or semicolon next to a
   dash, the long dash `—`, a spaced hyphen used as a dash, two marks together such as `?,`; the rules in
   `prompts/translate_rules_pl.txt` ask for the Polish short dash `–` with spaces and an inserted phrase set off by
   dashes or commas, never both), markup (`tag_problems`: only `<i>…</i>`, balanced, around titles of cited works,
   which the rules keep untranslated and in italics; any other tag or Markdown emphasis is a problem). Glossary, length,
   punctuation and markup problems trigger up to `--retries` (default 2) repeats of
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

Diagnostics: every request logs its time, prompt and answer token counts, tokens per second, the finish reason and any hidden reasoning (`reasoning_content`); an empty answer is explained (thinking model that used its tokens for reasoning, or cut off by the token/context limit). Use it to judge a model's real speed before changing anything.

Providers: `--provider openai` (default; LM Studio or any OpenAI-compatible server), `--provider claude-code` (the Claude Code CLI with the owner's subscription, see below) or `--provider anthropic`
(Anthropic Messages API, `POST https://api.anthropic.com/v1/messages`; key in `$ANTHROPIC_API_KEY` or
`--api-key`, model default `claude-sonnet-5-5`, change with `--model`). An API key is created in the
Anthropic Console and is billed per token, separately from a Claude Pro/Max subscription (as far as the
author knows; check the current pricing). The answer carries exact token counts: every request logs them, the
run ends with a total (`usage this run: ...`, also stored as `usage` in `translations.json`), and
`--price-in` / `--price-out` (USD per million tokens, from the pricing page) add a cost. Rate limits and
overload (HTTP 429, 5xx, 529) are retried with a growing pause; other HTTP errors stop the run with the server's
message (for example a wrong key). Cached units cost nothing, so a repeated run only pays for what changed.
`--dry-run` prints a rough token estimate. The Anthropic provider has been tested only against a mock
server, not against the real API.

```bash
export ANTHROPIC_API_KEY=...            # never commit or paste a key; create a key limited to this use and revoke it when done
python translate.py output/book/document.json --provider anthropic --price-in 3 --price-out 15   # use the real prices
```

### Partial runs (`--only`, `--pages`) and the output folder (2026-11-18)

- `--pages 1,3-5` selects the units with at least one source block on those pages (a paragraph across a page
  break belongs to both pages); with `--only` both sets are translated.
- A partial run **updates** `translations.json`: the selected units are translated, the other units of the existing
  file are kept, everything in document order. (Before, the file was rewritten with only the selected units.)
- The context sent with a paragraph (the last sentence of the preceding paragraph) is taken from the whole book,
  also in a partial run, so the prompt and the cache key are the same as in a full run: translating page 3 first
  and the whole book later does not translate page 3 twice. (Before, the first selected paragraph lost its context.)
- The output folder is created when it does not exist (the GUI writes to `<project>/translation/`).
- `--force` ignores cached answers for the selected units (with `--only` / `--pages`; otherwise the whole book)
  and asks the model again; the new answer replaces the cached one, so later runs reuse it. Without it, a
  repeated run with unchanged prompt, glossary hits, context, text and model returns the cached answer.
- The GUI's *Run translation* window builds this command from the project's translation settings (`GUI.md`, section 3).

### Claude Code as the translation engine (`--provider claude-code`)

Added 2026-11-17 because the local models the owner can run (Gemma 4 12B on a 16 GB Mac) translate too
poorly (section 7a of `HANDOFF.md`). `translate.py` runs the **Claude Code CLI** once per unit in headless mode,
logged in with the owner's Claude subscription: **no API key, no per-token bill**; the tokens count against the
plan's usage limits instead.

```bash
claude                      # once, in a terminal: /login with the Claude account, then /exit
python translate.py output/book/document.json --provider claude-code                 # model opus (default)
python translate.py output/book/document.json --provider claude-code --model sonnet  # lighter on the usage limits
python translate.py output/book/document.json --provider claude-code --only u0021    # one unit, to look at the quality first
```

The call (checked against Claude Code 2.1.326; `claude --help` on the Mac shows the installed version's flags):

```
claude -p --output-format json --model opus --system-prompt "<translator, no tools, answer only>" \
       --tools "" --strict-mcp-config --no-session-persistence          (the prompt on stdin)
```

- The prompt is the same as for the other providers (base prompt, rules, glossary list, kind of text, context,
  text), sent on stdin. `--system-prompt` *replaces* Claude Code's own coding-assistant system prompt, `--tools ""`
  offers no tool at all, `--strict-mcp-config` skips the owner's MCP servers (faster start, fewer tokens),
  `--no-session-persistence` keeps thousands of one-shot sessions out of `~/.claude`. Each call runs in a fresh,
  empty temporary folder so no `CLAUDE.md` or project files are picked up.
- **Not `--bare`**: it would be the cleanest mode, but it accepts only an API key, never the subscription login.
- `ANTHROPIC_API_KEY` is **removed** from the CLI's environment, because with it Claude Code would bill the API
  instead of the subscription; `--claude-use-api-key` keeps it.
- The program is found as `--claude-bin`, `$CLAUDE_BIN`, `claude` on `PATH`, or `~/.local/bin/claude` (the native
  installer's place, where the owner has it). `python envcheck.py` shows it under *Translation*.
- `--temperature` and `--max-tokens` do not apply (the CLI has no such options). `--timeout` is per call.
- The JSON result gives token counts (logged per call and totalled as `usage`) and `total_cost_usd`: on a
  subscription that is the notional API price, reported as `reported_cost`, not a bill.
- Errors: not logged in (the CLI words it "Authentication error · This may be a temporary network issue") stops the
  run at once with a hint to `/login`; overload / 5xx is retried after 10, 20, 40 s; **a usage limit stops the run**,
  writes what is done to `translations.json` and says so. Everything finished is in the cache, so the same command
  run after the limit resets continues where it stopped.
- `--claude-args ARG ...` passes anything else on (for example `--claude-args=--verbose`).
- Tests run against a fake `claude` program and against results recorded from the real CLI (not logged in); **the
  provider has not yet translated a page with a real login** (first thing to do on the Mac).

Reasoning models: a "thinking" model can spend all `--max-tokens` on hidden reasoning and return an empty
answer. Seen with `google/gemma-4-12b` in LM Studio: 15,000 characters of reasoning, 4095 tokens, 420 s at
9.7 tok/s, empty answer. **Gemma 4 thinks by default.** In LM Studio, open the model's settings (Custom
Fields) and switch **"Enable Thinking" off** (default on, per the model page). Google's documentation
describes the same switch as the chat-template flag `enable_thinking`. `--no-think` sends
`chat_template_kwargs.enable_thinking=false` and `reasoning_effort=none` with each request; whether LM
Studio passes them to the template is unverified, so check the `model call` log line for hidden reasoning.
`--extra-body JSON` adds any other request field.

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

### Human corrections of the translation

`translations.json` stays as the translation wrote it. A human's corrections of single blocks are kept in the project's
`review/text_edits.json` (render window, Block tab) and set over the translation when rendering (`text_edits.py`,
`RENDERING.md` → *Human text corrections*); a new translation run therefore never overwrites them. They follow the German
text, not the unit number; when the German changes, the correction is listed as orphaned instead of applied.

### One central glossary for all books (since 2026-11-18)

The glossary is shared by every book the owner processes (`glossary_store.py`):

- **Central glossary**: every `*.csv` in one folder outside the repository, default `~/BookTrans/glossary`, set in
  `~/.booktrans/config.json` (`{"glossary_dir": "..."}`; `$BOOKTRANS_CONFIG` names another config file,
  `$BOOKTRANS_GLOSSARY` overrides the folder) or in the GUI (*Glossary…* -> *Change…*). On first use the folder is
  created and seeded with the repository's `glossary/*.csv`; from then on the repository copy is only that seed. New
  entries go into `terms.csv`, imported name lists into `places.csv`; `terms.csv` is read first.
- **Project layer**: `review/glossary.csv` of a GUI project, choices for one book only. It is read before the central
  files, so it wins for the same German word and kind; *Promote to central* moves an entry up.
- **Rejected words**: `rejected.csv` in the central folder; the harvest never proposes them again.
- A project's translation setting *Glossary* left blank means project layer + central glossary
  (`project.glossary_files`); explicit files replace both. `translate.py` without `--glossary` reads the central
  glossary. Writes are atomic.

**Harvest** (`glossary_harvest.py`, `translate.py --harvest FILE`, in the GUI: *Run translation* with "collect glossary
candidates", or *Glossary…* -> *Candidates* -> *Collect candidates…*): every translated unit is shown to the model
once more (German + Polish) with the question which names and art-history terms occur and which Polish form the
translation used (JSON answer). Dropped: what the glossary already has, what was rejected, German words that are not
in the German text. The rest is aggregated over the book (most frequent Polish form, the other forms, the units) into
`review/glossary_candidates.json`; a human accepts each candidate into the central glossary or the book's layer
(status `confirmed`), edits it first, or rejects it. Answers are cached per (model, prompt, German, Polish) beside the
candidates file, so a repeated harvest only asks about changed units.

**Import** of large name lists: *Glossary…* -> *Import name list…* (or `glossary_store.import_pairs_central`):
`german,polish` CSV into the central `places.csv` as `proposed`; a German name with several Polish ones becomes one
`ambiguous` row; names the central glossary already has are left alone (human work wins).

A cached translation stays valid when the glossary grows: the cache key contains only the glossary lines given for
that unit, so only units that match a new or changed entry are translated again.

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
- A review pass over `proposed` entries (the harvest covers new words; existing `proposed` entries are confirmed by
  editing them in the glossary window).
- The OCR-correction vocabulary (`glossary/vocabulary.txt`, German words the dictionaries lack) is still per
  repository, not central.

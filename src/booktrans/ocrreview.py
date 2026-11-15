#!/usr/bin/env python3
"""Stage 5b: LLM-assisted review of the OCR flags that ``ocrfix.py`` could not decide.

    python ocrreview.py propose output/book/ocr_review.json [--provider openai|anthropic] [--model NAME] ...
    python ocrreview.py show    output/book/ocr_review.json              # flags with the model's proposals
    python ocrreview.py accept  output/book/ocr_review.json --min-confidence high
    python ocrfix.py run output/book/document.json                       # applies the decisions

``propose`` takes every open flag of ``ocr_review.json`` (no ``resolution`` yet) and asks the model one question
per flag: the **whole text of the unit** (the auto-corrected ``text`` from ``document.corrected.json``) with the
flagged word marked ``⟦like this⟧`` and the other open flags of the same unit marked ``‹like this›``, the reason
the checker gave, its candidates, the glossary names found in the text, and the instructions of
``prompts/ocr_review_de.txt``.  The model answers with a small JSON object (``replace`` / ``keep`` / ``unsure``,
the replacement, a confidence and a reason).  The answer is checked (valid JSON, sane replacement) and the
replacement is looked up in the dictionaries; the result is stored as ``llm`` in the flag.  A proposal is
only a proposal: it changes nothing until it becomes the flag's ``resolution``, either by ``accept`` (for the
confidence you choose) or by you editing ``ocr_review.json``.  Answers are cached in ``<review>.llm-cache.json``.

The model options are the ones of ``translate.py`` (LM Studio by default, or ``--provider anthropic``).
Every step is a separate function (``collect``, ``build_prompt``, ``parse_answer``, ``check``, ``accept``) so the
prompt, the checks or the whole step are easy to replace.  See ``docs/OCR_CORRECTION.md``.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import glossary as G   # noqa: E402
import log             # noqa: E402
import translate as T  # noqa: E402  (model access: providers, retries, token accounting)

ROOT = HERE.parent.parent
TAG = 'ocrrev'
CONF = {'low': 0, 'medium': 1, 'high': 2}
SEV = {'low': 0, 'medium': 1, 'high': 2}
KIND_HINT = {'heading': 'a chapter heading', 'caption': 'a figure caption', 'footnote_or_footer': 'a footnote',
             'author_byline_candidate': "an author's name line", 'paragraph': 'a paragraph of running text'}
REASON_HINT = {
    'ambiguous': 'several corrections are about equally likely',
    'weak_candidate': 'the best correction found needs several changes',
    'name_risk': 'it could be a name, the checker would have changed it only on weak evidence',
    'short_word': 'it is a short word, too little evidence for an automatic change',
    'no_candidate': 'unknown word, the checker found no correction',
    'unknown_name_or_word': 'unknown capitalised word, perhaps a name',
    'suspect': 'a known word that looks like a typical OCR slip of a much more frequent word',
    'stray_letter': 'a single stray letter',
    'mixed_digits_letters': 'letters mixed with digits',
}


# ---------------------------------------------------------------- tasks
def corrected_offset(unit, raw_offset):
    """Position in the corrected text of a character at ``raw_offset`` in the raw text."""
    return raw_offset + sum(len(c['after']) - len(c['before']) for c in unit.get('ocr_corrections', ())
                            if c['offset'] < raw_offset)


def collect(doc, review, redo=False, min_severity='low', only=None):
    """Open flags that need a proposal: [(flag, unit)] in document order."""
    units = {u['unit_id']: u for u in doc['units']}
    roles = {}
    for p in doc['pages']:
        for b in p['blocks']:
            if b['kind'] == 'text' and b.get('unit_id') and b['unit_id'] not in roles:
                roles[b['unit_id']] = b.get('role')
    out = []
    for f in review.get('flags', []):
        if f.get('resolution') is not None or (f.get('llm') and not redo):
            continue
        if SEV.get(f.get('severity'), 1) < SEV[min_severity] or (only and f['unit'] not in only):
            continue
        u = units.get(f['unit'])
        if u is None:
            continue
        u.setdefault('_role', roles.get(f['unit'], u.get('type')))
        out.append((f, u))
    return out


def mark_text(unit, flag, others):
    """The unit text with the flagged word as ⟦word⟧ and the other open flags as ‹word›."""
    text = unit['text']
    spots = []
    for f in [flag] + others:
        pos = corrected_offset(unit, f['offset'])
        if text[pos:pos + len(f['token'])] != f['token']:           # offsets drifted (text edited): search instead
            pos = text.find(f['token'])
        if pos >= 0:
            spots.append((pos, f['token'], f is flag))
    out, last = [], 0
    for pos, tok, main in sorted(spots):
        if pos < last:
            continue
        out.append(text[last:pos])
        out.append(('⟦%s⟧' if main else '‹%s›') % tok)
        last = pos + len(tok)
    out.append(text[last:])
    return ''.join(out)


def glossary_names(matcher, text):
    names = []
    for e, _ in matcher.find(text):
        names += [e['de']] + [v for v in e['variants'].split(';') if v.strip()]
    return sorted(set(n.strip() for n in names))


def build_prompt(base, unit, flag, others, names=()):
    parts = [base.strip()]
    kind = KIND_HINT.get(unit.get('_role') or unit.get('type'), 'a text')
    parts.append('The text is %s.' % kind)
    parts.append('Text:\n<<<\n%s\n>>>' % mark_text(unit, flag, others))
    why = REASON_HINT.get(flag['reason'], flag['reason'])
    parts.append('The checker flagged ⟦%s⟧ because: %s.' % (flag['token'], why))
    if flag.get('candidates'):
        parts.append('Its suggestions (hints only): ' + ', '.join('%s' % c['text'] for c in flag['candidates']) + '.')
    if names:
        parts.append('Names that are correct in this book and appear in this text: ' + ', '.join(names) + '.')
    return '\n\n'.join(parts)


# ---------------------------------------------------------------- answer
def parse_answer(raw):
    """JSON object from the model's answer -> dict or None."""
    a = re.sub(r'<think>.*?</think>', '', raw or '', flags=re.S)
    m = re.search(r'\{.*\}', a, flags=re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def check(answer, token):
    """(normalised answer, problem or None).  A replacement equal to the word means ``keep``."""
    if not answer:
        return None, 'no JSON object in the answer'
    dec = str(answer.get('decision', '')).strip().lower()
    conf = str(answer.get('confidence', '')).strip().lower()
    rep = str(answer.get('replacement') or '').strip()
    if dec not in ('replace', 'keep', 'unsure'):
        return None, 'decision must be replace, keep or unsure'
    if conf not in CONF:
        conf = 'low'
    if dec == 'replace':
        if not rep:
            return None, 'decision is replace but the replacement is empty'
        if rep == token:
            dec = 'keep'
    if '\n' in rep or len(rep) > 3 * len(token) + 12:
        return None, 'the replacement must be one short phrase without line breaks'
    return {'decision': dec, 'replacement': rep if dec != 'keep' else '', 'confidence': conf,
            'reason': str(answer.get('reason') or '').strip()[:300]}, None


def dictionary_check(lex, text):
    """True when every word of the replacement is known to the dictionaries (None without a lexicon)."""
    if lex is None:
        return None
    words = G.words(text)
    return bool(words) and all(lex.known(w) or any(c.isdigit() for c in w) for w in words)


def propose_one(prompt, call, token, lex=None, retries=1):
    """Ask, parse, check; on a malformed answer ask again with a remark.  -> llm record."""
    remark, last_raw, problem = None, '', None
    for attempt in range(retries + 1):
        raw = call(prompt + ('\n\nIMPORTANT: ' + remark if remark else ''))
        last_raw = raw
        ans, problem = check(parse_answer(raw), token)
        if ans:
            known = dictionary_check(lex, ans['replacement']) if ans['decision'] != 'keep' else None
            ans['in_dictionary'] = known
            if known is False and ans['confidence'] == 'high':
                ans['confidence'] = 'medium'      # a word the dictionaries do not know is never "high"
                ans['reason'] += ' (not in the dictionaries)'
            return ans
        remark = 'Your last answer was not usable (%s). Answer with exactly one JSON object as described.' % problem
    return {'decision': 'unsure', 'replacement': '', 'confidence': 'low', 'in_dictionary': None,
            'reason': 'unusable model answer: %s' % problem, 'raw': last_raw[:300]}


def key_of(model, prompt):
    return hashlib.sha256(json.dumps([model, prompt], ensure_ascii=False).encode('utf-8')).hexdigest()[:24]


# ---------------------------------------------------------------- steps
def propose(doc, review, a, call=None, lex=None, cache=None, save=lambda: None):
    base = T.read(a.prompt)
    gfiles = [Path(g) for g in a.glossary] if a.glossary else sorted((ROOT / 'glossary').glob('*.csv'))
    matcher = G.Matcher(G.load(gfiles)) if gfiles else G.Matcher([])
    cache = {} if cache is None else cache
    tasks = collect(doc, review, a.redo, a.min_severity, set(a.only or []))
    log.info(TAG, '%d flags to ask about' % len(tasks))
    by_unit = {}
    for f in review.get('flags', []):
        if f.get('resolution') is None:
            by_unit.setdefault(f['unit'], []).append(f)
    done = 0
    for n, (flag, unit) in enumerate(tasks, 1):
        others = [f for f in by_unit.get(flag['unit'], []) if f is not flag]
        prompt = build_prompt(base, unit, flag, others, glossary_names(matcher, unit['text']))
        if a.dry_run:
            print('===== %s %s =====\n%s\n' % (flag['unit'], flag['token'], prompt))
            continue
        key = key_of(a.model, prompt)
        if key in cache:
            rec = cache[key]
            log.info(TAG, '[%d/%d] %s "%s" cached' % (n, len(tasks), flag['unit'], flag['token']))
        else:
            log.info(TAG, '[%d/%d] %s "%s" (%s) ...' % (n, len(tasks), flag['unit'], flag['token'], flag['reason']))
            rec = propose_one(prompt, call, flag['token'], lex)
            rec['model'] = a.model
            cache[key] = rec
            save()
        flag['llm'] = rec
        done += 1
        log.info(TAG, '    -> %s %r (%s)  %s' % (rec['decision'], rec['replacement'], rec['confidence'], rec['reason'][:100]))
    return done


def accept(review, min_confidence='high', decisions=('replace', 'keep')):
    """Turn proposals of at least ``min_confidence`` into resolutions.  -> number accepted."""
    n = 0
    for f in review.get('flags', []):
        r = f.get('llm')
        if f.get('resolution') is not None or not r or r['decision'] not in decisions:
            continue
        if CONF.get(r['confidence'], 0) < CONF[min_confidence]:
            continue
        f['resolution'] = r['replacement'] if r['decision'] == 'replace' else '='
        f['resolved_by'] = 'llm'
        n += 1
    return n


def show(review):
    rows = [f for f in review.get('flags', []) if f.get('llm')]
    for f in rows:
        r = f['llm']
        print('%-8s %-24s %-8s -> %-8s %-22r %-6s %s' % (f['unit'], f['token'], f['reason'][:8], r['decision'], r['replacement'],
                                                    r['confidence'], r['reason'][:70]))
    open_ = sum(1 for f in review.get('flags', []) if f.get('resolution') is None)
    print('%d flags with proposals, %d open in total' % (len(rows), open_))


# ---------------------------------------------------------------- CLI
def read_json(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def write_json(p, d):
    Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')


def cmd_propose(a):
    review = read_json(a.review)
    docp = a.document or Path(a.review).with_name('document.corrected.json')
    doc = read_json(docp)
    if a.provider == 'anthropic':
        a.endpoint = a.endpoint or 'https://api.anthropic.com'
        a.api_key = a.api_key or os.environ.get('ANTHROPIC_API_KEY')
        a.model = a.model or 'claude-sonnet-5-5'
        if not a.api_key and not a.dry_run:
            raise SystemExit('no API key: set ANTHROPIC_API_KEY or pass --api-key')
    else:
        a.endpoint = a.endpoint or os.environ.get('LMSTUDIO_ENDPOINT', 'http://localhost:1234/v1')
        a.api_key = a.api_key or os.environ.get('LMSTUDIO_API_KEY')
        if not a.model and not a.dry_run:
            try:
                ms = T.list_models(a.endpoint)
            except (urllib.error.URLError, OSError, ValueError) as e:
                raise SystemExit('cannot reach %s (%s). Is the LM Studio server running?' % (a.endpoint, e))
            if not ms:
                raise SystemExit('the endpoint reports no models: load one in LM Studio')
            a.model = ms[0]
            log.info(TAG, 'model: %s (first reported; choose another with --model)' % a.model)
    a.model = a.model or 'dry-run'
    extra = json.loads(a.extra_body) if a.extra_body else {}
    if a.no_think:
        extra.setdefault('chat_template_kwargs', {'enable_thinking': False})
        extra.setdefault('reasoning_effort', 'none')
    call = lambda prompt: T.chat(a.endpoint, a.model, prompt, a.temperature, a.max_tokens, a.timeout, a.api_key, extra, a.provider)  # noqa: E731
    lex = None
    if not a.no_verify:
        try:
            import ocrfix
            lex = ocrfix.Lexicon(glossary_files=a.glossary or sorted(str(p) for p in (ROOT / 'glossary').glob('*.csv')))
        except ImportError:
            log.info(TAG, 'symspellpy not installed: proposals are not checked against the dictionaries')
    cache_path = Path(str(a.review) + '.llm-cache.json')
    cache = read_json(cache_path) if cache_path.exists() else {}
    n = propose(doc, review, a, call, lex, cache, lambda: write_json(cache_path, cache))
    if not a.dry_run:
        write_json(a.review, review)
        log.info(TAG, '%d proposals stored in %s; next: "ocrreview.py show", then "accept" or edit the file' % (n, a.review))


def cmd_show(a):
    show(read_json(a.review))


def cmd_accept(a):
    review = read_json(a.review)
    n = accept(review, a.min_confidence, ('replace',) if a.replace_only else ('replace', 'keep'))
    write_json(a.review, review)
    log.info(TAG, '%d proposals accepted (confidence >= %s); run "python ocrfix.py run <document.json>" to apply them' % (n, a.min_confidence))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('propose', help='ask the model about every open flag')
    p.add_argument('review', type=Path, help='ocr_review.json')
    p.add_argument('--document', type=Path, help='document.corrected.json (default: next to the review file)')
    p.add_argument('--provider', choices=['openai', 'anthropic'], default='openai')
    p.add_argument('--endpoint'); p.add_argument('--model'); p.add_argument('--api-key')
    p.add_argument('--glossary', nargs='*', type=Path)
    p.add_argument('--prompt', type=Path, default=ROOT / 'prompts' / 'ocr_review_de.txt')
    p.add_argument('--temperature', type=float, default=0.0)
    p.add_argument('--max-tokens', type=int, default=1024)
    p.add_argument('--timeout', type=float, default=600)
    p.add_argument('--no-think', action='store_true')
    p.add_argument('--extra-body')
    p.add_argument('--min-severity', choices=list(SEV), default='low', help='skip flags below this severity')
    p.add_argument('--only', nargs='*', help='only these unit ids')
    p.add_argument('--redo', action='store_true', help='ask again for flags that already have a proposal')
    p.add_argument('--no-verify', action='store_true', help='do not look the replacement up in the dictionaries')
    p.add_argument('--dry-run', action='store_true', help='print the prompts, do not call the model')
    p.set_defaults(fn=cmd_propose)
    p = sub.add_parser('show', help='list the proposals'); p.add_argument('review', type=Path); p.set_defaults(fn=cmd_show)
    p = sub.add_parser('accept', help='turn proposals into resolutions')
    p.add_argument('review', type=Path)
    p.add_argument('--min-confidence', choices=list(CONF), default='high')
    p.add_argument('--replace-only', action='store_true', help='accept only replacements, not "keep"')
    p.set_defaults(fn=cmd_accept)
    a = ap.parse_args()
    os.environ.setdefault('BOOKTRANS_T0', str(time.time()))
    a.fn(a)


if __name__ == '__main__':
    main()

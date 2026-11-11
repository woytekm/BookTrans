#!/usr/bin/env python3
"""Translate the units of a ``document.json`` through an OpenAI-compatible endpoint (LM Studio).

    python translate.py output/book/document.json -o translations.json \\
        [--endpoint http://localhost:1234/v1] [--model NAME] [--glossary FILE ...] [--dry-run]

For every unit with ``translation_status: translate``:

1. the German text is scanned against the glossary (``glossary.py``); the matching ``confirmed`` /
   ``proposed`` entries become a short list in the prompt,
2. one request is sent: your base prompt (``prompts/translate_pl.txt``), the rules
   (``prompts/translate_rules_pl.txt``), the glossary list, the end of the preceding unit as context, the
   kind of text (heading, caption ...) and the text,
3. the answer is cleaned and checked (glossary terms present, length ratio, digits); on a failed check the
   request is repeated once or twice with a remark naming the problem,
4. the result goes to ``translations.json`` (the format ``render.py`` reads, plus ``glossary`` and ``flags``
   per unit for review).

Answers are cached in ``<output>.cache.json`` (key: model, prompts, glossary list, context, text), so an
interrupted or repeated run only translates what changed.  ``--dry-run`` writes the prompts to
``<output>.prompts.txt`` without calling the model.  Standard library only.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import glossary as G   # noqa: E402
import log             # noqa: E402

ROOT = HERE.parent.parent
KIND_HINT = {
    'heading': 'a chapter heading (short; translate it as a heading, not as a sentence)',
    'caption': 'a figure caption (short; keep the figure number and the order of the parts)',
    'author_byline_candidate': "an author's name line (keep the name unchanged)",
    'footnote_or_footer': 'a footnote (keep it short and in the style of a footnote)',
    'paragraph': 'a paragraph of running text',
}


# ---------------------------------------------------------------- prompt
def read(path):
    return Path(path).read_text(encoding='utf-8').strip()


def last_sentence(text, limit=300):
    t = text.strip()
    parts = re.split(r'(?<=[.!?])\s+', t)
    s = parts[-1] if parts else t
    return s[-limit:]


def build_prompt(base, rules, glossary_block, kind, upper, text, context=None, remark=None):
    parts = [base, rules]
    if glossary_block:
        parts.append(glossary_block)
    hint = KIND_HINT.get(kind, 'a text')
    if upper:
        hint += '; it is set in capitals in the original: answer in normal upper/lower case'
    parts.append('This text is %s.' % hint)
    if context:
        parts.append('For continuity, the preceding text of the book ended with this sentence (do not translate it):\n'
                     '"%s"' % context)
    if remark:
        parts.append('IMPORTANT: ' + remark)
    parts.append('Text to translate:\n<<<\n%s\n>>>' % text)
    return '\n\n'.join(parts)


def clean(answer):
    a = re.sub(r'<think>.*?</think>', '', answer, flags=re.S).strip()
    a = re.sub(r'^```[a-z]*\n|\n```$', '', a).strip()
    a = re.sub(r'^(?:Tłumaczenie|Translation|Oto tłumaczenie)[^:\n]*:\s*', '', a, flags=re.I).strip()
    m = re.match(r'^<<<\s*(.*?)\s*>>>$', a, flags=re.S)
    if m:
        a = m.group(1)
    if len(a) > 2 and a[0] == a[-1] == '"' or (a[:1] == '„' and a[-1:] == '”'):
        a = a[1:-1].strip()
    return a


# ---------------------------------------------------------------- model
def chat(endpoint, model, prompt, temperature, max_tokens, timeout, api_key=None):
    body = {'model': model, 'temperature': temperature, 'max_tokens': max_tokens, 'stream': False,
            'messages': [{'role': 'user', 'content': prompt}]}        # one user message: works with every chat template
    req = urllib.request.Request(endpoint.rstrip('/') + '/chat/completions', json.dumps(body).encode('utf-8'),
                                 {'Content-Type': 'application/json',
                                  'Authorization': 'Bearer ' + (api_key or 'lm-studio')})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))['choices'][0]['message']['content']


def list_models(endpoint, timeout=15):
    with urllib.request.urlopen(endpoint.rstrip('/') + '/models', timeout=timeout) as r:
        return [m['id'] for m in json.loads(r.read().decode('utf-8'))['data']]


# ---------------------------------------------------------------- checks
def digits(s):
    return sorted(re.findall(r'\d+', s))


def problems(src, pl, hits):
    """(problems found, remark for a retry or None).  Digit differences are reported only: OCR often
    misreads digits in the source, so a retry would not help."""
    found, fixes = [], []
    miss = G.check(pl, hits)
    if miss:
        found.append('glossary: ' + ', '.join('%s (%s)' % (e['pl'], e['de']) for e in miss))
        fixes.append('Use the glossary forms: ' + ', '.join('%s for %s' % (e['pl'], e['de']) for e in miss) + '.')
    if not pl.strip():
        found.append('empty answer'); fixes.append('Answer with the Polish translation.')
    else:
        ratio = len(pl) / max(1, len(src))
        if len(src) > 60 and not 0.65 <= ratio <= 1.45:
            found.append('length ratio %.2f' % ratio)
            fixes.append('Your answer is %s than the original; translate the whole text, neither shortening nor '
                         'adding to it.' % ('shorter' if ratio < 1 else 'longer'))
    if digits(src) != digits(pl):
        found.append('digits differ (%s vs %s)' % (' '.join(digits(src)), ' '.join(digits(pl))))
    return found, (' '.join(fixes) if fixes else None)


# ---------------------------------------------------------------- units
def collect(doc):
    """Units to translate, in document order: (unit_id, text, role, uppercase, status)."""
    info = {}
    for p in doc['pages']:
        for b in p['blocks']:
            if b['kind'] == 'text' and b.get('unit_id') and b['unit_id'] not in info:
                info[b['unit_id']] = (b['role'], b['typography'].get('uppercase_ratio', 0) >= 0.6, b.get('translation_status'))
    out = []
    for u in doc['units']:
        role, upper, status = info.get(u['unit_id'], (u.get('type'), False, 'translate'))
        out.append((u['unit_id'], u['text'], role, upper, status))
    return out


def key_of(model, base, rules, block, kind, upper, text, context):
    h = hashlib.sha256('\x1f'.join([model, base, rules, block, kind, str(upper), text, context or '']).encode('utf-8'))
    return h.hexdigest()


def run(doc, a, call=None):
    base, rules = read(a.prompt), read(a.rules)
    gfiles = [Path(g) for g in a.glossary] if a.glossary else sorted((ROOT / 'glossary').glob('*.csv'))
    matcher = G.Matcher(G.load(gfiles))
    log.info('translate', 'glossary: %d entries from %s' % (len(matcher.entries), ', '.join(f.name for f in gfiles) or '-'))
    cache_path = a.output.with_suffix('.cache.json')
    cache = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    call = call or (lambda prompt: chat(a.endpoint, a.model, prompt, a.temperature, a.max_tokens, a.timeout, a.api_key))
    units = collect(doc)
    todo = [u for u in units if u[4] == 'translate' and (not a.only or u[0] in a.only)]
    skipped = [u[0] for u in units if u[4] != 'translate']
    log.info('translate', '%d units to translate, %d skipped (%s)' % (len(todo), len(skipped), 'excluded or under review' if skipped else '-'))
    result = {'schema': '1.0', 'target_language': 'pl', 'prompt': base, 'model': a.model, 'notes': [], 'units': {}}
    prompts_txt = []
    prev_by_role = {}
    t0 = time.time()
    for n, (uid, text, role, upper, status) in enumerate(todo, 1):
        hits = matcher.find(text)
        block = G.prompt_block(hits)
        ctx = last_sentence(prev_by_role[role]) if role == 'paragraph' and role in prev_by_role else None
        prev_by_role[role] = text
        prompt = build_prompt(base, rules, block, role, upper, text, ctx)
        entry = {'source': text, 'pl': '', 'glossary': [e['de'] + ' = ' + e['pl'] for e, _ in hits if e['status'] in G.USABLE],
                 'flags': []}
        review = [e['de'] + ' (' + e['status'] + ')' for e, _ in hits if e['status'] not in G.USABLE]
        if review:
            entry['flags'].append('glossary entries not given to the model: ' + ', '.join(review))
        if a.dry_run:
            prompts_txt.append('===== %s (%s) =====\n%s\n' % (uid, role, prompt))
            continue
        key = key_of(a.model, base, rules, block, role, upper, text, ctx)
        if key in cache:
            entry['pl'] = cache[key]['pl']; entry['flags'] += cache[key].get('flags', [])
            log.info('translate', '[%d/%d] %s cached' % (n, len(todo), uid))
        else:
            remark, flags = None, []
            for attempt in range(a.retries + 1):
                try:
                    answer = clean(call(build_prompt(base, rules, block, role, upper, text, ctx, remark)))
                except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
                    raise SystemExit('request to %s failed: %s' % (a.endpoint, e))
                flags, remark = problems(text, answer, hits)
                entry['pl'] = answer
                if not remark:
                    break
                log.info('translate', '%s attempt %d: %s' % (uid, attempt + 1, '; '.join(flags)))
            entry['flags'] += flags
            cache[key] = {'pl': entry['pl'], 'flags': flags}
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding='utf-8')
            log.info('translate', '[%d/%d] %s %s: %d -> %d chars%s  (%.0fs)' % (
                n, len(todo), uid, role, len(text), len(entry['pl']), '  FLAGS: ' + '; '.join(flags) if flags else '', time.time() - t0))
        result['units'][uid] = entry
        if entry['flags']:
            result['notes'].append('%s: %s' % (uid, '; '.join(entry['flags'])))
        a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if a.dry_run:
        p = a.output.with_suffix('.prompts.txt')
        p.write_text('\n'.join(prompts_txt), encoding='utf-8')
        log.info('translate', 'dry run: %d prompts written to %s' % (len(prompts_txt), p))
    else:
        a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        log.info('translate', 'wrote %s (%d units, %d with flags)' % (a.output, len(result['units']), sum(bool(u['flags']) for u in result['units'].values())))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('document', type=Path, help='document.json')
    ap.add_argument('-o', '--output', type=Path, default=None, help='translations JSON (default: next to document.json)')
    ap.add_argument('--endpoint', default=os.environ.get('LMSTUDIO_ENDPOINT', 'http://localhost:1234/v1'),
                    help='OpenAI-compatible base URL (LM Studio: http://localhost:1234/v1; default or $LMSTUDIO_ENDPOINT)')
    ap.add_argument('--model', default=None, help="model id as LM Studio lists it (default: the first model the endpoint reports)")
    ap.add_argument('--api-key', default=os.environ.get('LMSTUDIO_API_KEY'))
    ap.add_argument('--glossary', nargs='*', type=Path, help='glossary CSV files (default: glossary/*.csv)')
    ap.add_argument('--prompt', type=Path, default=ROOT / 'prompts' / 'translate_pl.txt')
    ap.add_argument('--rules', type=Path, default=ROOT / 'prompts' / 'translate_rules_pl.txt')
    ap.add_argument('--temperature', type=float, default=0.2)
    ap.add_argument('--max-tokens', type=int, default=4096)
    ap.add_argument('--timeout', type=float, default=600, help='seconds per request')
    ap.add_argument('--retries', type=int, default=2, help='extra attempts when a check fails (default 2)')
    ap.add_argument('--only', nargs='*', help='translate only these unit ids')
    ap.add_argument('--dry-run', action='store_true', help='write the prompts, do not call the model')
    a = ap.parse_args()
    os.environ.setdefault('BOOKTRANS_T0', str(time.time()))
    doc = json.loads(a.document.read_text(encoding='utf-8'))
    a.output = a.output or a.document.parent / 'translations.json'
    if not a.dry_run and not a.model:
        try:
            ms = list_models(a.endpoint)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise SystemExit('cannot reach %s (%s). Is the LM Studio server running (Developer tab, "Start Server")?' % (a.endpoint, e))
        if not ms:
            raise SystemExit('the endpoint reports no models: load one in LM Studio')
        a.model = ms[0]
        log.info('translate', 'model: %s (first reported; choose another with --model)' % a.model)
    a.model = a.model or 'dry-run'
    run(doc, a)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Translate the units of a ``document.json`` through an OpenAI-compatible endpoint (LM Studio), the
Anthropic API or the Claude Code CLI (``--provider claude-code``: your Claude subscription, no API key).

    python translate.py output/book/document.json -o translations.json \\
        [--endpoint http://localhost:1234/v1] [--model NAME] [--glossary FILE ...] [--dry-run]
    python translate.py output/book/document.json --provider claude-code [--model opus|sonnet]

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

Answers are cached in ``<output>.cache.json`` (key: model, prompts, glossary list, context, text; each answer also
keeps short hashes of these parts, so a unit translated anew is reported with what changed), so an
interrupted or repeated run only translates what changed; ``--force`` asks the model again for the selected units.  ``--dry-run`` writes the prompts to
``<output>.prompts.txt`` without calling the model.  Standard library only.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
USAGE = {'calls': 0, 'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'seconds': 0.0, 'reported_cost': 0.0}


def post_json(url, headers, body, timeout, tries=4):
    """POST JSON, parse the JSON answer. Rate limits and server overload (429, 5xx, 529) are retried with
    a growing pause; other HTTP errors raise RuntimeError carrying the server's message."""
    data = json.dumps(body).encode('utf-8')
    for k in range(tries):
        req = urllib.request.Request(url, data, {'Content-Type': 'application/json', **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            msg = e.read().decode('utf-8', 'replace')[:500]
            if e.code in (429, 500, 502, 503, 504, 529) and k < tries - 1:
                wait = 5 * 2 ** k
                log.info('translate', '  HTTP %d (%s); retrying in %ds' % (e.code, msg[:120].replace('\n', ' '), wait))
                time.sleep(wait)
                continue
            raise RuntimeError('HTTP %d from %s: %s' % (e.code, url, msg))


def _note_usage(dt, inp, out, cache_read=0, cache_write=0):
    USAGE['calls'] += 1; USAGE['seconds'] += dt
    USAGE['input'] += inp or 0; USAGE['output'] += out or 0
    USAGE['cache_read'] += cache_read or 0; USAGE['cache_write'] += cache_write or 0


def chat_openai(endpoint, model, prompt, temperature, max_tokens, timeout, api_key=None, extra=None):
    body = {'model': model, 'temperature': temperature, 'max_tokens': max_tokens, 'stream': False,
            'messages': [{'role': 'user', 'content': prompt}]}        # one user message: works with every chat template
    body.update(extra or {})
    t0 = time.time()
    data = post_json(endpoint.rstrip('/') + '/chat/completions',
                     {'Authorization': 'Bearer ' + (api_key or 'lm-studio')}, body, timeout)
    dt = time.time() - t0
    choice = data['choices'][0]
    msg = choice['message']
    content = msg.get('content') or ''
    use = data.get('usage') or {}
    pt, ct = use.get('prompt_tokens'), use.get('completion_tokens')
    _note_usage(dt, pt, ct)
    reasoning = msg.get('reasoning_content') or msg.get('reasoning') or ''
    log.info('translate', '  model call: %.1fs, prompt %s tok, answer %s tok%s, finish=%s%s' % (
        dt, pt if pt is not None else '?', ct if ct is not None else '?',
        (' (%.1f tok/s)' % (ct / dt)) if ct and dt > 0 else '', choice.get('finish_reason'),
        ', %d chars of hidden reasoning' % len(reasoning) if reasoning else ''))
    if not content.strip():
        if reasoning:
            log.info('translate', '  empty answer but the model produced reasoning text: it is a "thinking" model that spent its '
                                  'tokens on reasoning; disable thinking for it or use a non-reasoning model')
        elif choice.get('finish_reason') == 'length':
            log.info('translate', '  empty answer, cut off by the token limit (max_tokens %d or the context length set in LM Studio)' % max_tokens)
    return content


def chat_anthropic(endpoint, model, prompt, temperature, max_tokens, timeout, api_key=None, extra=None):
    """Anthropic Messages API (``POST /v1/messages``); the answer carries exact token counts."""
    if not api_key:
        raise RuntimeError('no API key: set ANTHROPIC_API_KEY or pass --api-key')
    body = {'model': model, 'max_tokens': max_tokens, 'messages': [{'role': 'user', 'content': prompt}]}
    if temperature is not None and temperature >= 0:
        body['temperature'] = temperature
    body.update(extra or {})
    t0 = time.time()
    data = post_json(endpoint.rstrip('/') + '/v1/messages',
                     {'x-api-key': api_key, 'anthropic-version': '2023-06-01'}, body, timeout)
    dt = time.time() - t0
    content = ''.join(b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text')
    use = data.get('usage') or {}
    inp, out = use.get('input_tokens'), use.get('output_tokens')
    _note_usage(dt, inp, out, use.get('cache_read_input_tokens'), use.get('cache_creation_input_tokens'))
    log.info('translate', '  model call: %.1fs, input %s tok, output %s tok, stop=%s' % (
        dt, inp if inp is not None else '?', out if out is not None else '?', data.get('stop_reason')))
    if data.get('stop_reason') == 'max_tokens':
        log.info('translate', '  answer cut off by --max-tokens (%d)' % max_tokens)
    return content


# ---------------------------------------------------------------- Claude Code CLI
CLAUDE_SYSTEM = ('You are a translator working inside a batch script. You have no tools and need none. '
                 'Reply with exactly what the user message asks for and nothing else: no greeting, no comments, '
                 'no Markdown, no questions.')
_RETRYABLE = re.compile(r'overloaded|\b529\b|\b50[0234]\b|internal server error|api error: 5|timed? ?out|econnreset|network', re.I)
_LIMIT = re.compile(r'usage limit|limit reached|rate.?limit|\b429\b|quota', re.I)
_AUTH = re.compile(r'authenticat|not logged in|/login|invalid api key|oauth|credential|\b401\b|\b403\b', re.I)


def classify_claude_error(msg, status=None):
    """'auth' | 'limit' | 'retry' | 'fatal' for an error text of the CLI (and its ``api_error_status``).  Order matters:
    the CLI words an authentication failure as "... may be a temporary network issue", which must not be retried."""
    if status in (401, 403) or _AUTH.search(msg):
        return 'auth'
    if status == 429 or _LIMIT.search(msg):
        return 'limit'
    if (isinstance(status, int) and status >= 500) or _RETRYABLE.search(msg):
        return 'retry'
    return 'fatal'


class UsageLimit(RuntimeError):
    """The subscription's usage limit was hit: stop the run (the cache keeps what is done)."""


def find_claude(explicit=None):
    """The Claude Code program: --claude-bin, $CLAUDE_BIN, ``claude`` on PATH, ~/.local/bin/claude (the native installer)."""
    for c in (explicit, os.environ.get('CLAUDE_BIN'), shutil.which('claude'), str(Path.home() / '.local' / 'bin' / 'claude')):
        if c and Path(c).expanduser().is_file():
            return str(Path(c).expanduser())
    return None


def claude_command(claude_bin, model, extra_args=None):
    """Headless one-shot call: the prompt comes on stdin, one JSON result object on stdout.  The system prompt replaces
    Claude Code's own (coding assistant), ``--tools ""`` offers no tool, ``--strict-mcp-config`` skips your MCP servers and
    ``--no-session-persistence`` keeps thousands of one-shot sessions out of ~/.claude.  (Not ``--bare``: it accepts only
    an API key, never the subscription login.)  Flags checked against Claude Code 2.1.326."""
    cmd = [claude_bin, '-p', '--output-format', 'json', '--model', model, '--system-prompt', CLAUDE_SYSTEM,
           '--tools', '', '--strict-mcp-config', '--no-session-persistence']
    return cmd + list(extra_args or [])


def claude_env(keep_api_key=False):
    """Environment for the CLI.  ANTHROPIC_API_KEY would make Claude Code bill the API instead of the subscription,
    so it is removed unless --claude-use-api-key is given."""
    env = dict(os.environ)
    if not keep_api_key:
        env.pop('ANTHROPIC_API_KEY', None)
    return env


def parse_claude_result(stdout):
    """The JSON object ``claude -p --output-format json`` prints (the last JSON line, in case anything precedes it)."""
    for line in reversed([ln for ln in stdout.strip().splitlines() if ln.strip()]):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, list):                     # some versions print the whole message list
            data = next((d for d in reversed(data) if isinstance(d, dict) and d.get('type') == 'result'), {})
        if isinstance(data, dict):
            return data
    try:
        return json.loads(stdout)
    except ValueError:
        raise RuntimeError('Claude Code printed no JSON result: %s' % stdout.strip()[:300])


def chat_claude_code(claude_bin, model, prompt, timeout, extra_args=None, keep_api_key=False, tries=4, run=subprocess.run,
                     sleep=None):
    sleep = sleep or time.sleep
    cmd = claude_command(claude_bin, model, extra_args)
    for k in range(tries):
        t0 = time.time()
        try:
            with tempfile.TemporaryDirectory(prefix='booktrans-claude-') as cwd:   # an empty folder: no project files, no CLAUDE.md
                p = run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=claude_env(keep_api_key))
        except subprocess.TimeoutExpired:
            raise RuntimeError('Claude Code did not answer within %.0fs (--timeout)' % timeout)
        dt = time.time() - t0
        out, err = p.stdout or '', (p.stderr or '').strip()
        try:
            data = parse_claude_result(out)
        except RuntimeError:
            if out.strip():
                raise
            data = {'is_error': True, 'result': err or 'exit code %d, no output' % p.returncode}
        text = data.get('result') or ''
        if data.get('is_error') or data.get('subtype') not in (None, 'success'):
            msg = (text or data.get('subtype') or err or 'error').strip()
            kind = classify_claude_error(msg, data.get('api_error_status'))
            if kind == 'auth':
                raise RuntimeError('Claude Code is not logged in (%s). Run "claude" once in a terminal, use /login with your '
                                   'Claude account, then run this again.' % msg[:200])
            if kind == 'limit':
                raise UsageLimit(msg[:300])
            if kind == 'retry' and k < tries - 1:
                log.info('translate', '  Claude Code: %s; retrying in %ds' % (msg[:120], 10 * 2 ** k)); sleep(10 * 2 ** k); continue
            raise RuntimeError('Claude Code: %s' % msg[:300])
        use = data.get('usage') or {}
        inp, outt = use.get('input_tokens'), use.get('output_tokens')
        _note_usage(dt, inp, outt, use.get('cache_read_input_tokens'), use.get('cache_creation_input_tokens'))
        cost = data.get('total_cost_usd', data.get('cost_usd'))
        if isinstance(cost, (int, float)):
            USAGE['reported_cost'] += cost
        log.info('translate', '  model call (claude-code %s): %.1fs, input %s tok (+%s cached), output %s tok%s' % (
            model, dt, inp if inp is not None else '?', use.get('cache_read_input_tokens') or 0,
            outt if outt is not None else '?', (', notional cost %.4f USD' % cost) if isinstance(cost, (int, float)) else ''))
        return text
    raise RuntimeError('Claude Code: giving up after %d attempts' % tries)


def chat(endpoint, model, prompt, temperature, max_tokens, timeout, api_key=None, extra=None, provider='openai', a=None):
    if provider == 'claude-code':          # temperature / max_tokens: the CLI has no such options
        return chat_claude_code(endpoint, model, prompt, timeout, getattr(a, 'claude_args', None),
                                getattr(a, 'claude_use_api_key', False))
    fn = chat_anthropic if provider == 'anthropic' else chat_openai
    return fn(endpoint, model, prompt, temperature, max_tokens, timeout, api_key, extra)


def list_models(endpoint, timeout=15):
    with urllib.request.urlopen(endpoint.rstrip('/') + '/models', timeout=timeout) as r:
        return [m['id'] for m in json.loads(r.read().decode('utf-8'))['data']]


# ---------------------------------------------------------------- checks
_SUP = str.maketrans('\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u2070', '1234567890'[:9] + '0')
_ROMAN = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}


def _roman(m):
    t = m.group(0); n = 0
    for i, c in enumerate(t):
        v = _ROMAN[c]
        n += -v if i + 1 < len(t) and _ROMAN[t[i + 1]] > v else v
    return str(n)


def digits(s):
    """Sorted numbers in ``s`` for comparing source and translation: superscript digits (footnote marks) count
    as digits and Roman numerals of two or more letters are converted ("XIII wieku" = "13. Jahrhunderts")."""
    s = s.translate(_SUP)
    s = re.sub(r'\b[IVXLCDM]{2,}\b', _roman, s)
    return sorted(re.findall(r'\d+', s))


# Punctuation the rules forbid (prompts/translate_rules_pl.txt): found in an answer they make a retry with this remark.
PUNCT_RULES = [
    ('comma and dash together', re.compile(r'[,;:]\s*[-–—](?=\s)|(?<=\s)[-–—]\s*[,;:]')),
    ('long dash', re.compile(r'—')),
    ('spaced hyphen as a dash', re.compile(r'(?<=\s)-(?=\s)')),
    ('two punctuation marks together', re.compile(r'(?<![A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ]\.)[,;:!?]\s*[,;:]|[,;:]\s*\.(?!\.)')),
]
PUNCT_FIX = {
    'comma and dash together': 'set off an inserted phrase either with dashes or with commas, never both',
    'long dash': 'use the short dash "–" instead of the long dash "—"',
    'spaced hyphen as a dash': 'a dash between words is "–" with spaces, not a hyphen "-"',
    'two punctuation marks together': 'never put two punctuation marks next to each other',
}


def tag_problems(pl):
    """Markup the rules do not allow: only ``<i>…</i>`` (titles of cited works), balanced and not nested; no Markdown
    emphasis.  -> a short description or ''."""
    depth = 0
    for m in re.finditer(r'</?([a-zA-Z]+)[^>]*>', pl):
        if m.group(1).lower() != 'i':
            return 'tag <%s>' % m.group(1)
        depth += -1 if m.group(0).startswith('</') else 1
        if depth not in (0, 1):
            return 'unbalanced <i>'
    if depth:
        return 'unbalanced <i>'
    if re.search(r'(?<![*\w])\*{1,2}[^*\s][^*]*\*{1,2}(?![*\w])|(?<![\w_])_[^_\s][^_]*_(?![\w_])', pl):
        return 'Markdown emphasis'
    return ''


def punctuation_problems(pl):
    """The punctuation rules an answer breaks (names from ``PUNCT_RULES``)."""
    return [name for name, rx in PUNCT_RULES if rx.search(pl)]


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
    tags = tag_problems(pl)
    if tags:
        found.append('markup: ' + tags)
        fixes.append('Use <i>...</i> only around titles of cited works, every <i> closed by </i>, and no other markup.')
    bad = punctuation_problems(pl)
    if bad:
        found.append('punctuation: ' + ', '.join(bad))
        fixes.append('Fix the punctuation: ' + '; '.join(PUNCT_FIX[k] for k in bad) + '.')
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


def report_usage(a):
    """Token totals of this run (cached units cost nothing); a cost when --price-in/--price-out are given."""
    u = dict(USAGE)
    u['seconds'] = round(u['seconds'], 1)
    u['reported_cost'] = round(u['reported_cost'], 4)
    if not u['reported_cost']:
        del u['reported_cost']
    pin, pout = getattr(a, 'price_in', None), getattr(a, 'price_out', None)
    if pin is not None and pout is not None:
        u['cost'] = round(u['input'] * pin / 1e6 + u['output'] * pout / 1e6 + u['cache_read'] * pin * 0.1 / 1e6
                          + u['cache_write'] * pin * 1.25 / 1e6, 4)
    log.info('translate', 'usage this run: %d requests, %d input + %d output tokens%s%s, %.0fs' % (
        u['calls'], u['input'], u['output'],
        (' (cache: %d read, %d written)' % (u['cache_read'], u['cache_write'])) if u['cache_read'] or u['cache_write'] else '',
        (', cost %.4f' % u['cost']) if 'cost' in u else '', u['seconds']))
    if 'reported_cost' in u:
        log.info('translate', 'Claude Code reports %.4f USD for this run: the API price of the tokens; on a Pro/Max subscription '
                              'it is counted against the plan\'s usage limits, not billed' % u['reported_cost'])
    return u


def key_of(model, base, rules, block, kind, upper, text, context):
    h = hashlib.sha256('\x1f'.join([model, base, rules, block, kind, str(upper), text, context or '']).encode('utf-8'))
    return h.hexdigest()


PART_NAMES = {'model': 'the model', 'base': 'the translation prompt', 'rules': 'the translation rules',
              'block': 'the glossary lines for the unit', 'kind': 'the kind of text', 'upper': 'the capitals flag',
              'context': 'the preceding sentence (context)'}


def key_parts(model, base, rules, block, kind, upper, text, context):
    """Short hashes of what a cache key is made of, stored with each answer: a later miss can then say what changed."""
    sh = lambda s: hashlib.sha1(str(s).encode('utf-8')).hexdigest()[:10]
    return {'model': sh(model), 'base': sh(base), 'rules': sh(rules), 'block': sh(block), 'kind': sh(kind),
            'upper': sh(upper), 'text': sh(text), 'context': sh(context or '')}


def miss_reason(parts, by_text):
    """Why a unit's answer is not in the cache: what differs from the cached answer(s) for the same German text."""
    olds = by_text.get(parts['text'], [])
    if not olds:
        return 'new text (no earlier answer for this German text)'
    best = min(olds, key=lambda o: sum(o[k] != parts[k] for k in PART_NAMES))
    diff = [PART_NAMES[k] for k in PART_NAMES if best.get(k) != parts[k]]
    return 'changed: ' + ', '.join(diff) if diff else 'answer was empty'


def glossary_paths(a):
    """The glossary files of a run: ``--glossary`` as given, else the central glossary shared by all books."""
    if a.glossary:
        return [Path(g) for g in a.glossary]
    import glossary_store
    return glossary_store.central_files()


def make_call(a):
    """prompt -> answer with the run's provider, model and limits."""
    extra = json.loads(a.extra_body) if getattr(a, 'extra_body', None) else {}
    if getattr(a, 'no_think', False):                  # servers ignore what they do not know
        extra.setdefault('chat_template_kwargs', {'enable_thinking': False})
        extra.setdefault('reasoning_effort', 'none')
    provider = getattr(a, 'provider', 'openai')
    return lambda prompt: chat(a.endpoint, a.model, prompt, a.temperature, a.max_tokens, a.timeout, a.api_key, extra, provider, a)


def harvest(doc, a, call=None):
    """``--harvest FILE``: glossary candidates from the translation in ``a.output`` (``glossary_harvest.py``) -> FILE.
    -> the candidates."""
    import glossary_harvest as H
    import glossary_store
    gfiles = glossary_paths(a)
    entries = G.load(gfiles)
    log.info('translate', 'glossary harvest: %d glossary entries from %s' % (len(entries), ', '.join(str(f) for f in gfiles) or '-'))
    try:
        trans = json.loads(a.output.read_text(encoding='utf-8')).get('units', {})
    except (OSError, ValueError) as e:
        raise SystemExit('no translation to harvest from (%s): %s' % (a.output, e))
    units = [(uid, text) for uid, text, role, upper, status in collect(doc) if status == 'translate'
             and (not a.only or uid in a.only)]
    cache_path = a.harvest.with_suffix('.cache.json')
    cache = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    for k in USAGE:
        USAGE[k] = 0.0 if k in ('seconds', 'reported_cost') else 0
    call = call or make_call(a)
    try:
        cands, asked = H.harvest(units, trans, entries, call, a.model, cache, glossary_store.rejected(),
                                 log=lambda m: log.info('translate', m))
    finally:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding='utf-8')
    H.write_candidates(a.harvest, cands, a.document)
    report_usage(a)
    log.info('translate', 'glossary harvest: %d units asked (%d from the cache), %d candidates -> %s' % (
        asked, len(units) - asked, len(cands), a.harvest))
    return cands


def run(doc, a, call=None):
    base, rules = read(a.prompt), read(a.rules)
    gfiles = glossary_paths(a)
    matcher = G.Matcher(G.load(gfiles))
    log.info('translate', 'glossary: %d entries from %s' % (len(matcher.entries), ', '.join(str(f) for f in gfiles) or '-'))
    cache_path = a.output.with_suffix('.cache.json')
    cache = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    call = call or make_call(a)
    meta = cache.get('_meta') or {}
    for name, now in (('base', base), ('rules', rules), ('model', a.model)):
        h = hashlib.sha1(str(now).encode('utf-8')).hexdigest()[:10]
        if meta.get(name) and meta[name] != h:
            log.info('translate', 'NOTE: %s changed since the last run: the cache keys include it, so every unit is '
                                  'translated again' % PART_NAMES[name])
    by_text = {}
    for k, v in cache.items():
        if k != '_meta' and isinstance(v, dict) and v.get('parts'):
            by_text.setdefault(v['parts']['text'], []).append(v['parts'])
    misses = {}
    for k in USAGE:
        USAGE[k] = 0.0 if k in ('seconds', 'reported_cost') else 0
    units = collect(doc)
    every = [u for u in units if u[4] == 'translate']
    todo = [u for u in every if not a.only or u[0] in a.only]
    skipped = [u[0] for u in units if u[4] != 'translate']
    log.info('translate', '%d units to translate, %d skipped (%s)' % (len(todo), len(skipped), 'excluded or under review' if skipped else '-'))
    result = {'schema': '1.0', 'target_language': 'pl', 'prompt': base, 'model': a.model, 'notes': [], 'units': {}}
    kept = {}
    if a.only and not a.dry_run and a.output.exists():
        # a partial run (--only / --pages) updates the selected units and keeps the others from the previous output
        try:
            kept = json.loads(a.output.read_text(encoding='utf-8')).get('units', {})
        except (OSError, ValueError):
            kept = {}
        kept = {k: v for k, v in kept.items() if k not in a.only}
        if kept:
            log.info('translate', 'partial run: %d other units kept from %s' % (len(kept), a.output))
    order = [u[0] for u in units]

    def save():
        merged = dict(kept)
        merged.update(result['units'])
        out = dict(result)
        out['units'] = {k: merged[k] for k in order if k in merged}
        out['units'].update({k: v for k, v in merged.items() if k not in out['units']})
        out['notes'] = ['%s: %s' % (k, '; '.join(v['flags'])) for k, v in out['units'].items() if v.get('flags')]
        a.output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        return out

    prompts_txt = []
    prev_by_role = {}
    t0 = time.time()
    n = 0
    for uid, text, role, upper, status in every:
        # the context is the preceding unit of the whole book, also in a partial run: same prompt, same cache key
        ctx = last_sentence(prev_by_role[role]) if role == 'paragraph' and role in prev_by_role else None
        prev_by_role[role] = text
        if a.only and uid not in a.only:
            continue
        n += 1
        if not text.strip():                            # emptied by a human (joined into another unit): nothing to translate
            result['units'][uid] = {'source': '', 'pl': '', 'glossary': [], 'flags': []}
            log.info('translate', '[%d/%d] %s empty (joined into another unit by a human): skipped' % (n, len(todo), uid))
            continue
        hits = matcher.find(text)
        block = G.prompt_block(hits)
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
        parts = key_parts(a.model, base, rules, block, role, upper, text, ctx)
        if key in cache and cache[key].get('pl', '').strip() and not getattr(a, 'force', False):   # an empty answer is never reused
            entry['pl'] = cache[key]['pl']; entry['flags'] += cache[key].get('flags', [])
            log.info('translate', '[%d/%d] %s cached' % (n, len(todo), uid))
        else:
            remark, flags = None, []
            if getattr(a, 'force', False):
                why = '--force'
            elif by_text:
                why = miss_reason(parts, by_text)
            elif any(k != '_meta' for k in cache):
                why = 'the cached answers are older than this check (cache_explain.py can tell what changed)'
            else:
                why = 'new text (empty cache)'
            misses[why] = misses.get(why, 0) + 1
            log.info('translate', '[%d/%d] %s %s: sending %d chars%s (not cached: %s; waiting for the model ...)' % (
                n, len(todo), uid, role, len(text), ', cached answer ignored (--force)' if key in cache and getattr(a, 'force', False) else '',
                why))
            for attempt in range(a.retries + 1):
                try:
                    answer = clean(call(build_prompt(base, rules, block, role, upper, text, ctx, remark)))
                except UsageLimit as e:
                    save()
                    raise SystemExit('usage limit reached at %s (%d of %d units done this run): %s\n'
                                     'Everything translated so far is cached; run the same command again when the limit resets.'
                                     % (uid, n - 1, len(todo), e))
                except (urllib.error.URLError, OSError, KeyError, ValueError, RuntimeError) as e:
                    raise SystemExit('request to %s failed: %s' % (a.endpoint, e))
                flags, remark = problems(text, answer, hits)
                entry['pl'] = answer
                if not remark:
                    break
                log.info('translate', '%s attempt %d: %s' % (uid, attempt + 1, '; '.join(flags)))
            entry['flags'] += flags
            if entry['pl'].strip():
                cache[key] = {'pl': entry['pl'], 'flags': flags, 'parts': parts}
                cache['_meta'] = {name: hashlib.sha1(str(v).encode('utf-8')).hexdigest()[:10]
                                  for name, v in (('base', base), ('rules', rules), ('model', a.model))}
                cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding='utf-8')
            log.info('translate', '[%d/%d] %s %s: %d -> %d chars%s  (%.0fs)' % (
                n, len(todo), uid, role, len(text), len(entry['pl']), '  FLAGS: ' + '; '.join(flags) if flags else '', time.time() - t0))
        result['units'][uid] = entry
        save()
    if a.dry_run:
        p = a.output.with_suffix('.prompts.txt')
        p.write_text('\n'.join(prompts_txt), encoding='utf-8')
        log.info('translate', 'dry run: %d prompts written to %s' % (len(prompts_txt), p))
        chars_in = sum(len(x) for x in prompts_txt); chars_src = sum(len(u[1]) for u in todo)
        log.info('translate', 'rough estimate for a real run (about 3.5 characters per token; Polish output about as long as the German): '
                              '%d input tokens, %d output tokens, %d requests (without retries or cache hits)' % (
                                  chars_in / 3.5, chars_src * 0.96 / 3.0, len(todo)))
    else:
        if misses:
            log.info('translate', 'translated anew (not in the cache): ' + '; '.join(
                '%d x %s' % (v, k) for k, v in sorted(misses.items(), key=lambda kv: -kv[1])))
        result['usage'] = report_usage(a)
        result = save()
        log.info('translate', 'wrote %s (%d units, %d with flags)' % (a.output, len(result['units']), sum(bool(u['flags']) for u in result['units'].values())))
    return result


def parse_pages(spec):
    """'1,3-5' -> {1, 3, 4, 5}; raises ValueError on anything else."""
    pages = set()
    for part in str(spec).replace(' ', '').split(','):
        if not part:
            continue
        m = re.fullmatch(r'(\d+)(?:-(\d+))?', part)
        if not m:
            raise ValueError('page list must look like 1,3-5 (got %r)' % part)
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if hi < lo:
            raise ValueError('page range %s goes backwards' % part)
        pages.update(range(lo, hi + 1))
    if not pages:
        raise ValueError('empty page list')
    return pages


def units_on_pages(doc, pages):
    """Unit ids with at least one source block on one of ``pages`` (a paragraph across a page break counts for both)."""
    page_of = {b['block_id']: p['page'] for p in doc.get('pages', []) for b in p.get('blocks', [])}
    return [u['unit_id'] for u in doc.get('units', []) if any(page_of.get(b) in pages for b in u.get('source_block_ids', []))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('document', type=Path, help='document.json')
    ap.add_argument('-o', '--output', type=Path, default=None, help='translations JSON (default: next to document.json)')
    ap.add_argument('--provider', choices=['openai', 'anthropic', 'claude-code'], default='openai',
                    help='openai: OpenAI-compatible endpoint such as LM Studio (default); anthropic: Anthropic Messages API (key in $ANTHROPIC_API_KEY); '
                         'claude-code: the Claude Code CLI with your Claude subscription (claude -p, no API key)')
    ap.add_argument('--claude-bin', default=None, help='claude-code: the claude program (default: $CLAUDE_BIN, claude on PATH, ~/.local/bin/claude)')
    ap.add_argument('--claude-args', nargs='*', default=None, help='claude-code: extra arguments for claude, e.g. --claude-args=--verbose')
    ap.add_argument('--claude-use-api-key', action='store_true',
                    help='claude-code: keep $ANTHROPIC_API_KEY in the environment (Claude Code then bills the API; by default it is removed so the subscription is used)')
    ap.add_argument('--endpoint', default=None,
                    help='base URL (openai: LM Studio http://localhost:1234/v1, or $LMSTUDIO_ENDPOINT; anthropic: https://api.anthropic.com)')
    ap.add_argument('--model', default=None, help="model id as LM Studio lists it (default: the first model the endpoint reports); "
                                                    "anthropic: default claude-sonnet-5-5; claude-code: an alias (opus, sonnet) or a full id, default opus")
    ap.add_argument('--api-key', default=None, help='default: $ANTHROPIC_API_KEY (anthropic) or $LMSTUDIO_API_KEY (openai)')
    ap.add_argument('--price-in', type=float, help='price per million input tokens, to report a cost')
    ap.add_argument('--price-out', type=float, help='price per million output tokens')
    ap.add_argument('--glossary', nargs='*', type=Path, help='glossary CSV files, first wins (default: the central glossary, glossary_store.central_dir())')
    ap.add_argument('--prompt', type=Path, default=ROOT / 'prompts' / 'translate_pl.txt')
    ap.add_argument('--rules', type=Path, default=ROOT / 'prompts' / 'translate_rules_pl.txt')
    ap.add_argument('--temperature', type=float, default=0.2)
    ap.add_argument('--max-tokens', type=int, default=4096)
    ap.add_argument('--timeout', type=float, default=600, help='seconds per request')
    ap.add_argument('--retries', type=int, default=2, help='extra attempts when a check fails (default 2)')
    ap.add_argument('--only', nargs='*', help='translate only these unit ids (the other units of an existing output are kept)')
    ap.add_argument('--force', action='store_true',
                    help='ignore cached answers: send the selected units (--only / --pages; else the whole book) to the model again; '
                         'the new answer replaces the cached one')
    ap.add_argument('--pages', default=None, help='translate only the units on these pages, e.g. 1,3-5 (with --only: both sets)')
    ap.add_argument('--no-think', action='store_true', help='ask a reasoning model not to think (sends chat_template_kwargs.enable_thinking=false and reasoning_effort=none; whether the server passes them on depends on the server and model; in LM Studio the reliable switch is the model\'s "Enable Thinking" custom field)')
    ap.add_argument('--extra-body', help='JSON merged into every request body, e.g. \'{"top_p": 0.9}\'')
    ap.add_argument('--dry-run', action='store_true', help='write the prompts, do not call the model')
    ap.add_argument('--harvest', type=Path, default=None,
                    help='instead of translating: ask the model, unit by unit, which names and terms the existing translation '
                         '(-o) used, and write the ones the glossary lacks to this JSON (glossary candidates, glossary_harvest.py)')
    ap.add_argument('--raw', action='store_true', help='use document.json even if document.corrected.json (OCR-corrected by ocrfix.py) exists next to it')
    a = ap.parse_args()
    if a.provider == 'anthropic':
        a.endpoint = a.endpoint or 'https://api.anthropic.com'
        a.api_key = a.api_key or os.environ.get('ANTHROPIC_API_KEY')
        a.model = a.model or 'claude-sonnet-5-5'
        if not a.api_key and not a.dry_run:
            raise SystemExit('no API key: set ANTHROPIC_API_KEY (create one in the Anthropic Console) or pass --api-key')
    elif a.provider == 'claude-code':
        a.model = a.model or 'opus'
        a.endpoint = find_claude(a.claude_bin)
        if not a.endpoint and not a.dry_run:
            raise SystemExit('Claude Code not found: install it, pass --claude-bin PATH or set $CLAUDE_BIN')
        a.endpoint = a.endpoint or 'claude'
        if os.environ.get('ANTHROPIC_API_KEY') and not a.claude_use_api_key:
            log.info('translate', 'ANTHROPIC_API_KEY is set: removed for the claude calls so your subscription is used (--claude-use-api-key keeps it)')
    else:
        a.endpoint = a.endpoint or os.environ.get('LMSTUDIO_ENDPOINT', 'http://localhost:1234/v1')
        a.api_key = a.api_key or os.environ.get('LMSTUDIO_API_KEY')
    os.environ.setdefault('BOOKTRANS_T0', str(time.time()))
    corrected = a.document.with_name('document.corrected.json')
    if a.document.name == 'document.json' and corrected.exists() and not a.raw:
        log.info('translate', 'using %s (OCR-corrected; --raw uses document.json)' % corrected)
        a.document = corrected
    doc = json.loads(a.document.read_text(encoding='utf-8'))
    if a.pages:
        try:
            on = units_on_pages(doc, parse_pages(a.pages))
        except ValueError as e:
            raise SystemExit('--pages: %s' % e)
        if not on:
            raise SystemExit('--pages %s: no units on these pages' % a.pages)
        a.only = sorted(set(a.only or []) | set(on))
        log.info('translate', '--pages %s: %d units' % (a.pages, len(on)))
    a.output = a.output or a.document.parent / 'translations.json'
    a.output.parent.mkdir(parents=True, exist_ok=True)
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
    if a.harvest:
        harvest(doc, a)
        return
    run(doc, a)


if __name__ == '__main__':
    main()

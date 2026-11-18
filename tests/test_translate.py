import json

import pytest
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import translate as T

ROOT = Path(__file__).resolve().parent.parent


def _doc():
    blk = lambda uid, role, status='translate', up=0.0: {'kind': 'text', 'unit_id': uid, 'role': role,
                                                          'translation_status': status, 'typography': {'uppercase_ratio': up}}
    return {'pages': [{'blocks': [blk('u0001', 'caption', up=1.0), blk('u0002', 'paragraph'), blk('u0003', 'paragraph'),
                                  blk('u0004', 'caption', status='exclude')]}],
            'units': [{'unit_id': 'u0001', 'type': 'caption', 'text': '37 PITSCHEN PORTAL'},
                      {'unit_id': 'u0002', 'type': 'paragraph', 'text': 'Das Portal in Pitschen ist einfach. Es hat 3 Stufen.'},
                      {'unit_id': 'u0003', 'type': 'paragraph', 'text': 'Danach folgt Bösdorf mit dem Kämpfer.'},
                      {'unit_id': 'u0004', 'type': 'caption', 'text': 'Grundriss'}]}


def _args(tmp_path, **kw):
    g = tmp_path / 'g.csv'
    g.write_text('de,pl,kind,status\nPitschen,Byczyna,place,confirmed\nKämpfer,impost,term,proposed\n', encoding='utf-8')
    d = dict(prompt=ROOT / 'prompts' / 'translate_pl.txt', rules=ROOT / 'prompts' / 'translate_rules_pl.txt',
             glossary=[g], output=tmp_path / 't.json', endpoint='x', model='m', temperature=0.2, max_tokens=100,
             timeout=5, api_key=None, retries=2, only=None, dry_run=False)
    d.update(kw)
    return SimpleNamespace(**d)


def test_prompt_contains_glossary_context_and_kind(tmp_path):
    prompts = []
    answers = iter(['37 BYCZYNA, PORTAL', 'Portal w Byczynie jest prosty. Ma 3 stopnie.', 'Następnie Pakosławice z impostem.'])
    res = T.run(_doc(), _args(tmp_path), call=lambda p: (prompts.append(p), next(answers))[1])
    assert len(prompts) == 3 and 'u0004' not in res['units']                   # the excluded unit is skipped
    assert '- Pitschen = Byczyna (miejscowość)' in prompts[1] and 'Kämpfer' not in prompts[1]
    assert '- Kämpfer = impost (termin)' in prompts[2]
    assert 'Es hat 3 Stufen.' in prompts[2]                                      # the end of the previous paragraph as context
    assert 'set in capitals' in prompts[0] and 'caption' in prompts[0]
    assert res['units']['u0002']['pl'].startswith('Portal w Byczynie')


def test_retry_when_a_glossary_term_is_missing_then_flag(tmp_path):
    seen = []
    def call(p):
        seen.append(p)
        return 'Portal w Pitschen jest prosty. Ma 3 stopnie.'                    # never uses Byczyna
    res = T.run(_doc(), _args(tmp_path, only=['u0002'], retries=1), call=call)
    assert len(seen) == 2 and 'IMPORTANT: Use the glossary forms: Byczyna for Pitschen' in seen[1]
    assert any('glossary' in f for f in res['units']['u0002']['flags'])


def test_cache_avoids_a_second_request(tmp_path):
    n = []
    a = _args(tmp_path, only=['u0002'])
    T.run(_doc(), a, call=lambda p: (n.append(1), 'Portal w Byczynie jest prosty. Ma 3 stopnie.')[1])
    T.run(_doc(), a, call=lambda p: (n.append(1), 'inna odpowiedź')[1])
    assert len(n) == 1
    assert json.loads(a.output.read_text(encoding='utf-8'))['units']['u0002']['pl'].startswith('Portal w Byczynie')


def test_clean_answer():
    assert T.clean('<think>hmm</think>\nTłumaczenie: „Portal jest prosty.”') == 'Portal jest prosty.'
    assert T.clean('```\nTekst\n```') == 'Tekst'
    assert T.clean('<<<\nTekst\n>>>') == 'Tekst'


def test_dry_run_and_real_http_endpoint(tmp_path):
    a = _args(tmp_path, only=['u0002'], dry_run=True)
    T.run(_doc(), a)
    assert 'Text to translate' in (tmp_path / 't.prompts.txt').read_text(encoding='utf-8')

    got = {}
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            got['body'] = body
            out = json.dumps({'choices': [{'message': {'content': 'Portal w Byczynie jest prosty. Ma 3 stopnie.'}}]}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(out)
        def log_message(self, *a):
            pass
    srv = HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        b = _args(tmp_path, only=['u0002'], endpoint='http://127.0.0.1:%d/v1' % srv.server_port, output=tmp_path / 'h.json')
        res = T.run(_doc(), b)
    finally:
        srv.shutdown()
    assert res['units']['u0002']['pl'].startswith('Portal w Byczynie')
    assert got['body']['model'] == 'm' and got['body']['messages'][0]['role'] == 'user'


def test_empty_answers_are_not_cached(tmp_path):
    a = _args(tmp_path, only=['u0002'], retries=0)
    T.run(_doc(), a, call=lambda p: '')
    res = T.run(_doc(), a, call=lambda p: 'Portal w Byczynie jest prosty. Ma 3 stopnie.')
    assert res['units']['u0002']['pl'].startswith('Portal w Byczynie')


def test_digits_ignore_roman_numerals_and_superscripts():
    assert T.digits('der zweiten Hälfte des 13. Jahrhunderts') == T.digits('drugiej połowy XIII wieku')
    assert T.digits('1 Siehe Hamann II. 1923.') == T.digits('¹ Por. Hamann II, 1923.')
    assert T.digits('Abb. 37') != T.digits('il. 38')


def _serve(handler_cls):
    srv = HTTPServer(('127.0.0.1', 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_anthropic_provider_headers_usage_and_cost(tmp_path):
    got = {}
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got['path'] = self.path
            got['key'] = self.headers['x-api-key']; got['ver'] = self.headers['anthropic-version']
            got['body'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            out = json.dumps({'content': [{'type': 'text', 'text': 'Portal w Byczynie jest prosty. Ma 3 stopnie.'}],
                              'usage': {'input_tokens': 500, 'output_tokens': 40}, 'stop_reason': 'end_turn'}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(out)
        def log_message(self, *a):
            pass
    srv = _serve(H)
    try:
        a = _args(tmp_path, only=['u0002'], endpoint='http://127.0.0.1:%d' % srv.server_port, provider='anthropic',
                  api_key='sk-test', price_in=3.0, price_out=15.0, model='claude-sonnet-5-5')
        res = T.run(_doc(), a)
    finally:
        srv.shutdown()
    assert got['path'] == '/v1/messages' and got['key'] == 'sk-test' and got['ver'] == '2023-06-01'
    assert got['body']['messages'][0]['role'] == 'user' and 'stream' not in got['body'] and got['body']['max_tokens'] == 100
    assert res['units']['u0002']['pl'].startswith('Portal w Byczynie')
    assert res['usage']['input'] == 500 and res['usage']['output'] == 40 and res['usage']['calls'] == 1
    assert abs(res['usage']['cost'] - (500 * 3 + 40 * 15) / 1e6) < 1e-9


def test_overload_is_retried_and_errors_carry_the_message(tmp_path, monkeypatch):
    monkeypatch.setattr(T.time, 'sleep', lambda s: None)
    state = {'n': 0}
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            state['n'] += 1
            if self.path.endswith('bad'):
                self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":{"message":"invalid x-api-key"}}'); return
            if state['n'] < 3:
                self.send_response(529); self.end_headers(); self.wfile.write(b'overloaded'); return
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({'content': [{'type': 'text', 'text': 'ok'}], 'usage': {}}).encode())
        def log_message(self, *a):
            pass
    srv = _serve(H)
    try:
        base = 'http://127.0.0.1:%d' % srv.server_port
        assert T.chat_anthropic(base, 'm', 'x', 0.2, 10, 5, 'k') == 'ok' and state['n'] == 3
        import pytest
        with pytest.raises(RuntimeError, match='401.*invalid x-api-key'):
            T.post_json(base + '/bad', {}, {}, 5)
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- claude-code provider
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
prompt = sys.stdin.read()
log = os.environ['FAKE_CLAUDE_LOG']
with open(log, 'a', encoding='utf-8') as f:
    f.write(json.dumps({'argv': sys.argv[1:], 'prompt': prompt, 'cwd': os.getcwd(),
                        'has_key': 'ANTHROPIC_API_KEY' in os.environ}) + '\n')
mode = os.environ.get('FAKE_CLAUDE_MODE', 'ok')
if mode == 'limit':
    print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': True,
                      'result': 'Claude usage limit reached. Your limit will reset at 3am.', 'api_error_status': 429}))
    sys.exit(1)
print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                  'result': 'Portal w Byczynie jest prosty. Ma 3 stopnie.', 'total_cost_usd': 0.0123,
                  'usage': {'input_tokens': 900, 'output_tokens': 30, 'cache_read_input_tokens': 100}}))
'''


def _fake_claude(tmp_path, monkeypatch, mode='ok'):
    exe = tmp_path / 'claude'
    exe.write_text(FAKE_CLAUDE, encoding='utf-8')
    exe.chmod(0o755)
    monkeypatch.setenv('FAKE_CLAUDE_LOG', str(tmp_path / 'calls.jsonl'))
    monkeypatch.setenv('FAKE_CLAUDE_MODE', mode)
    return exe


def _calls(tmp_path):
    return [json.loads(l) for l in (tmp_path / 'calls.jsonl').read_text(encoding='utf-8').splitlines()]


def test_claude_code_provider_runs_the_cli_headless(tmp_path, monkeypatch):
    exe = _fake_claude(tmp_path, monkeypatch)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-should-not-be-passed')
    a = _args(tmp_path, only=['u0002'], provider='claude-code', endpoint=str(exe), model='opus')
    res = T.run(_doc(), a)
    assert res['units']['u0002']['pl'].startswith('Portal w Byczynie')
    (c,) = _calls(tmp_path)
    argv = c['argv']
    assert argv[:2] == ['-p', '--output-format'] and argv[argv.index('--output-format') + 1] == 'json'
    assert argv[argv.index('--model') + 1] == 'opus'
    assert argv[argv.index('--tools') + 1] == '' and '--no-session-persistence' in argv and '--bare' not in argv
    assert 'Pitschen = Byczyna' in c['prompt'] and 'Text to translate' in c['prompt']     # the whole prompt on stdin
    assert not c['has_key']                                                              # subscription, not the API
    assert Path(c['cwd']).name.startswith('booktrans-claude-') and not Path(c['cwd']).exists()   # empty, removed temp dir
    assert res['usage']['input'] == 900 and res['usage']['output'] == 30 and res['usage']['cache_read'] == 100
    assert abs(res['usage']['reported_cost'] - 0.0123) < 1e-9


def test_claude_code_keeps_the_api_key_only_when_asked(tmp_path, monkeypatch):
    exe = _fake_claude(tmp_path, monkeypatch)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-x')
    T.run(_doc(), _args(tmp_path, only=['u0002'], provider='claude-code', endpoint=str(exe), model='opus',
                        claude_use_api_key=True, claude_args=['--verbose']))
    (c,) = _calls(tmp_path)
    assert c['has_key'] and c['argv'][-1] == '--verbose'


def test_claude_code_usage_limit_stops_and_keeps_the_cache(tmp_path, monkeypatch):
    exe = _fake_claude(tmp_path, monkeypatch)
    a = _args(tmp_path, only=['u0002', 'u0003'], provider='claude-code', endpoint=str(exe), model='opus', retries=0)
    a.only = ['u0002']
    T.run(_doc(), a)                                                  # u0002 cached
    monkeypatch.setenv('FAKE_CLAUDE_MODE', 'limit')
    a.only = ['u0002', 'u0003']
    with pytest.raises(SystemExit) as e:
        T.run(_doc(), a)
    assert 'usage limit reached at u0003' in str(e.value) and 'run the same command again' in str(e.value)
    out = json.loads(a.output.read_text(encoding='utf-8'))
    assert out['units']['u0002']['pl'].startswith('Portal w Byczynie')   # the finished unit survives in the output


def _result(**kw):
    d = {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'ok', 'usage': {}}
    d.update(kw)
    return SimpleNamespace(stdout=json.dumps(d), stderr='', returncode=1 if d['is_error'] else 0)


def test_claude_code_errors_are_classified(monkeypatch):
    waits = []
    # the real CLI (2.1.326) without a login: is_error with subtype "success" and a "network issue" wording
    auth = _result(is_error=True, result='Authentication error · This may be a temporary network issue, please try again',
                   api_error_status=None)
    with pytest.raises(RuntimeError, match='not logged in'):
        T.chat_claude_code('claude', 'opus', 'x', 5, run=lambda *a, **k: auth, sleep=waits.append)
    assert waits == []                                                # no retry for a missing login
    seq = iter([_result(is_error=True, result='API Error: 529 overloaded', api_error_status=529), _result(result='Tekst')])
    assert T.chat_claude_code('claude', 'opus', 'x', 5, run=lambda *a, **k: next(seq), sleep=waits.append) == 'Tekst'
    assert waits == [10]
    with pytest.raises(T.UsageLimit):
        T.chat_claude_code('claude', 'opus', 'x', 5, run=lambda *a, **k: _result(is_error=True, result='x', api_error_status=429))
    empty = SimpleNamespace(stdout='', stderr='Invalid API key · Please run /login', returncode=1)
    with pytest.raises(RuntimeError, match='not logged in'):
        T.chat_claude_code('claude', 'opus', 'x', 5, run=lambda *a, **k: empty)
    def slow(*a, **k):
        raise T.subprocess.TimeoutExpired('claude', 5)
    with pytest.raises(RuntimeError, match='within 5s'):
        T.chat_claude_code('claude', 'opus', 'x', 5, run=slow)


def test_find_claude(tmp_path, monkeypatch):
    exe = tmp_path / 'myclaude'; exe.write_text('#!/bin/sh\n'); exe.chmod(0o755)
    assert T.find_claude(str(exe)) == str(exe)
    monkeypatch.setenv('CLAUDE_BIN', str(exe))
    assert T.find_claude() == str(exe)


# ---------------------------------------------------------------- partial runs (--only / --pages)
def _echo(prompts):
    def call(p):                      # passes the checks: keeps digits and length, carries the glossary forms
        prompts.append(p)
        text = p.rsplit('<<<\n', 1)[-1].split('\n>>>')[0]
        return text.replace('Pitschen', 'Byczyna').replace('PITSCHEN', 'BYCZYNA').replace('Kämpfer', 'impost') + ' #%d' % len(prompts)
    return call


def test_partial_run_keeps_the_other_units_and_uses_the_book_context(tmp_path):
    full = []
    T.run(_doc(), _args(tmp_path), call=_echo(full))
    before = json.loads((tmp_path / 't.json').read_text(encoding='utf-8'))
    assert list(before['units']) == ['u0001', 'u0002', 'u0003']
    # re-translate only u0003 under a different model name: the other units stay in the output
    part = []
    res = T.run(_doc(), _args(tmp_path, only=['u0003'], model='m2'), call=_echo(part))
    assert len(part) == 1 and 'Es hat 3 Stufen.' in part[0]           # context from u0002 although u0002 is not selected
    assert list(res['units']) == ['u0001', 'u0002', 'u0003']          # document order, nothing dropped
    assert res['units']['u0001'] == before['units']['u0001'] and res['units']['u0002'] == before['units']['u0002']
    assert res['units']['u0003']['pl'] != before['units']['u0003']['pl']
    # same model: the partial run hits the cache of the full run (identical prompt, so identical key)
    again = []
    T.run(_doc(), _args(tmp_path, only=['u0003']), call=_echo(again))
    assert again == []


def test_pages_select_units_by_their_blocks():
    doc = {'pages': [{'page': 1, 'blocks': [{'block_id': 'a'}, {'block_id': 'b'}]},
                     {'page': 2, 'blocks': [{'block_id': 'c'}]}, {'page': 3, 'blocks': [{'block_id': 'd'}]}],
           'units': [{'unit_id': 'u1', 'source_block_ids': ['a']}, {'unit_id': 'u2', 'source_block_ids': ['b', 'c']},
                     {'unit_id': 'u3', 'source_block_ids': ['d']}]}
    assert T.parse_pages('1, 3-4') == {1, 3, 4}
    assert T.units_on_pages(doc, {2}) == ['u2']                         # a paragraph across the page break counts
    assert T.units_on_pages(doc, T.parse_pages('1-2')) == ['u1', 'u2']
    for bad in ('x', '3-1', ''):
        with pytest.raises(ValueError):
            T.parse_pages(bad)


def test_force_asks_the_model_again_for_the_selected_units(tmp_path):
    first = []
    T.run(_doc(), _args(tmp_path), call=_echo(first))
    old = json.loads((tmp_path / 't.json').read_text(encoding='utf-8'))['units']
    again = []
    res = T.run(_doc(), _args(tmp_path, only=['u0002'], force=True), call=_echo(again))
    assert len(again) == 1 and 'Das Portal in Pitschen' in again[0]           # only the selected unit, despite the cache
    assert res['units']['u0002']['pl'] != old['u0002']['pl']
    assert res['units']['u0001'] == old['u0001'] and res['units']['u0003'] == old['u0003']
    later = []
    res2 = T.run(_doc(), _args(tmp_path), call=_echo(later))                 # the new answer replaced the cached one
    assert later == [] and res2['units']['u0002']['pl'] == res['units']['u0002']['pl']


def test_a_unit_emptied_by_a_human_is_not_sent(tmp_path):
    doc = _doc()
    doc['units'][2]['text'] = ''                       # joined into another caption / paragraph in the GUI
    sent = []
    res = T.run(doc, _args(tmp_path), call=lambda p: (sent.append(p), 'Portal w Byczynie jest prosty. Ma 3 stopnie.')[1])
    assert res['units']['u0003'] == {'source': '', 'pl': '', 'glossary': [], 'flags': []}
    assert len(sent) == 2


def test_punctuation_rules_flag_and_retry(tmp_path):
    bad = 'Niestety, również i to niewiele, co posiadamy z owych czasów, - chodzi tu o kościoły -, zostało'
    assert T.punctuation_problems(bad) == ['comma and dash together', 'spaced hyphen as a dash']
    assert T.punctuation_problems('kościół — portal') == ['long dash']
    assert T.punctuation_problems('wiek?, nie') == ['two punctuation marks together']
    for ok in ('z owych czasów – chodzi tu o kościoły – zostało', 'biało-czerwony, XII-wieczny', 'itd., oraz',
               'lata 1900–1910 (il. 37).', 'por. il. 5, s. 12.'):
        assert T.punctuation_problems(ok) == [], ok
    answers = iter(['Portal w Byczynie, - jak widać -, jest prosty. Ma 3 stopnie.',
                    'Portal w Byczynie – jak widać – jest prosty. Ma 3 stopnie.'])
    prompts = []
    res = T.run(_doc(), _args(tmp_path, only=['u0002']), call=lambda p: (prompts.append(p), next(answers))[1])
    assert res['units']['u0002']['pl'].startswith('Portal w Byczynie – jak') and not res['units']['u0002']['flags']
    assert 'never both' in prompts[1]                  # the retry names the rule
    assert 'półpauza' in prompts[0]                    # the rule is in the prompt


def test_titles_in_italics_are_allowed_other_markup_is_not(tmp_path):
    ok = 'Zob. Richard Hamann, <i>Deutsche und französische Kunst im Mittelalter</i>, t. II, 1923.'
    assert T.tag_problems(ok) == '' and T.punctuation_problems(ok) == []
    assert T.tag_problems('<i>Tytuł</i> i <i>drugi</i>') == ''
    assert T.tag_problems('<i>Tytuł, 1923.') == 'unbalanced <i>'
    assert T.tag_problems('<b>Tytuł</b>') == 'tag <b>'
    assert T.tag_problems('Zob. *Deutsche Kunst*, 1923.') == 'Markdown emphasis'
    assert T.tag_problems('2 * 3 = 6, a_b') == ''
    answers = iter(['Dalej impost, zob. *Deutsche Kunst*.', 'Dalej impost, zob. <i>Deutsche Kunst</i>.'])
    prompts = []
    res = T.run(_doc(), _args(tmp_path, only=['u0003']), call=lambda p: (prompts.append(p), next(answers))[1])
    assert res['units']['u0003']['pl'] == 'Dalej impost, zob. <i>Deutsche Kunst</i>.'
    assert 'Titles of cited works' in prompts[0] and '<i>...</i> only around titles' in prompts[1]

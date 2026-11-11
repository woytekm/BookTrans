import json
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

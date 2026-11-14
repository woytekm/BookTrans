#!/usr/bin/env python3
"""Stage 5: correct OCR errors in the German text of the units, offline, before translation.

    python ocrfix.py fetch                                   # download the dictionaries into ../../dictionaries/
    python ocrfix.py run output/book/document.json           # -> document.corrected.json, ocr_corrections.json, ocr_review.json
    python ocrfix.py text "Niederschlesten und Überzesten"   # try the checker on a string

What it does, per unit (text of a ``document.json`` unit, already dehyphenated by stage 4):

1. The text is cut into words.  A word is *known* when it is in the frequency list (SymSpell ``de-100k``),
   accepted by Hunspell (``de_DE_frami`` through the pure-Python ``spylls``), in the project vocabulary
   (``glossary/vocabulary.txt``), a glossary name, or a compound of known words.  Old spelling is accepted
   (``daß``, ``Thür``, ``Cultur`` are checked through their modern forms).
2. For an unknown word, candidates are generated with a *cost*:
   OCR confusions (``Binfassung`` -> ``Einfassung``: B for E, cost 0.7; ``fi`` for ``ß``; ``O8`` -> ``OS``),
   edit-distance neighbours (SymSpell), lost spaces (``nichtmehr`` -> ``nicht mehr``) and, as a last resort
   for the human, Hunspell suggestions.
3. **Conservative decision.**  A candidate is applied automatically only if its cost is at most
   ``AUTO_MAX`` and it beats the runner-up clearly.  Everything else is *flagged* with the best candidates
   (``ocr_review.json``) for a human.  A known word that looks like a frequent OCR confusion of a far more
   common word (``Fin`` for ``Ein``) is flagged as *suspect*, never changed.
4. Every automatic change is recorded (unit, offset, before, after, rule, cost) in ``ocr_corrections.json``.

The corrected file is a copy of ``document.json`` in which each unit has ``text`` (corrected), ``text_raw``
(original) and ``ocr_corrections``; the blocks and lines keep the raw OCR (they carry the layout).  Point
``translate.py`` at the corrected file.  Human decisions go into ``ocr_review.json`` (``resolution`` of a
flag: the right word, or ``"="`` to accept the word as it is; ``accepted_words`` accepts words everywhere)
and are applied on the next run.  See ``docs/OCR_CORRECTION.md``.

Needs ``spylls`` and ``symspellpy`` (``pip install spylls symspellpy``) and the dictionaries (``fetch``).
Without ``spylls`` only the frequency list and the vocabulary are used (more flags, fewer corrections).
"""
import argparse
import itertools
import json
import math
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import glossary as G   # noqa: E402
import log             # noqa: E402

ROOT = HERE.parent.parent
DICT_DIR = ROOT / 'dictionaries'
VOCAB = ROOT / 'glossary' / 'vocabulary.txt'
SOURCES = {
    'de-100k.txt': 'https://raw.githubusercontent.com/wolfgarbe/SymSpell/master/SymSpell.FrequencyDictionary/de-100k.txt',
    'de_DE_frami.dic': 'https://raw.githubusercontent.com/LibreOffice/dictionaries/master/de/de_DE_frami.dic',
    'de_DE_frami.aff': 'https://raw.githubusercontent.com/LibreOffice/dictionaries/master/de/de_DE_frami.aff',
}

MIN_AUTO_LEN = 5       # shorter unknown words are only flagged (too little evidence)
NAME_RISK_COST = 0.6   # a capitalised word may be a name: above this cost it is changed only if the candidate is a common word
NAME_RISK_COUNT = 10000
AUTO_MAX = 1.0         # highest candidate cost that may be applied without a human
MARGIN = 0.29          # the best candidate must be this much cheaper than the runner-up ...
COMPOUND_PENALTY = 0.5  # a candidate known only as a compound of known words costs this much more
FREQ_RATIO = 50        # ... or this many times more frequent at equal cost
SUSPECT_RATIO = 500    # a known word is "suspect" when its OCR-confusion neighbour is this much more frequent
SUSPECT_MAX_FREQ = 20000   # ... and the word itself is rarer than this (counts of de-100k)

# OCR output -> intended text, cost (lower = more typical OCR slip).  Both directions of the common pairs.
CONFUSIONS = [
    ('B', 'E', .7), ('F', 'E', .6), ('K', 'E', .8), ('E', 'F', .7), ('E', 'B', .8),
    ('fi', 'ß', .5), ('B', 'ß', .7), ('f', 'ß', .9), ('ss', 'ß', .6),
    ('i', 't', .6), ('t', 'i', .6), ('l', 't', .8), ('t', 'l', .8), ('l', 'i', .7), ('i', 'l', .7),
    ('l', 'f', .6), ('z', 'g', .8), ('z', 'r', .7), ('r', 'z', .9), ('e', 'g', .9), ('g', 'e', .9), ('e', 'c', .6), ('c', 'e', .6),
    ('c', 'o', .8), ('o', 'c', .8), ('e', 'o', .9), ('a', 'o', .9), ('n', 'u', .5), ('u', 'n', .5),
    ('rn', 'm', .5), ('m', 'rn', .8), ('in', 'm', .8), ('ni', 'm', .9), ('li', 'h', .6), ('h', 'li', .6),
    ('h', 'b', .8), ('b', 'h', .8), ('ii', 'n', .7), ('ri', 'n', .8), ('vv', 'w', .5), ('cl', 'd', .6),
    ('d', 'cl', .8), ('s', 'f', .8), ('f', 's', .8), ('f', 'k', .9), ('k', 'E', .9),
    ('u', 'ü', .6), ('a', 'ä', .6), ('o', 'ö', .6), ('U', 'Ü', .6), ('A', 'Ä', .6), ('O', 'Ö', .6),
    ('ii', 'ü', .6), ('I', 'l', .6), ('l', 'I', .7), ('1', 'l', .8), ('0', 'O', .5), ('8', 'S', .7),
    ('5', 'S', .8), ('8', 'B', .8), ('0', 'o', .6),
]
CONF_BY_KEY = {}
for _o, _t, _c in CONFUSIONS:
    CONF_BY_KEY.setdefault(_o, []).append((_t, _c))

STOP = set('''ab am an da du er es ob oder um zu so je im in ist und der die das den dem des ein eine einer
nicht mehr auch aber wie wenn nur noch bei mit von vor aus nach zum zur als wo ihr sie wir ihn uns
dass daß hier dort sehr viel man''' .split())
LINK = ['', 's', 'es', 'n', 'en', 'e', 'er', 'ns', 'ens']
SEG_STOP = STOP - {'er', 'es', 'du', 'ab', 'je', 'ob', 'ihr', 'sie', 'wir', 'uns', 'ihn', 'man', 'da', 'so', 'um', 'wo'}
ABBREV_GLUE = re.compile(r'(?<![A-Za-zÄÖÜäöüß])([A-Z])\.(?=[a-zäöüß]{2,})')
TOKEN = re.compile(r"[^\W_]+(?:-[^\W_]+)*", re.U)
ROMAN = re.compile(r'^[IVXLCDM]+$')


def dl_distance(a, b):
    """Damerau-Levenshtein (optimal string alignment) distance."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            c = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def recase(orig, cand):
    """Give a lower-case candidate the capitalisation pattern of the original token."""
    if len(orig) > 1 and orig.isupper():
        return cand.upper()
    if orig[:1].isupper():
        return cand[:1].upper() + cand[1:]
    return cand


# ---------------------------------------------------------------- dictionaries
def fetch(dest=DICT_DIR):
    dest.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        log.info('fetch', '%s' % name)
        with urllib.request.urlopen(url, timeout=120) as r:
            (dest / name).write_bytes(r.read())
    log.info('fetch', 'done: %s' % dest)


class Lexicon:
    """Word knowledge: frequency list, Hunspell, project vocabulary, glossary names."""

    def __init__(self, dict_dir=DICT_DIR, vocab=(VOCAB,), glossary_files=(), use_hunspell=True, freq_lines=None):
        from symspellpy import SymSpell
        self.freq = {}
        self.cased = {}      # lower -> proper form of vocabulary / glossary names
        self.names = set()   # lower-case vocabulary + glossary words (never "corrected")
        self.hs = None
        self._known, self._hs_cache = {}, {}
        self.sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
        lines = freq_lines
        fp = Path(dict_dir) / 'de-100k.txt'
        if lines is None and fp.exists():
            lines = fp.read_text(encoding='utf-8').splitlines()
        elif lines is None:
            log.info('ocrfix', 'WARNING: %s missing (run: python ocrfix.py fetch); only the vocabulary is used' % fp)
            lines = []
        for ln in lines:
            p = ln.split()
            if len(p) == 2 and p[1].isdigit():
                self.freq[p[0]] = int(p[1])
        self.total = float(sum(self.freq.values()) or 1)
        for path in vocab:
            if path and Path(path).exists():
                for ln in Path(path).read_text(encoding='utf-8').splitlines():
                    ln = ln.split('#')[0].strip()
                    if ln:
                        w = ln.split()
                        self.add_name(w[0], int(w[1]) if len(w) > 1 and w[1].isdigit() else 5000000)
        if glossary_files:
            for e in G.load(glossary_files):
                for nm in e['_names']:
                    for w in G.words(nm):
                        self.add_name(w, 5000000)
        for w, c in self.freq.items():
            self.sym.create_dictionary_entry(w, c)
        if use_hunspell and (Path(dict_dir) / 'de_DE_frami.dic').exists():
            try:
                from spylls.hunspell import Dictionary
                self.hs = Dictionary.from_files(str(Path(dict_dir) / 'de_DE_frami'))
            except ImportError:
                log.info('ocrfix', 'WARNING: spylls not installed (pip install spylls); Hunspell is not used')

    def add_name(self, word, count=5000000):
        lw = word.lower()
        self.names.add(lw)
        self.cased[lw] = word
        self.freq[lw] = max(self.freq.get(lw, 0), count)

    # --- knowledge
    def count(self, w):
        lw = w.lower()
        return max([self.freq.get(v, 0) for v in self._variants(lw)] or [0])

    @staticmethod
    def _variants(lw):
        """The word and its modern spellings (old orthography: ß, th, c)."""
        out = [lw]
        if 'ß' in lw:
            out.append(lw.replace('ß', 'ss'))
        for v in list(out):
            if 'th' in v:
                out.append(v.replace('th', 't'))
            if v.startswith('c') and len(v) > 4:
                out += ['k' + v[1:], 'z' + v[1:]]
        return out

    def _hs(self, w):
        r = self._hs_cache.get(w)
        if r is None:
            r = bool(self.hs and (self.hs.lookup(w) or self.hs.lookup(w.capitalize()) or self.hs.lookup(w.lower())))
            self._hs_cache[w] = r
        return r

    def _word(self, v):
        return v in self.freq or v in self.names or self._hs(v)

    def known_word(self, w):
        return any(self._word(v) for v in self._variants(w.lower()))

    def is_compound(self, lw, depth=0):
        """Known compound: parts of at least 4 letters joined by a linking element."""
        if len(lw) < 8 or depth > 2:
            return False
        for i in range(4, len(lw) - 3):
            left, right = lw[:i], lw[i:]
            for link in LINK:
                if link and not left.endswith(link):
                    continue
                core = left[:len(left) - len(link)] if link else left
                if len(core) >= 4 and self._part(core) and (self._part(right) or self.is_compound(right, depth + 1)):
                    return True
        return False

    def _part(self, p):
        return len(p) >= 4 and p not in STOP and (p in self.freq or p in self.names or self._hs(p))

    def known(self, w):
        k = self._known.get(w)
        if k is None:
            k = self.known_word(w) or self.is_compound(w.lower())
            self._known[w] = k
        return k


# ---------------------------------------------------------------- candidates
class Cand:
    __slots__ = ('text', 'cost', 'rule', 'count')

    def __init__(self, text, cost, rule, count=0):
        self.text, self.cost, self.rule, self.count = text, round(cost, 2), rule, count

    def as_dict(self):
        return {'text': self.text, 'cost': self.cost, 'rule': self.rule}


class Checker:
    def __init__(self, lex, suggest=True):
        self.lex, self.suggest = lex, suggest
        self._sug = {}

    # --- A: OCR confusions, up to two substitutions
    def confusions(self, tok, max_cost=1.6):
        lex, seen, out = self.lex, {tok: 0.0}, []
        frontier = [(tok, 0.0)]
        for _ in range(2):
            nxt = []
            for word, base in frontier:
                for key, reps in CONF_BY_KEY.items():
                    start = 0
                    while True:
                        i = word.find(key, start)
                        if i < 0:
                            break
                        start = i + 1
                        for rep, c in reps:
                            v, cost = word[:i] + rep + word[i + len(key):], base + c
                            if cost > max_cost or seen.get(v, 9) <= cost:
                                continue
                            seen[v] = cost
                            nxt.append((v, cost))
                            if v.lower() == tok.lower():
                                continue
                            if lex.known_word(v):
                                out.append(Cand(v, cost, 'ocr_confusion', lex.count(v)))
                            elif lex.is_compound(v.lower()):
                                out.append(Cand(v, cost + COMPOUND_PENALTY, 'ocr_confusion', 0))
            if out:        # a single substitution is enough; do not pile up second ones
                break
            frontier = nxt[:150]
        return out

    # --- B: edit distance in the frequency list
    def edits(self, tok):
        from symspellpy import Verbosity
        lw = tok.lower()
        n = len(lw)
        if n < 5:
            return []
        out = []
        for s in self.lex.sym.lookup(lw, Verbosity.ALL, max_edit_distance=1 if n < 8 else 2, include_unknown=False):
            if s.term != lw:
                t = self.lex.cased.get(s.term) or recase(tok, s.term)
                out.append(Cand(t, float(s.distance), 'edit_distance', s.count))
        return out

    # --- C: lost spaces
    def _seg_cost(self, seg):
        lex = self.lex
        if seg in SEG_STOP:
            return 2.5, True
        if len(seg) < 3:
            return None, False
        c = lex.count(seg)
        if c:
            return -math.log(c / lex.total), c >= 5000
        if len(seg) >= 7 and lex.known_word(seg):
            return 18.0, True
        return None, False

    def segmentation(self, tok):
        lw, n = tok.lower(), len(tok)
        if n < 6:
            return []
        INF = float('inf')
        best = [(INF, None, True)] * (n + 1)
        best[0] = (0.0, None, True)
        for i in range(n):
            if best[i][0] == INF:
                continue
            for j in range(i + 2, min(n, i + 26) + 1):
                c, good = self._seg_cost(lw[i:j])
                if c is None:
                    continue
                tot = best[i][0] + c + 4.0
                if tot < best[j][0]:
                    best[j] = (tot, i, best[i][2] and good)
        if best[n][1] is None:
            return []
        cuts, j = [], n
        while j:
            cuts.append(j)
            j = best[j][1]
        cuts = sorted(cuts)
        if len(cuts) < 2 or len(cuts) > 4:
            return []
        parts, prev = [], 0
        for c in cuts:
            parts.append(tok[prev:c])
            prev = c
        common = all(p.lower() in SEG_STOP or self.lex.count(p) >= 20000000 for p in parts)
        return [Cand(' '.join(parts), 0.5 if common else (1.0 if best[n][2] else 1.5), 'lost_space')]

    # --- D: Hunspell suggestions (slow; only to help the human)
    def hunspell(self, tok):
        hs = self.lex.hs
        if not (self.suggest and hs) or not 6 <= len(tok) <= 24:
            return []
        if tok not in self._sug:
            try:
                self._sug[tok] = list(itertools.islice(hs.suggest(tok), 6))
            except Exception:    # spylls can fail on odd input; this is only a hint
                self._sug[tok] = []
        out = []
        for s in self._sug[tok]:
            if ' ' in s or s.startswith('-'):
                continue
            d = dl_distance(tok.lower(), s.lower())
            if d <= 2:
                out.append(Cand(s, d + 0.2, 'hunspell', self.lex.count(s)))
        return out

    def candidates(self, tok, camel=True):
        if any(ch.isdigit() for ch in tok):
            return self._finish(tok, self.confusions(tok))
        seg = (self.camel(tok) if camel else []) or self.segmentation(tok)
        if len(tok) >= 14 and seg and seg[0].cost <= AUTO_MAX:
            return seg      # long unknown word that splits cleanly into known words: no need to look further
        cands = self.confusions(tok) + self.edits(tok) + seg
        if not any(c.cost <= AUTO_MAX for c in cands):
            cands += self.hunspell(tok)
        return self._finish(tok, cands)

    def camel(self, tok):
        """``gewölbtesPortalmit`` / ``DieDurcbildung``: an inner capital after a lower-case letter starts a word."""
        cuts = [m.start() for m in re.finditer(r'(?<=[a-zäöüß])(?=[A-ZÄÖÜ])', tok)]
        if not cuts or len(tok) < 6:
            return []
        parts, prev = [], 0
        for c in cuts + [len(tok)]:
            parts.append(tok[prev:c])
            prev = c
        out, cost = [], 0.5
        for part in parts:
            if len(part) < 2 or self.lex.known(part) or (len(part) <= 3 and part[0].isupper()):
                out.append(part)
                continue
            sub = [c for c in self.candidates(part, camel=False)]
            b, _ = self.decide(sub, part)
            if not b:
                if sub and sub[0].cost <= 1.5:
                    b = sub[0]
                else:
                    return []
            out.append(b.text)
            cost += b.cost
        return [Cand(' '.join(out), cost, 'lost_space')]

    def _finish(self, tok, cands):
        lex = self.lex
        for c in cands:      # an exact project/glossary name is the better guess
            if c.text in lex.cased.values() and c.rule != 'lost_space':
                c.cost = round(max(c.cost - 0.3, 0.1), 2)
        best = {}
        caps = len(tok) > 1 and tok.isupper()
        for c in cands:
            if caps and c.rule != 'lost_space':
                c.text = c.text.upper()      # a heading in capitals: candidates in capitals, one per word
            if c.text == tok or c.text.lower() == tok.lower() and c.rule != 'ocr_confusion':
                continue
            if len(c.text) < 3 and len(tok) >= 3:
                continue        # never offer a one- or two-letter word for a longer one
            if c.text not in best or (c.cost, -c.count) < (best[c.text].cost, -best[c.text].count):
                best[c.text] = c
        return sorted(best.values(), key=lambda c: (c.cost, -c.count))

    def decide(self, cands, tok=''):
        """(candidate or None, reason) -- the conservative auto-correction rule."""
        if not cands:
            return None, 'no_candidate'
        b = cands[0]
        if b.cost > AUTO_MAX:
            return None, 'weak_candidate'
        if len(tok) < MIN_AUTO_LEN and b.text not in self.lex.cased.values():
            return None, 'short_word'     # (a project/glossary name is evidence enough)
        if tok[:1].isupper() and not tok.isupper() and b.cost > NAME_RISK_COST and b.count < NAME_RISK_COUNT and b.rule != 'lost_space':
            return None, 'name_risk'
        if len(cands) > 1:
            s = cands[1]
            if s.cost - b.cost < MARGIN and not b.count >= FREQ_RATIO * max(s.count, 1):
                return None, 'ambiguous'
        return b, 'auto'

    def suspect(self, tok):
        """A known, rare word that is a typical OCR slip of a much more frequent word (Fin <- Ein)."""
        lex = self.lex
        lw = tok.lower()
        c = lex.count(lw)
        if len(lw) < 3 or c > SUSPECT_MAX_FREQ or tok in lex.names or lw in lex.names:
            return []
        out = []
        for key, reps in CONF_BY_KEY.items():
            i = tok.find(key)
            while i >= 0:
                for rep, cost in reps:
                    v = tok[:i] + rep + tok[i + len(key):]
                    vc = lex.count(v)
                    if vc >= SUSPECT_RATIO * max(c, 1) and lex.known_word(v) and v != tok:
                        out.append(Cand(v, cost, 'suspect', vc))
                i = tok.find(key, i + 1)
        best = {}
        for c in out:
            if c.text not in best or c.count > best[c.text].count:
                best[c.text] = c
        return sorted(best.values(), key=lambda c: -c.count)[:3]


# ---------------------------------------------------------------- text
class Fixer:
    def __init__(self, lex, suggest=True, accepted=()):
        self.lex = lex
        self.ck = Checker(lex, suggest)
        self.accepted = {w.lower() for w in accepted}

    def process(self, text, unit_id='', resolutions=None):
        """-> (corrected_text, corrections[], flags[]).  Offsets refer to ``text``."""
        resolutions = resolutions or {}
        edits, flags, corrections = [], [], []
        for m in ABBREV_GLUE.finditer(text):
            edits.append((m.end(), m.end(), ' ', 'abbrev_space', 0.2, m.group(0), m.group(0) + ' '))
        occ = {}
        for tm in TOKEN.finditer(text):
            parts, pos = [], tm.start()
            for part in tm.group(0).split('-'):
                parts.append((pos, part))
                pos += len(part) + 1
            joined = ''.join(t for _, t in parts)
            if len(parts) > 1 and self.lex.known(joined) and all(len(t) > 1 for _, t in parts):
                continue    # a hyphenated spelling of a known word
            for k, (start, tok) in enumerate(parts):
                end = start + len(tok)
                occ[tok] = occ.get(tok, 0) + 1
                key = '%s|%s|%d' % (unit_id, tok, occ[tok])
                followed_by_dot = text[end:end + 1] == '.'
                res = self._classify(tok, followed_by_dot)
                if res is None:
                    continue
                if key in resolutions:
                    r = resolutions[key]
                    if r and r != '=':
                        edits.append((start, end, r, 'human', 0.0, tok, r))
                    continue
                if tok.lower() in self.accepted:
                    continue
                kind, cand, cands = res
                if kind == 'fix':
                    edits.append((start, end, cand.text, cand.rule, cand.cost, tok, cand.text))
                else:
                    flags.append(self._flag(unit_id, tok, start, kind, cands, text, key))
        edits.sort(key=lambda e: (e[0], e[1]))
        out, last = [], 0
        for s, e, rep, rule, cost, before, after in edits:
            if s < last:
                continue
            out.append(text[last:s])
            out.append(rep)
            last = e
            corrections.append({'unit': unit_id, 'offset': s, 'before': before, 'after': after, 'rule': rule, 'cost': cost})
        out.append(text[last:])
        return ''.join(out), corrections, flags

    def _classify(self, tok, followed_by_dot):
        """None (fine) | ('fix', cand, cands) | (reason, None, cands)."""
        lex, ck = self.lex, self.ck
        has_digit = any(c.isdigit() for c in tok)
        if tok.isdigit() or ROMAN.match(tok) or len(tok) == 1 and followed_by_dot:
            return None
        if has_digit:
            if len(tok) < 2 or re.match(r'^\d+[a-z]?$', tok) or re.match(r'^\d+(?:st|nd|rd|th)$', tok):
                return None
            if tok.isalnum() and any(c.isalpha() for c in tok):
                cands = ck.candidates(tok)
                c, why = ck.decide(cands, tok)
                return ('fix', c, cands) if c else ('mixed_digits_letters', None, cands)
            return None
        if len(tok) == 1:
            if tok.lower() in ('a', 'o') or tok.isupper():
                return None
            return ('stray_letter', None, [])
        if len(tok) == 2 and (lex.known_word(tok) or tok.isupper()):
            return None
        if lex.known(tok):
            sus = ck.suspect(tok)
            return ('suspect', None, sus) if sus else None
        if len(tok) <= 2:
            return None
        cands = ck.candidates(tok)
        c, why = ck.decide(cands, tok)
        if c:
            return 'fix', c, cands
        return why, None, cands

    @staticmethod
    def _flag(unit_id, tok, start, reason, cands, text, key):
        if reason == 'suspect':
            sev = 'low'
        elif reason in ('no_candidate',) and tok[:1].isupper() and start > 0:
            sev, reason = 'low', 'unknown_name_or_word'
        elif reason == 'no_candidate':
            sev = 'high'
        else:
            sev = 'medium'
        return {'key': key, 'unit': unit_id, 'offset': start, 'token': tok, 'reason': reason, 'severity': sev,
                'candidates': [c.as_dict() for c in cands[:4]],
                'context': text[max(0, start - 40):start + len(tok) + 40], 'resolution': None}


# ---------------------------------------------------------------- document
def load_review(path):
    if not Path(path).exists():
        return {}, []
    d = json.loads(Path(path).read_text(encoding='utf-8'))
    res = {r['key']: r['resolution'] for r in d.get('resolved', [])}
    res.update({f['key']: f['resolution'] for f in d.get('flags', []) if f.get('resolution') is not None})
    return res, d.get('accepted_words', [])


def load_proposals(path):
    """LLM proposals (``llm``) of the flags of an earlier review file, by flag key (see ocrreview.py)."""
    if not Path(path).exists():
        return {}
    d = json.loads(Path(path).read_text(encoding='utf-8'))
    return {f['key']: f['llm'] for f in d.get('flags', []) if f.get('llm')}


def run(doc, fixer, review_res=None):
    """Correct ``doc['units']`` in place.  -> (corrections, flags)."""
    info = {}
    for p in doc['pages']:
        for b in p['blocks']:
            if b['kind'] == 'text' and b.get('unit_id') and b['unit_id'] not in info:
                info[b['unit_id']] = b.get('translation_status')
    all_c, all_f = [], []
    for u in doc['units']:
        if info.get(u['unit_id'], 'translate') == 'exclude':
            continue
        new, cors, flags = fixer.process(u['text'], u['unit_id'], review_res)
        u['text_raw'] = u['text']
        u['text'] = new
        u['ocr_corrections'] = cors
        u['ocr_flags'] = len(flags)
        all_c += cors
        all_f += flags
    return all_c, all_f


def cmd_run(a):
    log.info('ocrfix', 'loading dictionaries')
    gl = a.glossary or ([str(p) for p in sorted((ROOT / 'glossary').glob('*.csv'))])
    lex = Lexicon(glossary_files=gl, use_hunspell=not a.no_hunspell)
    doc = json.loads(Path(a.document).read_text(encoding='utf-8'))
    out = Path(a.output) if a.output else Path(a.document).with_name('document.corrected.json')
    review_path = Path(a.review) if a.review else out.with_name('ocr_review.json')
    res, accepted = load_review(review_path)
    proposals = load_proposals(review_path)
    fixer = Fixer(lex, suggest=not a.no_suggest, accepted=accepted)
    log.info('ocrfix', '%d units' % len(doc['units']))
    cors, flags = run(doc, fixer, res)
    for f in flags:                      # proposals of ocrreview.py survive a new run
        if f['key'] in proposals:
            f['llm'] = proposals[f['key']]
    doc['ocr_correction'] = {'version': 1, 'corrections': len(cors), 'flags': len(flags),
                             'hunspell': bool(lex.hs), 'review_resolutions_applied': len(res)}
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
    out.with_name('ocr_corrections.json').write_text(json.dumps(
        {'schema': '1.0', 'corrections': cors}, ensure_ascii=False, indent=1), encoding='utf-8')
    resolved = [{'key': k, 'token': k.split('|')[1], 'resolution': v} for k, v in sorted(res.items())]
    review_path.write_text(json.dumps(
        {'schema': '1.0', 'accepted_words': sorted(set(accepted)), 'resolved': resolved,
         'note': 'Fill "resolution" of a flag with the right word, or "=" to accept the word; add words to accepted_words to accept them everywhere. Then run ocrfix.py again (decisions move to "resolved" and are kept).',
         'flags': flags}, ensure_ascii=False, indent=1), encoding='utf-8')
    by = {}
    for f in flags:
        by[f['severity']] = by.get(f['severity'], 0) + 1
    log.info('ocrfix', '%d corrections, %d flags %s -> %s' % (len(cors), len(flags), by or '', out))


def cmd_text(a):
    lex = Lexicon(glossary_files=a.glossary or [str(p) for p in sorted((ROOT / 'glossary').glob('*.csv'))], use_hunspell=not a.no_hunspell)
    fx = Fixer(lex, suggest=not a.no_suggest)
    new, cors, flags = fx.process(a.string, 'text')
    print(new)
    for c in cors:
        print('  fixed  %-22s -> %-22s %s %.1f' % (c['before'], c['after'], c['rule'], c['cost']))
    for f in flags:
        print('  FLAG   %-22s %s/%s %s' % (f['token'], f['reason'], f['severity'], ['%s(%.1f,%s)' % (c['text'], c['cost'], c['rule'][:4]) for c in f['candidates']]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('fetch', help='download the dictionaries')
    for name in ('run', 'text'):
        p = sub.add_parser(name)
        p.add_argument('--glossary', nargs='*')
        p.add_argument('--no-hunspell', action='store_true')
        p.add_argument('--no-suggest', action='store_true', help='skip the slow Hunspell suggestions')
        if name == 'run':
            p.add_argument('document')
            p.add_argument('-o', '--output')
            p.add_argument('--review')
        else:
            p.add_argument('string')
    a = ap.parse_args()
    {'fetch': lambda a: fetch(), 'run': cmd_run, 'text': cmd_text}[a.cmd](a)


if __name__ == '__main__':
    main()

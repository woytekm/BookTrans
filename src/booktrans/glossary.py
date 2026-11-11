#!/usr/bin/env python3
"""Glossary: German -> Polish names and terms, looked up *before* translation and checked *after*.

    python glossary.py import-places train.csv test.csv -o ../../glossary/places.csv   # german,polish CSV -> glossary
    python glossary.py match  document.json ../../glossary/*.csv              # what each unit would get; unknown names
    python glossary.py check  document.json translations.json ../../glossary/*.csv   # glossary terms missing in the Polish text
    python glossary.py candidates document.json ../../glossary/*.csv          # German words to consider adding

Glossary files are CSV (UTF-8) with the columns

    de, pl, kind, status, district, variants, note, source

``kind``: place | term | person.  ``status``: confirmed | proposed | disputed | ambiguous.  ``variants``:
other German spellings, separated by ``;``.  ``district``: disambiguates places of the same name.
Only ``confirmed`` and ``proposed`` entries are given to the model; ``disputed`` and ``ambiguous`` ones
are only reported for a human.  Several files can be combined; the first definition of a (de, kind) wins.

This is a plain lookup (no embeddings): the text of a unit is normalised (case, umlauts and ß folded,
because OCR drops diacritics), entries are matched in it (terms also inside compounds: ``Kämpfer`` finds
``Portalkämpfer``; place names must start a word and tolerate one or two OCR slips), and only the
matching entries go into the prompt.  See ``docs/TRANSLATION.md``.
"""
import argparse
import csv
import difflib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

FIELDS = ['de', 'pl', 'kind', 'status', 'district', 'variants', 'note', 'source']
USABLE = ('confirmed', 'proposed')
_TR = str.maketrans({'ä': 'a', 'ö': 'o', 'ü': 'u', 'Ä': 'a', 'Ö': 'o', 'Ü': 'u', 'ß': 'ss'})


def norm(s):
    """Case-folded, umlaut-free, accent-free text (OCR loses diacritics, Polish ones are not needed for matching)."""
    s = s.translate(_TR).casefold()
    s = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in s if not unicodedata.combining(c)).replace('ł', 'l')


def words(s):
    return re.findall(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", s)


# ---------------------------------------------------------------- files
def load(paths):
    """Entries from one or more CSV files (first definition of a (de, kind) pair wins)."""
    entries, seen = [], set()
    for p in paths:
        with open(p, encoding='utf-8', newline='') as fh:
            for row in csv.DictReader(fh):
                row = {k: (row.get(k) or '').strip() for k in FIELDS}
                if not row['de'] or not row['pl'] or row['de'].startswith('#'):
                    continue
                row['kind'] = row['kind'] or 'term'
                row['status'] = row['status'] or 'proposed'
                key = (norm(row['de']), row['kind'])
                if key in seen:
                    continue
                seen.add(key)
                row['_names'] = [row['de']] + [v.strip() for v in row['variants'].split(';') if v.strip()]
                entries.append(row)
    return entries


def save(entries, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, FIELDS, extrasaction='ignore')
        w.writeheader()
        for e in entries:
            w.writerow(e)


def import_pairs(paths, kind='place', source='german-polish paired placenames (CC-BY 4.0, from German Wikipedia)'):
    """``german,polish`` CSV (header optional) -> glossary entries.  A German name that maps to several
    different Polish names becomes ``ambiguous`` (needs a district); exact duplicates are dropped."""
    pairs = []
    for path in ([paths] if isinstance(paths, (str, Path)) else paths):
        with open(path, encoding='utf-8', newline='') as fh:
            for row in csv.reader(fh):
                if len(row) >= 2 and row[0].strip() and row[1].strip() and row[0].strip().lower() != 'german':
                    pairs.append((row[0].strip(), row[1].strip()))
    by_de = defaultdict(list)
    for de, pl in pairs:
        if pl not in by_de[de]:
            by_de[de].append(pl)
    out = []
    for de, pls in by_de.items():
        for pl in pls:
            amb = len(pls) > 1
            out.append({'de': de, 'pl': pl, 'kind': kind, 'status': 'ambiguous' if amb else 'proposed',
                        'district': '', 'variants': '',
                        'note': ('same German name as: ' + '; '.join(x for x in pls if x != pl)) if amb else '',
                        'source': source})
    return sorted(out, key=lambda e: norm(e['de']))


# ---------------------------------------------------------------- matching
class Matcher:
    def __init__(self, entries):
        self.entries = entries
        self.places = defaultdict(list)          # normalised name (1-3 words) -> entries
        self.fuzzy = defaultdict(list)           # (first letter, length) -> normalised names
        self.terms = []                          # (normalised name, whole_word_only, entry)
        for e in entries:
            for name in e['_names']:
                n = norm(name)
                if e['kind'] == 'term':
                    self.terms.append((n, len(n) < 6, e))
                else:
                    self.places[n].append(e)
                    if len(n) >= 5:
                        self.fuzzy[(n[0], len(n))].append(n)

    def find(self, text):
        """List of ``(entry, matched text)`` for one German text, ambiguous and disputed ones included."""
        hits, seen = [], set()
        toks = words(text)
        nt = [norm(t) for t in toks]
        # terms: substring of the normalised text (compounds), short ones as whole words
        flat = ' ' + ' '.join(nt) + ' '
        for n, whole, e in self.terms:
            if (' ' + n + ' ' in flat) if whole else (n in flat):
                if id(e) not in seen:
                    seen.add(id(e)); hits.append((e, n))
        # places: 1-3 word windows, exact, then one fuzzy try for capitalised words not found
        i = 0
        while i < len(toks):
            found = False
            for span in (3, 2, 1):
                if i + span > len(toks):
                    continue
                key = ' '.join(nt[i:i + span])
                if key in self.places and (span > 1 or toks[i][:1].isupper() or toks[i].isupper()):
                    for e in self.places[key]:
                        if id(e) not in seen:
                            seen.add(id(e)); hits.append((e, ' '.join(toks[i:i + span])))
                    i += span; found = True; break
            if found:
                continue
            t = nt[i]
            if len(t) >= 5 and toks[i][:1].isupper():
                best = None
                for ln in (len(t) - 1, len(t), len(t) + 1):
                    for cand in self.fuzzy.get((t[0], ln), ()):
                        r = difflib.SequenceMatcher(None, t, cand).ratio()
                        if r >= 0.85 and (best is None or r > best[0]):
                            best = (r, cand)
                if best:
                    for e in self.places[best[1]]:
                        if id(e) not in seen:
                            seen.add(id(e)); hits.append((e, toks[i] + ' (~)'))
            i += 1
        return hits


def usable(hits):
    return [(e, m) for e, m in hits if e['status'] in USABLE]


def prompt_block(hits):
    """Glossary lines for the prompt (usable entries only); empty string when nothing matched."""
    lines = []
    for e, _ in usable(hits):
        kind = {'place': 'miejscowość', 'person': 'osoba'}.get(e['kind'], 'termin')
        lines.append('- %s = %s (%s%s)' % (e['de'], e['pl'], kind, ', ' + e['district'] if e['district'] else ''))
    if not lines:
        return ''
    return ('Glossary: use exactly these Polish equivalents (in the nominative; inflect them as the sentence '
            'requires):\n' + '\n'.join(lines))


# ---------------------------------------------------------------- check
def _stems(pl):
    out = []
    for w in words(pl):
        n = norm(w)
        out.append(n[:-2] if len(n) > 5 else n[:max(3, len(n) - 1)])
    return out


def check(pl_text, hits):
    """Usable glossary hits whose Polish form (any inflection: stems compared) is not in ``pl_text``."""
    npl = norm(pl_text)
    return [e for e, _ in usable(hits) if not all(s in npl for s in _stems(e['pl']))]


# ---------------------------------------------------------------- CLI
def _units(doc):
    roles = {}
    for p in doc['pages']:
        for b in p['blocks']:
            if b['kind'] == 'text' and b.get('unit_id'):
                roles.setdefault(b['unit_id'], b['role'])
    return [(u['unit_id'], roles.get(u['unit_id'], u.get('type')), u['text']) for u in doc['units']]


def unknown_names(text, matcher, known_hits):
    """Capitalised words in mid-sentence that are not in the glossary: candidates for missing place names."""
    out = []
    covered = {norm(m.split(' (')[0]) for _, m in known_hits}
    for m in re.finditer(r'(?<![.!?]\s)(?<!^)\b([A-ZÄÖÜ][a-zäöüß]{3,}(?:\s+[A-ZÄÖÜ]{1,3}\b)?)', text):
        w = m.group(1)
        n = norm(w.split()[0])
        if n in covered or n in matcher.places or any(n in t[0] for t in matcher.terms):
            continue
        out.append(w)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('import-places'); p.add_argument('csv', nargs='+', type=Path); p.add_argument('-o', '--output', type=Path, required=True)
    p.add_argument('--kind', default='place')
    p = sub.add_parser('match'); p.add_argument('document', type=Path); p.add_argument('glossary', nargs='+', type=Path)
    p = sub.add_parser('check'); p.add_argument('document', type=Path); p.add_argument('translations', type=Path)
    p.add_argument('glossary', nargs='+', type=Path)
    p = sub.add_parser('candidates'); p.add_argument('document', type=Path); p.add_argument('glossary', nargs='+', type=Path)
    p.add_argument('--min-count', type=int, default=1)
    a = ap.parse_args()

    if a.cmd == 'import-places':
        ents = import_pairs(a.csv, a.kind)
        save(ents, a.output)
        amb = sum(e['status'] == 'ambiguous' for e in ents)
        print('%d entries written to %s (%d ambiguous: same German name, several Polish names)' % (len(ents), a.output, amb))
        return

    m = Matcher(load(a.glossary))
    doc = json.loads(a.document.read_text(encoding='utf-8'))
    if a.cmd == 'match':
        for uid, role, text in _units(doc):
            hits = m.find(text)
            if not hits:
                continue
            print('%s (%s)' % (uid, role))
            for e, mt in hits:
                print('   %-24s -> %-22s [%s/%s]%s' % (mt, e['pl'], e['kind'], e['status'], '  ' + e['note'] if e['note'] else ''))
        for uid, role, text in _units(doc):
            unk = unknown_names(text, m, m.find(text))
            if unk:
                print('%s possible names not in the glossary: %s' % (uid, ', '.join(sorted(set(unk)))))
    elif a.cmd == 'check':
        trans = json.loads(a.translations.read_text(encoding='utf-8'))['units']
        bad = 0
        for uid, role, text in _units(doc):
            if uid not in trans:
                continue
            miss = check(trans[uid]['pl'], m.find(text))
            if miss:
                bad += 1
                print('%s: missing in the Polish text: %s' % (uid, ', '.join('%s (%s)' % (e['pl'], e['de']) for e in miss)))
        print('%d unit(s) with glossary terms missing' % bad)
        sys.exit(1 if bad else 0)
    else:
        # German nouns are capitalised, so only capitalised words inside a sentence are candidates
        known = {norm(n) for e in m.entries for n in e['_names']}
        cnt, ex = Counter(), {}
        for uid, role, text in _units(doc):
            for mt in re.finditer(r'(?<![.!?:]\s)(?<!^)\b([A-ZÄÖÜ][a-zäöüß]{6,})\b', text):
                w = mt.group(1); n = norm(w)
                if n in known or any(k in n for k in known if len(k) >= 6):
                    continue
                cnt[w] += 1
                ex.setdefault(w, '%s: %s' % (uid, text[max(0, mt.start() - 30):mt.end() + 30].replace('\n', ' ')))
        for w, c in cnt.most_common():
            if c >= a.min_count:
                print('%4d  %-28s %s' % (c, w, ex[w]))


if __name__ == '__main__':
    main()

"""The central glossary shared by all books, and a project's own layer (no GUI toolkit, testable alone).

Where things are:

* **central glossary**: every ``*.csv`` in one folder outside the repository, the same for all projects.  The folder is
  set in the user configuration ``~/.booktrans/config.json`` (``{"glossary_dir": "..."}``; ``$BOOKTRANS_CONFIG`` names
  another file) or by ``$BOOKTRANS_GLOSSARY``; default ``~/BookTrans/glossary``.  On first use it is created and seeded
  with the repository's ``glossary/*.csv`` (the repository copy is only the seed from then on).
* **project layer**: ``review/glossary.csv`` of a GUI project: choices for this book only.  It is read first, so its
  entries win over central ones with the same German word and kind (``glossary.load``: first definition wins).
* **rejected**: ``rejected.csv`` in the central folder: words a human said do not belong in the glossary, so the
  harvest (``glossary_harvest.py``) never proposes them again.

Every write is atomic (temporary file + rename): the glossary is human work.
"""
import csv
import json
import os
import shutil
from pathlib import Path

import glossary as G

HERE = Path(__file__).resolve().parent
REPO_GLOSSARY = HERE.parent.parent / 'glossary'
DEFAULT_DIR = Path.home() / 'BookTrans' / 'glossary'
MAIN_FILE = 'terms.csv'                  # where new central entries go
PLACES_FILE = 'places.csv'               # where imported name lists go
REJECTED_FILE = 'rejected.csv'
PROJECT_FILE = 'glossary.csv'            # in a project's review/ folder
KINDS = ('place', 'term', 'person')
STATUSES = ('confirmed', 'proposed', 'disputed', 'ambiguous')


# ---------------------------------------------------------------- configuration
def config_file():
    return Path(os.environ.get('BOOKTRANS_CONFIG') or Path.home() / '.booktrans' / 'config.json')


def read_config():
    try:
        d = json.loads(config_file().read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_config(d):
    p = config_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    os.replace(str(tmp), str(p))


def central_dir():
    """The central glossary folder (not created here)."""
    v = os.environ.get('BOOKTRANS_GLOSSARY') or read_config().get('glossary_dir')
    return Path(os.path.expanduser(v)) if v else DEFAULT_DIR


def set_central_dir(path):
    """Store another central folder in the user configuration (created and seeded when empty)."""
    d = read_config()
    d['glossary_dir'] = str(Path(os.path.expanduser(str(path))).resolve())
    write_config(d)
    return ensure_central()


def ensure_central():
    """The central folder, created and seeded with the repository's glossary files when it has no CSV yet."""
    d = central_dir()
    d.mkdir(parents=True, exist_ok=True)
    if not any(d.glob('*.csv')):
        for f in sorted(REPO_GLOSSARY.glob('*.csv')):
            shutil.copy2(f, d / f.name)
        if not (d / MAIN_FILE).exists():
            G.save([], d / MAIN_FILE)
    return d


def central_files():
    """The central glossary CSVs: ``terms.csv`` first, then the others by name (the rejected list is not a glossary)."""
    d = ensure_central()
    fs = [f for f in sorted(d.glob('*.csv')) if f.name != REJECTED_FILE]
    return sorted(fs, key=lambda f: (f.name != MAIN_FILE, f.name))


def project_file(project_root):
    return Path(project_root) / 'review' / PROJECT_FILE


def layer_files(project_root=None):
    """[(layer, path)] in reading order: the project's file (when it exists), then the central files."""
    out = []
    if project_root is not None and project_file(project_root).exists():
        out.append(('project', project_file(project_root)))
    return out + [('central', f) for f in central_files()]


# ---------------------------------------------------------------- reading and writing
def read_file(path):
    """All rows of one glossary CSV, in file order (nothing merged or dropped)."""
    p = Path(path)
    if not p.exists():
        return []
    with open(p, encoding='utf-8', newline='') as fh:
        return [{k: (row.get(k) or '').strip() for k in G.FIELDS} for row in csv.DictReader(fh)]


def write_file(path, rows):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, G.FIELDS, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in G.FIELDS})
    os.replace(str(tmp), str(p))


def key_of(entry):
    return (G.norm(entry['de']), entry.get('kind') or 'term')


def all_entries(project_root=None):
    """Every row of every layer, each with ``_layer`` and ``_file``; ``_shadowed`` when an earlier layer / file
    defines the same German word and kind (it is not used)."""
    out, seen = [], set()
    for layer, f in layer_files(project_root):
        for r in read_file(f):
            if not r['de'] or r['de'].startswith('#'):
                continue
            r['kind'] = r['kind'] or 'term'
            r['status'] = r['status'] or 'proposed'
            r.update(_layer=layer, _file=str(f), _shadowed=key_of(r) in seen)
            seen.add(key_of(r))
            out.append(r)
    return out


def check_entry(e):
    """Raises ValueError with a readable message for an entry a human typed."""
    if not str(e.get('de', '')).strip() or not str(e.get('pl', '')).strip():
        raise ValueError('German and Polish must not be empty')
    if e.get('kind', 'term') not in KINDS:
        raise ValueError('kind must be one of %s' % ', '.join(KINDS))
    if e.get('status', 'proposed') not in STATUSES:
        raise ValueError('status must be one of %s' % ', '.join(STATUSES))
    if ',' in str(e.get('de')) and e.get('kind') != 'term':
        raise ValueError('a name must not contain a comma (use "variants" for other spellings)')


def upsert(path, entry, old_key=None):
    """Put ``entry`` into the file ``path``: replaces the row with ``old_key`` (or the entry's own key), else appends.
    -> 'added' or 'changed'."""
    check_entry(entry)
    e = {k: str(entry.get(k, '') or '').strip() for k in G.FIELDS}
    e['kind'] = e['kind'] or 'term'
    e['status'] = e['status'] or 'proposed'
    rows = read_file(path)
    target = old_key or key_of(e)
    for i, r in enumerate(rows):
        if r['de'] and key_of(dict(r, kind=r['kind'] or 'term')) == target:
            rows[i] = e
            write_file(path, rows)
            return 'changed'
    rows.append(e)
    write_file(path, rows)
    return 'added'


def delete(path, key):
    """Remove the row with ``key`` from ``path``.  -> True when there was one."""
    rows = read_file(path)
    keep = [r for r in rows if not (r['de'] and key_of(dict(r, kind=r['kind'] or 'term')) == key)]
    if len(keep) == len(rows):
        return False
    write_file(path, keep)
    return True


def add_central(entry):
    return upsert(ensure_central() / MAIN_FILE, entry)


def add_project(project_root, entry):
    return upsert(project_file(project_root), entry)


def promote(project_root, key):
    """Move an entry from the project layer to the central glossary (replacing a central one with the same key in
    whichever central file has it, else into ``terms.csv``).  -> the entry."""
    rows = [r for r in read_file(project_file(project_root)) if r['de'] and key_of(dict(r, kind=r['kind'] or 'term')) == key]
    if not rows:
        raise ValueError('not in the project glossary: %s' % key[0])
    e = rows[0]
    target = ensure_central() / MAIN_FILE
    for f in central_files():
        if any(r['de'] and key_of(dict(r, kind=r['kind'] or 'term')) == key for r in read_file(f)):
            target = f
            break
    upsert(target, e)
    delete(project_file(project_root), key)
    return e


# ---------------------------------------------------------------- rejected words
def rejected():
    """Keys of words a human rejected for the glossary."""
    return {key_of(r) for r in read_file(ensure_central() / REJECTED_FILE) if r['de']}


def reject(de, kind, note=''):
    p = ensure_central() / REJECTED_FILE
    e = {'de': de, 'pl': '-', 'kind': kind or 'term', 'status': 'disputed', 'note': note, 'source': 'rejected by a human'}
    if key_of(e) not in {key_of(r) for r in read_file(p) if r['de']}:
        rows = read_file(p) + [e]
        write_file(p, rows)


# ---------------------------------------------------------------- import
def import_pairs_central(paths, kind='place', source=None):
    """``german,polish`` lists (``glossary.import_pairs``) into the central ``places.csv``.  Entries the central
    glossary already has (same German word and kind, any file) are left alone: human work wins over a list.
    -> (added, skipped)."""
    new = G.import_pairs(paths, kind=kind, **({'source': source} if source else {}))
    have = {key_of(r) for f in central_files() for r in read_file(f) if r['de']}
    target = ensure_central() / PLACES_FILE
    rows = read_file(target)
    added = skipped = 0
    keys_in_new = {}
    for e in new:
        k = key_of(e)
        if k in have:
            skipped += 1
            continue
        if k in keys_in_new:                      # several Polish names for one German one: kept as one ambiguous row
            r = keys_in_new[k]
            r['status'] = 'ambiguous'
            r['note'] = (r['note'] + '; ' if r['note'] else 'also: ') + e['pl']
            continue
        keys_in_new[k] = e
        rows.append(e)
        added += 1
    write_file(target, rows)
    return added, skipped

#!/usr/bin/env python3
"""Why did a translation run miss the cache?  For a cache made before ``translate.py`` recorded what each answer was
made from: try the earlier versions of the prompt, the rules and the glossary (from the git history of this
repository) and report which combination the cached answers were made with.

    python cache_explain.py <project folder> [--model opus] [--glossary FILE ...]

The project folder is a GUI project (``translation/translations.cache.json``, the book's ``document.corrected.json``).
``--model``: as in the translation settings (default: the project's, else ``opus``).  Nothing is changed.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import glossary as G
import glossary_store as GS
import translate as T
from project import Project

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT)] + list(args), capture_output=True, text=True, check=True).stdout


def versions(path, limit=12):
    """[(label, text)] of a repository file: now, then the commits that changed it, newest first."""
    out = [('now', (ROOT / path).read_text(encoding='utf-8').strip())]
    try:
        for line in git('log', '--format=%h %ad %s', '--date=format:%Y-%m-%d %H:%M', '-%d' % limit, '--', path).splitlines():
            h = line.split()[0]
            try:
                txt = git('show', '%s:%s' % (h, path)).strip()
            except subprocess.CalledProcessError:
                continue
            if txt not in [t for _, t in out]:
                out.append((line[:70], txt))
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return out


def glossary_versions(extra):
    """[(label, entries)]: the central glossary now, the repository's glossary/ at its commits, and ``extra`` files."""
    out = [('central glossary now', G.load(GS.central_files()))]
    tmp = Path(tempfile.mkdtemp())
    try:
        for line in git('log', '--format=%h %ad %s', '--date=format:%Y-%m-%d %H:%M', '-8', '--', 'glossary').splitlines():
            h = line.split()[0]
            names = [n for n in git('ls-tree', '--name-only', h, 'glossary/').split() if n.endswith('.csv')]
            files = []
            for n in names:
                f = tmp / h / Path(n).name
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(git('show', '%s:%s' % (h, n)), encoding='utf-8')
                files.append(f)
            out.append(('repository glossary at ' + line[:60], G.load(sorted(files))))
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    if extra:
        out.append(('given files', G.load([Path(e) for e in extra])))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project', type=Path)
    ap.add_argument('--model', default=None)
    ap.add_argument('--glossary', nargs='*', default=None)
    a = ap.parse_args()
    p = Project.load(a.project)
    model = a.model or str(p.data.get('translation', {}).get('model') or '').strip() or 'opus'
    cache_f = p.translations_file.with_name('translations.cache.json')
    cache = json.loads(cache_f.read_text(encoding='utf-8'))
    keys = {k for k in cache if k != '_meta'}
    doc_f = p.book_dir() / 'document.corrected.json'
    if not doc_f.exists():
        doc_f = p.book_dir() / 'document.json'
    doc = json.loads(doc_f.read_text(encoding='utf-8'))
    units = [u for u in T.collect(doc)]
    bases = versions('prompts/translate_pl.txt')
    ruless = versions('prompts/translate_rules_pl.txt')
    gloss = glossary_versions(a.glossary)
    print('cache: %d answers; document: %d units; model %r' % (len(keys), len(units), model))
    found = Counter()
    for gl_label, entries in gloss:
        m = G.Matcher(entries)
        blocks, prev = [], {}
        for uid, text, role, upper, status in units:
            ctx = T.last_sentence(prev[role]) if role == 'paragraph' and role in prev else None
            prev[role] = text
            if status == 'translate' and text.strip():
                blocks.append((text, role, upper, ctx, G.prompt_block(m.find(text))))
        for bl, base in bases:
            for rl, rules in ruless:
                hits = sum(T.key_of(model, base, rules, blk, role, upper, text, ctx) in keys
                           for text, role, upper, ctx, blk in blocks)
                if hits:
                    found[(bl, rl, gl_label)] = hits
        if gl_label == gloss[0][0]:
            n_units = len(blocks)
    if not found:
        print('No cached answer matches any combination: the model name, the document text or the context differ '
              '(try --model with the model the translation used).')
        return 1
    print('\ncached answers that match the units of the current document (of %d units):' % n_units)
    for (bl, rl, gl), n in found.most_common(8):
        print('  %4d  prompt: %s\n        rules:  %s\n        glossary: %s' % (n, bl, rl, gl))
    best = found.most_common(1)[0]
    if best[0][:2] != ('now', 'now'):
        print('\n=> the cached answers were made with an earlier prompt / rules version; the current one has a different '
              'cache key, so every unit was translated again.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

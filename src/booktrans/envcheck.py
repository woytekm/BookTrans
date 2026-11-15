#!/usr/bin/env python3
"""Is the environment intact?  Checks what the pipeline and the GUI need (no GUI toolkit needed to run it).

    python envcheck.py                 # general check
    python envcheck.py PROJECT         # plus the project's input folder and its parameters
    python envcheck.py --lang deu+eng  # check another OCR language

Exit code 1 when something that blocks a stage is missing (status ``fail``).  Statuses:

* ``ok``    - present and plausible;
* ``warn``  - works, but degraded or suspicious (fast OCR model, optional module missing);
* ``fail``  - a stage cannot run (module, Tesseract, dictionaries of an enabled OCR correction);
* ``info``  - for your information (version, size, a check that is not needed for this project).

Nothing here touches the network.  A check never raises: a problem is reported as a ``fail``/``warn`` row.
"""
import csv
import importlib.metadata as md
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

OK, WARN, FAIL, INFO = 'ok', 'warn', 'fail', 'info'
Check = namedtuple('Check', 'group name status detail hint')

# (import name, distribution names, needed to run the pipeline, what for, pip name)
MODULES = [
    ('numpy', ['numpy'], True, 'image arrays (all stages)', 'numpy'),
    ('cv2', ['opencv-python', 'opencv-python-headless', 'opencv-contrib-python'], True, 'layout analysis and deskew (stages 1-3)', 'opencv-python'),
    ('PIL', ['Pillow'], True, 'image input/output, page assembly', 'Pillow'),
    ('pytesseract', ['pytesseract'], True, 'OCR (stages 1-2)', 'pytesseract'),
    ('pyphen', ['pyphen'], False, 'Polish hyphenation when pages are rebuilt (render.py)', 'pyphen'),
]
# Files the OCR correction (ocrfix.py) downloads with ``python ocrfix.py fetch``.  Kept in step with
# ocrfix.SOURCES by tests/test_envcheck.py.
DICT_FILES = ('de-100k.txt', 'de_DE_frami.dic', 'de_DE_frami.aff')
FONTS = ('CrimsonText-Regular.ttf', 'CrimsonText-Bold.ttf', 'CrimsonText-Italic.ttf', 'CrimsonText-SemiBold.ttf')
PROMPTS = ('translate_pl.txt', 'translate_rules_pl.txt', 'ocr_review_de.txt')
TESSERACT_DIRS = ('/opt/local/bin', '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin')   # MacPorts, Homebrew, ...
# Size of deu.traineddata: tessdata_fast is about 1.5 MB, tessdata_best about 8.6 MB.  Below this limit it is
# surely the fast model.  The size cannot tell tessdata_best from the standard "tessdata" model.
FAST_MODEL_MAX = 3 * 1024 * 1024

G_PY, G_MOD, G_OCR, G_FIX, G_RENDER, G_PROJECT = ('Python and GUI', 'Pipeline modules', 'OCR engine',
                                                 'OCR correction (stage 5)', 'Fonts and prompts', 'Project')


def _mb(n):
    return '%.1f MB' % (n / 1048576.0)


# ------------------------------------------------------------------ python, GUI, modules
def check_python():
    out = [Check(G_PY, 'Python', INFO, '%s  (%s)' % (sys.version.split()[0], sys.executable), '')]
    try:
        import tkinter
        out.append(Check(G_PY, 'tkinter (GUI toolkit)', OK, 'Tk %s' % tkinter.TkVersion, ''))
    except Exception as e:   # ImportError, or a Tcl/Tk that does not load
        out.append(Check(G_PY, 'tkinter (GUI toolkit)', FAIL, 'not importable: %s' % e,
                         'MacPorts: sudo port install py%d%d-tkinter' % sys.version_info[:2]))
    try:
        from PIL import ImageTk   # noqa: F401
        out.append(Check(G_PY, 'Pillow ImageTk (page images in the GUI)', OK, '', ''))
    except Exception as e:
        out.append(Check(G_PY, 'Pillow ImageTk (page images in the GUI)', FAIL, 'not importable: %s' % e,
                         'pip install Pillow (needs tkinter first)'))
    return out


def module_version(dists):
    for d in dists:
        try:
            return md.version(d)
        except md.PackageNotFoundError:
            continue
    return ''


def check_modules(need_ocrfix=True):
    out = []
    for imp, dists, needed, what, pip in MODULES:
        found = importlib.util.find_spec(imp) is not None
        ver = module_version(dists) if found else ''
        if found:
            out.append(Check(G_MOD, imp, OK, ver or 'present', ''))
        else:
            out.append(Check(G_MOD, imp, FAIL if needed else WARN, 'missing: %s' % what,
                             'pip install %s%s' % (pip, '   (only needed to run the pipeline, not to review results)' if needed else '')))
    for imp, dist, what, severity in (('symspellpy', 'symspellpy', 'OCR correction (stage 5) does not run without it', FAIL),
                                      ('spylls', 'spylls', 'without Hunspell more words are flagged and fewer corrected', WARN)):
        found = importlib.util.find_spec(imp) is not None
        if found:
            out.append(Check(G_FIX, imp, OK, module_version([dist]) or 'present', ''))
        else:
            out.append(Check(G_FIX, imp, severity if need_ocrfix else INFO,
                             ('missing: ' if need_ocrfix else 'not needed (OCR correction is off): ') + what,
                             'pip install %s' % dist))
    return out


# ------------------------------------------------------------------ tesseract
def find_tesseract():
    """-> (path or None, on_path).  Looks beside PATH in the usual package-manager folders: an app started from
    Finder has a short PATH and would not find a MacPorts install."""
    exe = shutil.which('tesseract')
    if exe:
        return exe, True
    for d in TESSERACT_DIRS:
        p = Path(d) / 'tesseract'
        if p.is_file() and os.access(str(p), os.X_OK):
            return str(p), False
    return None, False


def run_cmd(args, timeout=8):
    """-> (returncode or None, combined output).  Never raises."""
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError) as e:
        return None, str(e)


def parse_langs(output):
    """Output of ``tesseract --list-langs`` -> (tessdata folder or None, [languages])."""
    folder = None
    langs = []
    for i, ln in enumerate(output.splitlines()):
        ln = ln.strip()
        if i == 0 and ln.lower().startswith('list of available languages'):
            m = re.search(r'in "([^"]+)"', ln)
            folder = m.group(1) if m else None
            continue
        if ln and re.fullmatch(r'[A-Za-z0-9_]+', ln):
            langs.append(ln)
    return folder, langs


def check_tesseract(lang='deu', find=find_tesseract, run=run_cmd):
    exe, on_path = find()
    if not exe:
        return [Check(G_OCR, 'tesseract program', FAIL, 'not found',
                      'MacPorts: sudo port install tesseract tesseract-deu   (the pipeline cannot OCR without it)')]
    out = []
    rc, text = run([exe, '--version'])
    ver = text.splitlines()[0].strip() if rc == 0 and text.strip() else 'version unknown'
    if on_path:
        out.append(Check(G_OCR, 'tesseract program', OK, '%s  (%s)' % (ver, exe), ''))
    else:
        out.append(Check(G_OCR, 'tesseract program', WARN, '%s  (%s, not on PATH)' % (ver, exe),
                         'pytesseract looks it up on PATH: start the GUI/pipeline from a shell where "which tesseract" works'))
    rc, text = run([exe, '--list-langs'])
    folder, langs = parse_langs(text) if rc == 0 else (None, [])
    for lg in lang.split('+'):
        if rc != 0:
            out.append(Check(G_OCR, 'language %s' % lg, WARN, 'cannot list languages: %s' % text.strip()[:120], ''))
            continue
        if lg not in langs:
            out.append(Check(G_OCR, 'language %s' % lg, FAIL, 'not installed (found: %s)' % (', '.join(langs) or 'none'),
                             'MacPorts: sudo port install tesseract-%s' % lg))
            continue
        f = Path(folder) / ('%s.traineddata' % lg) if folder else None
        if f is not None and f.is_file():
            size = f.stat().st_size
            if lg == 'deu' and size < FAST_MODEL_MAX:
                out.append(Check(G_OCR, 'language deu', WARN, '%s, %s: looks like the fast model' % (f, _mb(size)),
                                 'copy deu.traineddata from github.com/tesseract-ocr/tessdata_best (HANDOFF.md section 2: it reads the headings correctly)'))
            else:
                out.append(Check(G_OCR, 'language %s' % lg, OK, '%s, %s%s' % (f, _mb(size),
                                 '  (not the fast model; size cannot tell tessdata_best from the standard model)' if lg == 'deu' else ''), ''))
        else:
            out.append(Check(G_OCR, 'language %s' % lg, OK, 'installed', ''))
    return out


# ------------------------------------------------------------------ OCR correction data
def _fix_status(need_ocrfix, missing_status):
    return missing_status if need_ocrfix else INFO


def check_dictionaries(root=ROOT, need_ocrfix=True):
    d = Path(root) / 'dictionaries'
    hint = 'cd src/booktrans && python ocrfix.py fetch'
    out = []
    p = d / 'de-100k.txt'
    if not p.is_file() or p.stat().st_size == 0:
        out.append(Check(G_FIX, 'de-100k.txt (frequency list)', _fix_status(need_ocrfix, FAIL), 'missing or empty: %s' % p, hint))
    else:
        valid = bad = 0
        for ln in p.read_text(encoding='utf-8', errors='replace').splitlines():
            parts = ln.split()
            if len(parts) == 2 and parts[1].isdigit():
                valid += 1
            elif ln.strip():
                bad += 1
        if valid == 0:
            out.append(Check(G_FIX, 'de-100k.txt (frequency list)', _fix_status(need_ocrfix, FAIL), 'no valid "word count" lines: not the right file', hint))
        elif valid < 50000 or bad > valid // 100:
            out.append(Check(G_FIX, 'de-100k.txt (frequency list)', _fix_status(need_ocrfix, WARN),
                             '%d entries, %d unreadable lines (expected about 100,000): truncated or damaged?' % (valid, bad), hint))
        else:
            out.append(Check(G_FIX, 'de-100k.txt (frequency list)', OK, '{:,} entries'.format(valid), ''))
    p = d / 'de_DE_frami.dic'
    if not p.is_file() or p.stat().st_size == 0:
        out.append(Check(G_FIX, 'de_DE_frami.dic (Hunspell words)', _fix_status(need_ocrfix, WARN), 'missing or empty', hint))
    else:
        lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
        head = lines[0].strip().lstrip('﻿') if lines else ''
        if head.isdigit() and abs(len(lines) - 1 - int(head)) <= max(10, int(head) // 20):
            out.append(Check(G_FIX, 'de_DE_frami.dic (Hunspell words)', OK, '{:,} entries'.format(int(head)), ''))
        else:
            out.append(Check(G_FIX, 'de_DE_frami.dic (Hunspell words)', _fix_status(need_ocrfix, WARN),
                             'header says %r, file has %d lines: truncated or not a Hunspell .dic' % (head[:12], len(lines)), hint))
    p = d / 'de_DE_frami.aff'
    if not p.is_file() or p.stat().st_size == 0:
        out.append(Check(G_FIX, 'de_DE_frami.aff (Hunspell rules)', _fix_status(need_ocrfix, WARN), 'missing or empty', hint))
    else:
        text = p.read_text(encoding='utf-8', errors='replace')
        if 'SFX ' in text or 'PFX ' in text:
            out.append(Check(G_FIX, 'de_DE_frami.aff (Hunspell rules)', OK, _mb(p.stat().st_size), ''))
        else:
            out.append(Check(G_FIX, 'de_DE_frami.aff (Hunspell rules)', _fix_status(need_ocrfix, WARN), 'no affix rules found: not a Hunspell .aff', hint))
    return out


def check_glossary(root=ROOT):
    g = Path(root) / 'glossary'
    out = []
    v = g / 'vocabulary.txt'
    if v.is_file():
        n = sum(1 for ln in v.read_text(encoding='utf-8', errors='replace').splitlines() if ln.split('#')[0].strip())
        out.append(Check(G_FIX, 'glossary/vocabulary.txt', OK, '%d book-specific words' % n, 'add words that the dictionaries do not know (old spelling, place names)' if n == 0 else ''))
    else:
        out.append(Check(G_FIX, 'glossary/vocabulary.txt', WARN, 'missing', 'create it: one word per line, "#" starts a comment'))
    csvs = sorted(g.glob('*.csv'))
    if not csvs:
        out.append(Check(G_FIX, 'glossary/*.csv (German -> Polish names)', WARN, 'no glossary files', 'translate.py and ocrfix.py use them for names and terms'))
    for c in csvs:
        try:
            with c.open(encoding='utf-8', newline='') as fh:
                rd = csv.DictReader(fh)
                cols = rd.fieldnames or []
                rows = sum(1 for _ in rd)
            if 'de' in cols and 'pl' in cols:
                out.append(Check(G_FIX, 'glossary/%s' % c.name, OK, '%d entries' % rows, ''))
            else:
                out.append(Check(G_FIX, 'glossary/%s' % c.name, WARN, 'header %s has no "de" and "pl" column' % cols[:4], ''))
        except (OSError, UnicodeDecodeError, csv.Error) as e:
            out.append(Check(G_FIX, 'glossary/%s' % c.name, FAIL, 'cannot read: %s' % e, ''))
    return out


def check_assets(root=ROOT):
    out = []
    fonts = Path(root) / 'fonts'
    for f in FONTS:
        ok = (fonts / f).is_file() and (fonts / f).stat().st_size > 0
        out.append(Check(G_RENDER, 'fonts/%s' % f, OK if ok else (FAIL if 'Regular' in f else WARN),
                         '' if ok else 'missing', '' if ok else 'page rebuilding (render.py) needs the bundled Crimson Text'))
    for p in PROMPTS:
        ok = (Path(root) / 'prompts' / p).is_file()
        out.append(Check(G_RENDER, 'prompts/%s' % p, OK if ok else WARN, '' if ok else 'missing', '' if ok else 'needed by translate.py / ocrreview.py'))
    return out


# ------------------------------------------------------------------ project
def check_project(project):
    from project import tiffs_in
    out = []
    inp = Path(project.input_dir)
    if not inp.is_dir():
        return [Check(G_PROJECT, 'input folder', FAIL, 'does not exist: %s' % inp, 'the scans were moved or the drive is not mounted')]
    t = tiffs_in(inp)
    if not t:
        return [Check(G_PROJECT, 'input folder', FAIL, 'no .tif/.tiff files in %s' % inp, '')]
    total = sum(p.stat().st_size for p in t)
    out.append(Check(G_PROJECT, 'input folder', OK, '%d TIFF files, %.2f GB  (%s)' % (len(t), total / 1e9, inp), ''))
    if len(t) == 1 and project.params['layout'] == 'spread':
        out.append(Check(G_PROJECT, 'single TIFF', INFO,
                         'booktrans.py writes a single input to pipeline/%s/ (not pipeline/%s/)' % (t[0].stem, project.name), ''))
    try:
        from PIL import Image
        with Image.open(t[0]) as im:
            out.append(Check(G_PROJECT, 'first TIFF opens', OK, '%s: %dx%d px' % (t[0].name, im.size[0], im.size[1]), ''))
    except Exception as e:
        out.append(Check(G_PROJECT, 'first TIFF opens', FAIL, '%s: %s' % (t[0].name, e), 'damaged file or unsupported TIFF variant'))
    try:
        probe = project.root if project.root.exists() else project.root.parent
        free = shutil.disk_usage(str(probe)).free
        st = OK if free > total else WARN
        out.append(Check(G_PROJECT, 'free disk space', st, '%.1f GB free where the project lives (inputs total %.1f GB)' % (free / 1e9, total / 1e9),
                         '' if st == OK else 'deskewed full-resolution pages are written next to the results'))
    except OSError as e:
        out.append(Check(G_PROJECT, 'free disk space', INFO, 'cannot determine: %s' % e, ''))
    if project.root.exists() and not os.access(str(project.root), os.W_OK):
        out.append(Check(G_PROJECT, 'project folder writable', FAIL, str(project.root), ''))
    return out


# ------------------------------------------------------------------ all
def run_checks(root=ROOT, project=None, params=None, lang=None):
    """The full report as a list of ``Check``.  ``project`` (a project.Project) adds its input folder and
    parameters; ``params`` / ``lang`` override them for a project that does not exist yet."""
    p = dict(project.params) if project is not None else {}
    p.update(params or {})
    lang = lang or p.get('lang', 'deu')
    need_fix = p.get('ocrfix', 'strict') != 'off'
    out = []
    out += check_python()
    out += check_modules(need_fix)
    out += check_tesseract(lang)
    out += check_dictionaries(root, need_fix)
    out += check_glossary(root)
    out += check_assets(root)
    if project is not None:
        out += check_project(project)
    return out


def worst(checks):
    """Most severe status: fail > warn > ok."""
    s = {c.status for c in checks}
    return FAIL if FAIL in s else WARN if WARN in s else OK


def format_report(checks):
    mark = {OK: ' ok ', WARN: 'warn', FAIL: 'FAIL', INFO: 'info'}
    lines, group = [], None
    for c in checks:
        if c.group != group:
            group = c.group
            lines.append('')
            lines.append(group)
        lines.append('  [%s] %-44s %s' % (mark[c.status], c.name, c.detail))
        if c.hint and c.status in (WARN, FAIL):
            lines.append('         -> %s' % c.hint)
    return '\n'.join(lines).strip()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('project', nargs='?', help='project folder or project.json')
    ap.add_argument('--lang', help='OCR language to check (default: the project\'s or deu)')
    a = ap.parse_args(argv)
    proj = None
    if a.project:
        sys.path.insert(0, str(HERE))
        from project import Project, ProjectError
        try:
            proj = Project.load(a.project)
        except ProjectError as e:
            raise SystemExit(str(e))
    checks = run_checks(project=proj, lang=a.lang)
    print(format_report(checks))
    w = worst(checks)
    print('\nresult: %s' % {OK: 'all good', WARN: 'works, with warnings', FAIL: 'something is missing'}[w])
    return 1 if w == FAIL else 0


if __name__ == '__main__':
    sys.path.insert(0, str(HERE))
    sys.exit(main())

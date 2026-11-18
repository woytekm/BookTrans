#!/usr/bin/env python3
"""GUI project: folder layout, ``project.json`` and the pipeline command (no GUI toolkit, testable alone).

A project is one folder, chosen by the user as "output directory" + project name::

    <project>/project.json       name, input folder, pipeline parameters (this module)
    <project>/pipeline/<book>/   output of booktrans.py (-o <project>/pipeline --name <book>); a re-run may wipe it
    <project>/review/            human decisions made in the GUI; never written or deleted by the pipeline
    <project>/translation/       translations.json (+ its .cache.json) of translate.py; outside pipeline/ for the same reason
    <project>/render/            the rebuilt pages of render.py (page PNGs, compare, layout.json, report.json)

The split is deliberate: ``booktrans.py`` removes its result folder before a full (non ``--resume``) run, so
nothing a human typed may live inside it (see docs/GUI.md, decision D4).
"""
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

SCHEMA = '1.2'           # 1.1: "translation" settings; 1.2: "render" settings (older files load with the defaults)
KIND = 'booktrans-project'
FILE = 'project.json'
LAYOUTS = ('spread', 'page', 'crop')
OCRFIX_MODES = ('strict', 'aggressive', 'off')
TIFF_SUFFIXES = ('.tif', '.tiff')
DEFAULT_PARAMS = {
    'lang': 'deu',          # Tesseract language(s), e.g. deu or deu+eng
    'layout': 'spread',     # spread | page | crop  (booktrans.py --layout)
    'scale': 'auto',        # auto | positive number (booktrans.py --scale)
    'ocr_scale': 2.0,       # booktrans.py --ocr-scale
    'ocrfix': 'strict',     # strict | aggressive | off  (stage 5, ocrfix.py)
    'ocr_suggest': False,   # ocrfix: slow Hunspell suggestions
}
PROVIDERS = ('claude-code', 'openai', 'anthropic')
TR_SOURCES = ('corrected', 'raw')
DEFAULT_TRANSLATION = {
    'provider': 'claude-code',  # claude-code (your subscription) | openai (LM Studio ...) | anthropic (API key)
    'model': '',                # '' = translate.py's default for the provider (claude-code: opus)
    'endpoint': '',             # openai / anthropic only; '' = translate.py's default ($LMSTUDIO_ENDPOINT, localhost)
    'glossary': '',             # glossary CSV files separated by ';' ('' = the repository's glossary/*.csv)
    'source': 'corrected',      # corrected: document.corrected.json when it exists | raw: document.json (--raw)
    'timeout': 600,             # seconds per request
    'retries': 2,               # extra attempts when a check fails
    'no_think': False,          # openai: ask a reasoning model not to think
}

BACKGROUNDS = ('paper', 'scan')
RESOLUTIONS = ('auto', 'work')
DEFAULT_RENDER = {
    'background': 'paper',      # paper: clean canvas | scan: the scan with the text wiped (render.py --background)
    'resolution': 'auto',       # auto: full resolution when the scan has it | work: analysis scale (fast previews)
    'font': '',                 # regular TTF or path@weight ('' = the default font, config/fonts.json); relative to the project
    'bold_font': '',
    'italic_font': '',
    'tracking': 'auto',         # auto: fitted | letter-spacing in em, e.g. 0.05
    'size_scale': 1.0,          # multiplies the fitted font size
    'word_space': 1.0,          # multiplies the natural word space
    'paragraph_indent': '',     # '' = as in the original | first-line indent in em
    'page_fit': True,           # fit size and letter-spacing of the body text per page
    'ink': 'auto',              # text colour: auto (measured from the scan) | R,G,B
}


class ProjectError(Exception):
    """A problem the user can fix (bad name, missing file, ...); the message is shown as is."""


def tiffs_in(folder):
    """The TIFFs of a folder in name order, the same selection ``booktrans.py`` makes."""
    f = Path(folder)
    if not f.is_dir():
        return []
    return sorted(p for p in f.iterdir() if p.suffix.lower() in TIFF_SUFFIXES and not p.name.startswith('.'))


def valid_name(name):
    """A project/book name is a folder name: no separators or characters Windows/macOS refuse."""
    return bool(name) and name == name.strip() and not name.startswith('.') and not re.search(r'[\\/:*?"<>|\x00]', name)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, data, indent=1):
    """Write atomically (temp file + rename): a crash never leaves a half-written project or review file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding='utf-8')
    os.replace(str(tmp), str(path))


def check_params(p):
    """Validate pipeline parameters; raises ProjectError with a readable message."""
    if p.get('layout') not in LAYOUTS:
        raise ProjectError('layout must be one of %s' % ', '.join(LAYOUTS))
    if p.get('ocrfix') not in OCRFIX_MODES:
        raise ProjectError('OCR correction must be one of %s' % ', '.join(OCRFIX_MODES))
    if not re.fullmatch(r'[A-Za-z_]+(\+[A-Za-z_]+)*', str(p.get('lang', ''))):
        raise ProjectError('language must look like deu or deu+eng')
    s = str(p.get('scale', ''))
    if s != 'auto':
        try:
            if float(s) <= 0:
                raise ValueError
        except ValueError:
            raise ProjectError('scale must be "auto" or a positive number')
    try:
        if float(p.get('ocr_scale')) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ProjectError('OCR scale must be a positive number')
    return p


def glossary_files(spec, base=None):
    """'a.csv; ~/b.csv' -> [Path, ...]; relative paths are taken from ``base`` (the project folder)."""
    out = []
    for part in str(spec or '').split(';'):
        part = part.strip()
        if part:
            q = Path(os.path.expanduser(part))
            out.append(q if q.is_absolute() or base is None else Path(base) / q)
    return out


def _font_path(spec, base):
    """A font setting (``path`` or ``path@weight`` for a variable font) with a relative path made absolute."""
    s = str(spec)
    path, wght = (s.rsplit('@', 1) + [''])[:2] if '@' in s else (s, '')
    p = Path(os.path.expanduser(path))
    p = p if p.is_absolute() or base is None else Path(base) / p
    return Path('%s@%s' % (p, wght)) if wght else p


def _font_file(spec, base):
    s = str(_font_path(spec, base))
    return Path(s.rsplit('@', 1)[0]) if '@' in s else Path(s)


def check_render(r, base=None):
    """Validate render settings; raises ProjectError with a readable message."""
    if r.get('background') not in BACKGROUNDS:
        raise ProjectError('background must be one of %s' % ', '.join(BACKGROUNDS))
    if r.get('resolution') not in RESOLUTIONS:
        raise ProjectError('resolution must be one of %s' % ', '.join(RESOLUTIONS))
    for k in ('font', 'bold_font', 'italic_font'):
        v = str(r.get(k) or '').strip()
        if v and not _font_file(v, base).is_file():
            raise ProjectError('%s: file not found: %s' % (k.replace('_', ' '), v))
    tr = str(r.get('tracking', 'auto')).strip()
    if tr != 'auto':
        try:
            if not 0 <= float(tr) <= 0.5:
                raise ValueError
        except ValueError:
            raise ProjectError('letter-spacing must be "auto" or a number of em from 0 to 0.5')
    for k, lo, hi in (('size_scale', 0.5, 1.5), ('word_space', 0.5, 3.0)):
        try:
            if not lo <= float(r.get(k)) <= hi:
                raise ValueError
        except (TypeError, ValueError):
            raise ProjectError('%s must be a number from %g to %g' % (k.replace('_', ' '), lo, hi))
    ind = str(r.get('paragraph_indent', '')).strip()
    if ind:
        try:
            if not 0 <= float(ind) <= 5:
                raise ValueError
        except ValueError:
            raise ProjectError('paragraph indent must be empty or a number of em from 0 to 5')
    if str(r.get('ink', '')).strip() == 'auto':
        return r
    try:
        ink = [int(v) for v in str(r.get('ink')).split(',')]
        if len(ink) != 3 or not all(0 <= v <= 255 for v in ink):
            raise ValueError
    except ValueError:
        raise ProjectError('ink must be "auto" or R,G,B with values 0 to 255')
    return r


def check_translation(t, base=None):
    """Validate translation settings; raises ProjectError with a readable message."""
    if t.get('provider') not in PROVIDERS:
        raise ProjectError('translation engine must be one of %s' % ', '.join(PROVIDERS))
    if t.get('source') not in TR_SOURCES:
        raise ProjectError('source text must be one of %s' % ', '.join(TR_SOURCES))
    if re.search(r'\s', str(t.get('model', '')).strip()):
        raise ProjectError('model must be a single word such as opus, sonnet or a model id')
    try:
        if float(t.get('timeout')) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ProjectError('timeout must be a positive number of seconds')
    try:
        if int(str(t.get('retries'))) < 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ProjectError('retries must be a whole number, 0 or more')
    missing = [str(g) for g in glossary_files(t.get('glossary'), base) if not g.is_file()]
    if missing:
        raise ProjectError('glossary file not found: %s' % ', '.join(missing))
    return t


class Project:
    def __init__(self, root, data):
        self.root = Path(root)
        self.data = data

    # ------------------------------------------------------------ create / load / save
    @classmethod
    def create(cls, root, name, input_dir, params=None):
        if not valid_name(name):
            raise ProjectError('"%s" is not a usable project name (no slashes, no leading dot)' % name)
        root = Path(root)
        if (root / FILE).exists():
            raise ProjectError('there is already a project in %s' % root)
        p = dict(DEFAULT_PARAMS)
        p.update(params or {})
        check_params(p)
        data = {'kind': KIND, 'schema': SCHEMA, 'name': name,
                'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'input_dir': os.path.abspath(os.path.expanduser(str(input_dir))), 'params': p,
                'translation': dict(DEFAULT_TRANSLATION), 'render': dict(DEFAULT_RENDER)}
        pr = cls(root, data)
        pr.save()
        pr.review_dir.mkdir(parents=True, exist_ok=True)
        return pr

    @classmethod
    def load(cls, path):
        """``path``: the project folder or its ``project.json``."""
        p = Path(path)
        f = p / FILE if p.is_dir() else p
        if not f.is_file():
            raise ProjectError('there is no %s in %s' % (FILE, p))
        try:
            data = read_json(f)
        except (OSError, ValueError) as e:
            raise ProjectError('cannot read %s: %s' % (f, e))
        if not isinstance(data, dict) or data.get('kind') != KIND:
            raise ProjectError('%s is not a BookTrans project file' % f)
        if str(data.get('schema', '')).split('.')[0] != SCHEMA.split('.')[0]:
            raise ProjectError('%s has project schema %s; this version reads %s' % (f, data.get('schema'), SCHEMA))
        params = dict(DEFAULT_PARAMS)
        params.update(data.get('params') or {})
        data['params'] = params
        tr = dict(DEFAULT_TRANSLATION)
        tr.update(data.get('translation') or {})
        data['translation'] = tr
        rd = dict(DEFAULT_RENDER)
        rd.update(data.get('render') or {})
        data['render'] = rd
        return cls(f.parent, data)

    def save(self):
        write_json(self.file, self.data)

    # ------------------------------------------------------------ paths
    @property
    def file(self):
        return self.root / FILE

    @property
    def name(self):
        return self.data['name']

    @property
    def input_dir(self):
        return self.data['input_dir']

    def input_change_warnings(self, new_dir):
        """Sentences to show before the input folder is changed to ``new_dir`` (empty when nothing is at stake)."""
        new = os.path.abspath(os.path.expanduser(str(new_dir)))
        if new == self.input_dir:
            return []
        out = []
        t = tiffs_in(new)
        if not t:
            out.append('There are no TIFF files in %s.' % new)
        if (self.book_dir() / 'document.json').exists() or any(self.pipeline_root.glob('*/stages')):
            out.append('The pipeline result in %s was made from %s. Run the pipeline without --resume (or use Clear project '
                       'data first) to process the new folder: with --resume, spreads whose TIFF has the same name in both '
                       'folders would be kept from the old one.' % (self.pipeline_root, self.input_dir))
            if (len(t) == 1) != (len(tiffs_in(self.input_dir)) == 1):
                out.append('The result folder name changes too (a single TIFF is processed under its own name, several '
                           'under the project name).')
        return out

    def set_input_dir(self, new_dir):
        """Change the input folder (must be an existing folder) and save project.json -> the TIFFs found there."""
        new = os.path.abspath(os.path.expanduser(str(new_dir)))
        if not os.path.isdir(new):
            raise ProjectError('%s is not a folder' % new)
        self.data['input_dir'] = new
        self.save()
        return tiffs_in(new)

    @property
    def params(self):
        return self.data['params']

    @property
    def translation(self):
        return self.data.setdefault('translation', dict(DEFAULT_TRANSLATION))

    @property
    def render_settings(self):
        return self.data.setdefault('render', dict(DEFAULT_RENDER))

    @property
    def render_dir(self):
        return self.root / 'render'

    @property
    def overrides_file(self):
        """Human adjustments of the rebuilt pages (``render_overrides.py``)."""
        return self.review_dir / 'render_overrides.json'

    @property
    def translation_dir(self):
        return self.root / 'translation'

    @property
    def translations_file(self):
        """translate.py's output (render.py reads it); the answer cache is ``translations.cache.json`` beside it."""
        return self.translation_dir / 'translations.json'

    @property
    def pipeline_root(self):
        return self.root / 'pipeline'

    @property
    def edits_file(self):
        """Human text edits of units (``unit_edits.py``)."""
        return self.review_dir / 'unit_edits.json'

    @property
    def decisions_file(self):
        """Human decisions about OCR flags (``ocr_decisions.py``)."""
        return self.review_dir / 'ocr_decisions.json'

    @property
    def review_dir(self):
        return self.root / 'review'

    def book_dir(self):
        """Where ``booktrans.py`` puts the result: ``pipeline/<name>``, or ``pipeline/<tiff stem>`` when the
        input holds a single TIFF (the wrapper only builds a "book" from several files).  When the expected
        folder does not exist but there is exactly one result folder, that one is used."""
        t = tiffs_in(self.input_dir)
        cand = self.pipeline_root / (t[0].stem if len(t) == 1 else self.name)
        if (cand / 'document.json').exists():
            return cand
        found = sorted(self.pipeline_root.glob('*/document.json'))
        return found[0].parent if len(found) == 1 else cand

    def files(self):
        b = self.book_dir()
        return {'document': b / 'document.json', 'corrected': b / 'document.corrected.json',
                'review': b / 'ocr_review.json', 'corrections': b / 'ocr_corrections.json'}

    def status(self):
        """Cheap summary for the header (file existence only, no JSON is read)."""
        f = self.files()
        t = tiffs_in(self.input_dir)
        return {'tiffs': len(t), 'tiff_bytes': sum(p.stat().st_size for p in t if p.exists()),
                'processed': f['document'].exists(), 'corrected': f['corrected'].exists(),
                'review': f['review'].exists(), 'book_dir': self.book_dir()}

    # ------------------------------------------------------------ clearing the project's data
    def data_parts(self):
        """What *Clear project data* can remove, in processing order: ``[{key, label, paths, bytes, default, note}]``
        (only parts that exist).  ``project.json`` (the settings) is never among them."""
        def size(p):
            p = Path(p)
            if p.is_symlink() or p.is_file():
                return p.lstat().st_size
            return sum(q.lstat().st_size for q in p.rglob('*') if q.is_file() or q.is_symlink()) if p.is_dir() else 0
        cache = self.translations_file.with_name('translations.cache.json')
        review = [self.edits_file, self.decisions_file, self.overrides_file]
        cand = [
            ('pipeline', 'Pipeline output (OCR, layout, document.json, corrected copy, figures, stages)',
             [p for p in sorted(self.pipeline_root.iterdir())] if self.pipeline_root.is_dir() else [], True, ''),
            ('translation', 'Translation (translations.json)',
             [p for p in self.translation_dir.iterdir() if p != cache] if self.translation_dir.is_dir() else [], True, ''),
            ('cache', 'Translation cache (answers already paid for / counted against limits)',
             [cache] if cache.exists() else [], False, 'kept, a new translation only sends what changed'),
            ('render', 'Rendered pages (render/: page images, PDF, report)', [self.render_dir] if self.render_dir.exists() else [],
             True, ''),
            ('review', 'Your review work (unit edits, OCR decisions, render adjustments)', [p for p in review if p.exists()],
             False, 'kept, it is tied to the OCR text and applies again after a new run'),
        ]
        out = []
        for k, label, paths, d, note in cand:
            if not paths:
                continue
            if any(Path(p).is_symlink() for p in paths):
                note = (note + '; ' if note else '') + 'a linked folder: only the link is removed, not the files it points to'
            out.append({'key': k, 'label': label, 'paths': paths, 'bytes': sum(size(p) for p in paths), 'default': d,
                        'note': note})
        return out

    def clear_data(self, keys):
        """Remove the parts ``keys`` (see ``data_parts``) -> the removed paths.  Only paths inside the project folder are
        touched; a symbolic link is removed, not what it points to; ``project.json`` is never removed."""
        root = self.root.resolve()
        removed = []
        for part in self.data_parts():
            if part['key'] not in keys:
                continue
            for p in part['paths']:
                p = Path(p)
                parent = p.parent.resolve()
                if parent != root and root not in parent.parents:
                    raise ProjectError('refusing to remove %s: outside the project folder' % p)
                if p.resolve() == self.file.resolve():
                    continue
                if p.is_symlink() or p.is_file():
                    p.unlink()
                elif p.is_dir():
                    shutil.rmtree(str(p))
                removed.append(p)
        if 'pipeline' in keys:
            self.pipeline_root.mkdir(parents=True, exist_ok=True)
        self.review_dir.mkdir(parents=True, exist_ok=True)
        return removed

    # ------------------------------------------------------------ data
    def load_document(self):
        """-> (document dict, 'corrected' | 'raw').  The corrected copy is preferred: it has the unit texts the
        translation will use, plus ``text_raw`` and ``ocr_corrections``."""
        f = self.files()
        for key, which in (('corrected', 'corrected'), ('document', 'raw')):
            if f[key].exists():
                try:
                    return read_json(f[key]), which
                except (OSError, ValueError) as e:
                    raise ProjectError('cannot read %s: %s' % (f[key], e))
        raise ProjectError('no document.json yet in %s: run the pipeline first' % self.book_dir())

    def load_review(self):
        """``ocr_review.json`` of the book (flags, resolutions) or {} when there is none."""
        p = self.files()['review']
        if not p.exists():
            return {}
        try:
            return read_json(p)
        except (OSError, ValueError) as e:
            raise ProjectError('cannot read %s: %s' % (p, e))

    # ------------------------------------------------------------ pipeline
    def command(self, resume=False):
        """The ``booktrans.py`` command line for this project (argument list)."""
        p = self.params
        cmd = [sys.executable, str(HERE / 'booktrans.py'), self.input_dir, '-o', str(self.pipeline_root),
               '--name', self.name, '--lang', p['lang'], '--layout', p['layout'], '--scale', str(p['scale']),
               '--ocr-scale', str(p['ocr_scale'])]
        if p['ocrfix'] == 'off':
            cmd.append('--no-ocrfix')
        else:
            if p['ocrfix'] == 'aggressive':
                cmd.append('--aggressive-ocr-fix')
            cmd += ['--edits', str(self.edits_file), '--decisions', str(self.decisions_file)]          # human texts from the GUI survive a re-run (a missing file is fine)
            if p['ocr_suggest']:
                cmd.append('--ocr-suggest')
        if resume:
            cmd.append('--resume')
        return cmd

    def command_line(self, resume=False):
        return ' '.join(shlex.quote(str(c)) for c in self.command(resume))

    def ocrfix_command(self):
        """Stage 5 alone: ``ocrfix.py run <book>/document.json`` with the project's OCR-correction mode and the GUI's
        edits and decisions, as ``booktrans.py`` runs it -> a new ``document.corrected.json`` in seconds, without
        stage 4.  Raises ProjectError when there is nothing to correct or the correction is switched off."""
        p = self.params
        if p['ocrfix'] == 'off':
            raise ProjectError('OCR correction is switched off in the parameters (OCR correction: off)')
        doc = self.book_dir() / 'document.json'
        if not doc.exists():
            raise ProjectError('no document.json yet in %s: run the pipeline first' % self.book_dir())
        cmd = [sys.executable, str(HERE / 'ocrfix.py'), 'run', str(doc)]
        if not p['ocr_suggest']:
            cmd.append('--no-suggest')
        if p['ocrfix'] == 'aggressive':
            cmd.append('--aggressive')
        return cmd + ['--edits', str(self.edits_file), '--decisions', str(self.decisions_file)]

    def ocrfix_command_line(self):
        return ' '.join(shlex.quote(str(c)) for c in self.ocrfix_command())

    # ------------------------------------------------------------ translation
    def translate_command(self, pages=None, dry_run=False, force=False):
        """``translate.py`` for this project: the book's document.json (translate.py itself takes the corrected copy
        beside it unless ``source`` is raw) -> ``translation/translations.json``.  ``pages``: e.g. '3-5' (a partial run
        keeps the other units); ``force``: ignore cached answers.  Raises ProjectError when there is no document yet."""
        t = self.translation
        doc = self.book_dir() / 'document.json'
        if not doc.exists():
            raise ProjectError('no document.json yet in %s: run the pipeline first' % self.book_dir())
        cmd = [sys.executable, str(HERE / 'translate.py'), str(doc), '-o', str(self.translations_file),
               '--provider', t['provider']]
        if str(t.get('model', '')).strip():
            cmd += ['--model', str(t['model']).strip()]
        if t['provider'] != 'claude-code' and str(t.get('endpoint', '')).strip():
            cmd += ['--endpoint', str(t['endpoint']).strip()]
        g = glossary_files(t.get('glossary'), self.root)
        if g:
            cmd += ['--glossary'] + [str(x) for x in g]
        if t['source'] == 'raw':
            cmd.append('--raw')
        cmd += ['--timeout', str(t['timeout']), '--retries', str(t['retries'])]
        if t['provider'] == 'openai' and t.get('no_think'):
            cmd.append('--no-think')
        if pages and str(pages).strip():
            cmd += ['--pages', str(pages).replace(' ', '')]
        if force:
            cmd.append('--force')
        if dry_run:
            cmd.append('--dry-run')
        return cmd

    def translate_command_line(self, pages=None, dry_run=False, force=False):
        return ' '.join(shlex.quote(str(c)) for c in self.translate_command(pages, dry_run, force))

    def translation_warnings(self):
        """Things to know before translating, as sentences: the corrected copy is missing or older than the saved
        edits and decisions (they would not be translated)."""
        if self.translation.get('source') == 'raw':
            return ['Source text "raw": the OCR text of document.json is translated, without the OCR correction and your edits.']
        f = self.files()
        if not f['document'].exists():
            return []
        if not f['corrected'].exists():
            return ['There is no document.corrected.json: the raw OCR text would be translated. '
                    'Run "Re-run OCR correction" first.']
        newer = [q.name for q in (self.edits_file, self.decisions_file)
                 if q.exists() and q.stat().st_mtime > f['corrected'].stat().st_mtime]
        if newer:
            return ['%s changed after document.corrected.json was written: your latest edits would not be translated. '
                    'Run "Re-run OCR correction" first.' % ' and '.join(newer)]
        return []

    # ------------------------------------------------------------ render
    def render_command(self, pages=None, resolution=None, pdf=False):
        """``render.py`` for this project: the book's document.json and ``translation/translations.json`` ->
        ``render/``, with the project's render settings and ``review/render_overrides.json``.  ``pages``: e.g. '3-5'
        (only these are drawn; the layout covers the book); ``resolution``: overrides the setting (``work`` for a
        quick preview); ``pdf``: also ``render/book.pdf``.  Raises ProjectError when the document or the translation is missing."""
        r = self.render_settings
        doc = self.book_dir() / 'document.json'
        if not doc.exists():
            raise ProjectError('no document.json yet in %s: run the pipeline first' % self.book_dir())
        if not self.translations_file.exists():
            raise ProjectError('no translation yet (%s): run the translation first' % self.translations_file)
        cmd = [sys.executable, str(HERE / 'render.py'), str(doc), str(self.translations_file), '-o', str(self.render_dir),
               '--background', r['background'], '--resolution', resolution or r['resolution'],
               '--tracking', str(r['tracking']).strip() or 'auto', '--size-scale', str(r['size_scale']),
               '--word-space', str(r['word_space']), '--ink', str(r['ink']).replace(' ', '')]
        for k, flag in (('font', '--font'), ('bold_font', '--bold-font'), ('italic_font', '--italic-font')):
            if str(r.get(k) or '').strip():
                cmd += [flag, str(_font_path(str(r[k]).strip(), self.root))]
        if str(r.get('paragraph_indent', '')).strip():
            cmd += ['--paragraph-indent', str(r['paragraph_indent']).strip()]
        if not r.get('page_fit', True):
            cmd.append('--no-page-fit')
        if self.overrides_file.exists():
            cmd += ['--overrides', str(self.overrides_file)]
        if pages and str(pages).strip():
            cmd += ['--pages', str(pages).replace(' ', '')]
        if pdf:
            cmd += ['--pdf', 'book.pdf']
        return cmd

    def render_command_line(self, pages=None, resolution=None, pdf=False):
        return ' '.join(shlex.quote(str(c)) for c in self.render_command(pages, resolution, pdf))

    def load_translations(self):
        """translations.json -> its ``units`` dict ({} when there is none or it cannot be read)."""
        try:
            return read_json(self.translations_file).get('units', {}) or {}
        except (OSError, ValueError, AttributeError):
            return {}


def expected_book_dir(input_dir, output_root, name):
    """Where the pipeline will put its result for these inputs (for the new-project dialog, before a project exists)."""
    t = tiffs_in(input_dir)
    return Path(output_root) / 'pipeline' / (t[0].stem if len(t) == 1 else name)


class PipelineRun:
    """``booktrans.py`` as a subprocess whose combined output is collected line by line (toolkit-free).

    ``drain()`` returns the lines that arrived since the last call, ``running`` / ``returncode`` tell the state and
    ``stop()`` ends the whole process group (booktrans.py starts the stages as child processes)."""

    def __init__(self, cmd, cwd=None):
        import queue
        import threading
        self.cmd = [str(c) for c in cmd]
        self._q = queue.Queue()
        env = dict(os.environ, PYTHONUNBUFFERED='1')
        kw = {'start_new_session': True} if hasattr(os, 'killpg') else {}
        self.proc = subprocess.Popen(self.cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', bufsize=1, **kw)
        self.stopped = False
        self._eof = False
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        for line in self.proc.stdout:
            self._q.put(line.rstrip('\n'))
        self.proc.stdout.close()
        self._q.put(None)                       # end marker: everything has been read

    def drain(self):
        out = []
        while True:
            try:
                item = self._q.get_nowait()
            except Exception:
                break
            if item is None:
                self._eof = True
            else:
                out.append(item)
        return out

    @property
    def running(self):
        return self.proc.poll() is None or self._reader.is_alive()

    @property
    def returncode(self):
        return self.proc.poll()

    def stop(self):
        if self.proc.poll() is not None:
            return
        self.stopped = True
        try:
            if hasattr(os, 'killpg'):
                os.killpg(self.proc.pid, signal.SIGTERM)
            else:
                self.proc.terminate()
        except (OSError, ProcessLookupError):
            pass

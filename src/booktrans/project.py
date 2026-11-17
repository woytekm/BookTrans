#!/usr/bin/env python3
"""GUI project: folder layout, ``project.json`` and the pipeline command (no GUI toolkit, testable alone).

A project is one folder, chosen by the user as "output directory" + project name::

    <project>/project.json       name, input folder, pipeline parameters (this module)
    <project>/pipeline/<book>/   output of booktrans.py (-o <project>/pipeline --name <book>); a re-run may wipe it
    <project>/review/            human decisions made in the GUI; never written or deleted by the pipeline

The split is deliberate: ``booktrans.py`` removes its result folder before a full (non ``--resume``) run, so
nothing a human typed may live inside it (see docs/GUI.md, decision D4).
"""
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

SCHEMA = '1.0'
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
                'input_dir': os.path.abspath(os.path.expanduser(str(input_dir))), 'params': p}
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

    @property
    def params(self):
        return self.data['params']

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

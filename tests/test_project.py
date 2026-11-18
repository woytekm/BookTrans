"""project.py: folder layout, validation, pipeline command."""
import json
import sys

import pytest

import project as PR


def touch_tiff(folder, name):
    from PIL import Image
    Image.new('L', (4, 3)).save(str(folder / name))


@pytest.fixture()
def scans(tmp_path):
    d = tmp_path / 'scans'
    d.mkdir()
    for n in ('b_0002.tif', 'a_0001.TIF', '.hidden.tif'):
        touch_tiff(d, n)
    (d / 'notes.txt').write_text('x')
    return d


def test_create_load_roundtrip(tmp_path, scans):
    p = PR.Project.create(tmp_path / 'out' / 'book', 'book', scans, {'ocrfix': 'aggressive'})
    assert (tmp_path / 'out' / 'book' / 'project.json').exists() and p.review_dir.is_dir()
    q = PR.Project.load(tmp_path / 'out' / 'book')                      # by folder
    r = PR.Project.load(tmp_path / 'out' / 'book' / 'project.json')     # by file
    for x in (q, r):
        assert x.name == 'book' and x.params['ocrfix'] == 'aggressive' and x.params['layout'] == 'spread'
        assert x.input_dir == str(scans)


def test_tiffs_in_matches_booktrans_selection(scans):
    assert [t.name for t in PR.tiffs_in(scans)] == ['a_0001.TIF', 'b_0002.tif']    # name order, no dotfiles, no other files
    assert PR.tiffs_in(scans / 'missing') == []


@pytest.mark.parametrize('bad', ['', ' x', '.x', 'a/b', 'a\\b', 'a:b', 'a*b'])
def test_bad_names_are_refused(tmp_path, scans, bad):
    with pytest.raises(PR.ProjectError):
        PR.Project.create(tmp_path / 'p', bad, scans)


def test_second_create_in_same_folder_is_refused(tmp_path, scans):
    PR.Project.create(tmp_path / 'p', 'p', scans)
    with pytest.raises(PR.ProjectError, match='already'):
        PR.Project.create(tmp_path / 'p', 'p', scans)


@pytest.mark.parametrize('params', [{'layout': 'poster'}, {'ocrfix': 'maybe'}, {'lang': 'de u'}, {'scale': '-1'},
                                    {'scale': 'big'}, {'ocr_scale': 0}, {'ocr_scale': 'x'}])
def test_bad_parameters_are_refused(tmp_path, scans, params):
    with pytest.raises(PR.ProjectError):
        PR.Project.create(tmp_path / 'p', 'p', scans, params)


def test_load_errors_are_readable(tmp_path):
    with pytest.raises(PR.ProjectError, match='no project.json'):
        PR.Project.load(tmp_path)
    (tmp_path / 'project.json').write_text('{not json')
    with pytest.raises(PR.ProjectError, match='cannot read'):
        PR.Project.load(tmp_path)
    (tmp_path / 'project.json').write_text(json.dumps({'kind': 'something-else'}))
    with pytest.raises(PR.ProjectError, match='not a BookTrans project'):
        PR.Project.load(tmp_path)
    (tmp_path / 'project.json').write_text(json.dumps({'kind': PR.KIND, 'schema': '2.0', 'name': 'x'}))
    with pytest.raises(PR.ProjectError, match='schema'):
        PR.Project.load(tmp_path)


def test_command_line_maps_every_parameter(tmp_path, scans):
    p = PR.Project.create(tmp_path / 'p', 'my book', scans, {'lang': 'deu+eng', 'layout': 'page', 'scale': '2', 'ocr_scale': 3,
                                                           'ocrfix': 'aggressive', 'ocr_suggest': True})
    c = p.command()
    assert c[0] == sys.executable and c[1].endswith('booktrans.py') and c[2] == str(scans)
    assert c[c.index('-o') + 1] == str(tmp_path / 'p' / 'pipeline') and c[c.index('--name') + 1] == 'my book'
    assert c[c.index('--lang') + 1] == 'deu+eng' and c[c.index('--layout') + 1] == 'page'
    assert c[c.index('--scale') + 1] == '2' and c[c.index('--ocr-scale') + 1] == '3'
    assert '--aggressive-ocr-fix' in c and '--ocr-suggest' in c and '--resume' not in c and '--no-ocrfix' not in c
    assert '--resume' in p.command(resume=True)
    assert c[c.index('--decisions') + 1] == str(p.decisions_file)
    assert c[c.index('--edits') + 1] == str(p.edits_file)                  # human texts of the GUI survive a re-run
    assert "'my book'" in p.command_line()                      # quoted for a shell
    p.params['ocrfix'] = 'off'
    c = p.command()
    assert '--no-ocrfix' in c and '--aggressive-ocr-fix' not in c and '--ocr-suggest' not in c


def test_book_dir_follows_the_wrappers_naming(tmp_path, scans):
    p = PR.Project.create(tmp_path / 'p', 'mybook', scans)
    assert p.book_dir() == p.pipeline_root / 'mybook'                       # two TIFFs: one book named after the project
    (scans / 'b_0002.tif').unlink()
    assert p.book_dir() == p.pipeline_root / 'a_0001'                       # one TIFF: the wrapper names it after the file
    assert PR.expected_book_dir(scans, tmp_path / 'p', 'mybook') == p.pipeline_root / 'a_0001'
    (p.pipeline_root / 'renamed').mkdir(parents=True)
    (p.pipeline_root / 'renamed' / 'document.json').write_text('{}')
    assert p.book_dir() == p.pipeline_root / 'renamed'                      # the only result folder there is


def test_status_and_document_loading(tmp_path, scans):
    p = PR.Project.create(tmp_path / 'p', 'mybook', scans)
    st = p.status()
    assert st['tiffs'] == 2 and not st['processed'] and not st['corrected']
    with pytest.raises(PR.ProjectError, match='run the pipeline'):
        p.load_document()
    assert p.load_review() == {}
    b = p.pipeline_root / 'mybook'
    b.mkdir(parents=True)
    (b / 'document.json').write_text(json.dumps({'pages': [], 'units': [], 'tag': 'raw'}))
    assert p.load_document()[1] == 'raw'
    (b / 'document.corrected.json').write_text(json.dumps({'pages': [], 'units': [], 'tag': 'fixed'}))
    doc, which = p.load_document()
    assert which == 'corrected' and doc['tag'] == 'fixed'                    # the corrected copy wins
    (b / 'ocr_review.json').write_text('{"flags": []}')
    assert p.load_review() == {'flags': []} and p.status()['processed'] and p.status()['review']


def test_write_json_is_atomic_and_utf8(tmp_path):
    f = tmp_path / 'sub' / 'x.json'
    PR.write_json(f, {'ä': 'Pitschen → Byczyna'})
    assert json.loads(f.read_text(encoding='utf-8')) == {'ä': 'Pitschen → Byczyna'} and 'Byczyna' in f.read_text(encoding='utf-8')
    assert not (tmp_path / 'sub' / 'x.json.tmp').exists()


def test_pipeline_run_collects_output_and_exit_code():
    import sys, time
    from project import PipelineRun
    run = PipelineRun([sys.executable, '-c', 'import sys; print("one"); print("two", file=sys.stderr); sys.exit(3)'])
    lines = []
    t0 = time.time()
    while run.running and time.time() - t0 < 20:
        lines += run.drain()
        time.sleep(0.02)
    lines += run.drain()
    assert lines == ['one', 'two'] and run.returncode == 3 and not run.stopped


def test_pipeline_run_stop_ends_the_process_group():
    import sys, time
    from project import PipelineRun
    run = PipelineRun([sys.executable, '-c', 'import time; print("up", flush=True); time.sleep(60)'])
    t0 = time.time()
    seen = []
    while 'up' not in seen and time.time() - t0 < 20:
        seen += run.drain()
        time.sleep(0.02)
    run.stop()
    while run.running and time.time() - t0 < 20:
        time.sleep(0.02)
    assert not run.running and run.stopped and run.returncode != 0


def test_ocrfix_command_runs_stage_5_alone_with_the_gui_files(tmp_path, scans):
    p = PR.Project.create(tmp_path / 'p', 'mybook', scans, {'ocrfix': 'aggressive', 'ocr_suggest': False})
    with pytest.raises(PR.ProjectError, match='run the pipeline first'):
        p.ocrfix_command()
    (p.book_dir()).mkdir(parents=True)
    (p.book_dir() / 'document.json').write_text('{}', encoding='utf-8')
    c = p.ocrfix_command()
    assert c[0] == sys.executable and c[1].endswith('ocrfix.py') and c[2] == 'run' and c[3] == str(p.book_dir() / 'document.json')
    assert '--aggressive' in c and '--no-suggest' in c
    assert c[c.index('--edits') + 1] == str(p.edits_file) and c[c.index('--decisions') + 1] == str(p.decisions_file)
    p.params.update(ocrfix='strict', ocr_suggest=True)
    c = p.ocrfix_command()
    assert '--aggressive' not in c and '--no-suggest' not in c
    p.params['ocrfix'] = 'off'
    with pytest.raises(PR.ProjectError, match='switched off'):
        p.ocrfix_command()


# ---------------------------------------------------------------- translation settings
def _with_doc(p, corrected=True):
    p.book_dir().mkdir(parents=True, exist_ok=True)
    (p.book_dir() / 'document.json').write_text('{}', encoding='utf-8')
    if corrected:
        (p.book_dir() / 'document.corrected.json').write_text('{}', encoding='utf-8')


def test_translation_defaults_and_an_old_project_file(tmp_path, scans):
    p = PR.Project.create(tmp_path / 'p', 'mybook', scans)
    data = json.loads(p.file.read_text(encoding='utf-8'))
    assert data['schema'] == PR.SCHEMA and data['translation']['provider'] == 'claude-code'
    del data['translation']
    data['schema'] = '1.0'                                         # a project made before translation settings existed
    p.file.write_text(json.dumps(data), encoding='utf-8')
    q = PR.Project.load(p.root)
    assert q.translation == PR.DEFAULT_TRANSLATION and q.translations_file == p.root / 'translation' / 'translations.json'


def test_translate_command_maps_the_settings(tmp_path, scans):
    p = PR.Project.create(tmp_path / 'p', 'mybook', scans)
    with pytest.raises(PR.ProjectError, match='run the pipeline first'):
        p.translate_command()
    _with_doc(p)
    c = p.translate_command()
    assert c[1].endswith('translate.py') and c[2] == str(p.book_dir() / 'document.json')
    assert c[c.index('-o') + 1] == str(p.translations_file) and c[c.index('--provider') + 1] == 'claude-code'
    assert '--model' not in c and '--endpoint' not in c and '--glossary' not in c and '--raw' not in c
    assert c[c.index('--timeout') + 1] == '600' and c[c.index('--retries') + 1] == '2' and '--pages' not in c
    (p.root / 'g.csv').write_text('de,pl,kind,status\n', encoding='utf-8')
    p.translation.update(model='sonnet', endpoint='http://x:1/v1', glossary='g.csv; ~/none.csv', source='raw', no_think=True)
    c = p.translate_command(pages='3, 5-6', dry_run=True)
    assert c[c.index('--model') + 1] == 'sonnet' and '--endpoint' not in c          # claude-code has no endpoint
    assert c[c.index('--glossary') + 1] == str(p.root / 'g.csv')                     # relative to the project folder
    assert '--raw' in c and '--no-think' not in c and c[c.index('--pages') + 1] == '3,5-6' and '--dry-run' in c and '--force' not in c
    assert '--force' in p.translate_command(pages='3', force=True)
    p.translation['provider'] = 'openai'
    c = p.translate_command()
    assert c[c.index('--endpoint') + 1] == 'http://x:1/v1' and '--no-think' in c


def test_check_translation(tmp_path):
    ok = dict(PR.DEFAULT_TRANSLATION)
    assert PR.check_translation(dict(ok)) == ok
    for bad, msg in ((dict(provider='gpt'), 'engine'), (dict(source='x'), 'source'), (dict(model='two words'), 'model'),
                     (dict(timeout='0'), 'timeout'), (dict(retries='-1'), 'retries'), (dict(retries='x'), 'retries'),
                     (dict(glossary='missing.csv'), 'not found')):
        with pytest.raises(PR.ProjectError, match=msg):
            PR.check_translation(dict(ok, **bad), tmp_path)


def test_translation_warnings_name_a_stale_corrected_copy(tmp_path, scans):
    import os
    p = PR.Project.create(tmp_path / 'p', 'mybook', scans)
    assert p.translation_warnings() == []                                            # no document yet: nothing to say
    _with_doc(p, corrected=False)
    assert 'no document.corrected.json' in p.translation_warnings()[0]
    _with_doc(p)
    assert p.translation_warnings() == []
    p.edits_file.write_text('{}', encoding='utf-8')
    t = (p.book_dir() / 'document.corrected.json').stat().st_mtime
    os.utime(p.edits_file, (t + 10, t + 10))
    w = p.translation_warnings()
    assert 'unit_edits.json changed after' in w[0] and 'Re-run OCR correction' in w[0]
    p.translation['source'] = 'raw'
    assert 'raw' in p.translation_warnings()[0]


def test_translate_dry_run_on_the_demo_project(tmp_path):
    """The real translate.py, from the project's command, on the GUI demo document: prompts only, no model."""
    import subprocess
    import gui_demo
    gui_demo.make_demo(tmp_path / 'd')
    p = PR.Project.load(tmp_path / 'd')
    r = subprocess.run(p.translate_command(pages='1', dry_run=True), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert '--pages 1:' in r.stderr and 'dry run:' in r.stderr
    prompts = p.translations_file.with_suffix('.prompts.txt').read_text(encoding='utf-8')
    assert 'Text to translate' in prompts


# ---------------------------------------------------------------- render settings
def test_render_defaults_validation_and_an_old_project_file(tmp_path, scans):
    p = PR.Project.create(tmp_path / 'p', 'mybook', scans)
    data = json.loads(p.file.read_text(encoding='utf-8'))
    assert data['schema'] == '1.2' and data['render'] == PR.DEFAULT_RENDER
    del data['render']
    data['schema'] = '1.1'
    p.file.write_text(json.dumps(data), encoding='utf-8')
    q = PR.Project.load(p.root)
    assert q.render_settings == PR.DEFAULT_RENDER
    assert q.render_dir == p.root / 'render' and q.overrides_file == p.root / 'review' / 'render_overrides.json'
    PR.check_render(dict(PR.DEFAULT_RENDER))
    for bad in ({'background': 'x'}, {'tracking': 'wide'}, {'tracking': '0.9'}, {'size_scale': 3}, {'ink': '1,2'},
                {'font': 'nope.ttf'}, {'paragraph_indent': 'x'}):
        with pytest.raises(PR.ProjectError):
            PR.check_render(dict(PR.DEFAULT_RENDER, **bad), p.root)


def test_render_command_maps_the_settings(tmp_path, scans):
    p = PR.Project.create(tmp_path / 'p', 'mybook', scans)
    with pytest.raises(PR.ProjectError, match='run the pipeline first'):
        p.render_command()
    _with_doc(p)
    with pytest.raises(PR.ProjectError, match='run the translation first'):
        p.render_command()
    p.translations_file.parent.mkdir(parents=True)
    p.translations_file.write_text('{"units": {}}', encoding='utf-8')
    c = p.render_command()
    assert c[1].endswith('render.py') and c[2] == str(p.book_dir() / 'document.json') and c[3] == str(p.translations_file)
    assert c[c.index('-o') + 1] == str(p.render_dir) and c[c.index('--tracking') + 1] == 'auto'
    assert '--font' not in c and '--overrides' not in c and '--no-page-fit' not in c and '--pages' not in c
    (p.root / 'fonts').mkdir()
    (p.root / 'fonts' / 'X.ttf').write_bytes(b'')
    p.render_settings.update(font='fonts/X.ttf', tracking='0.05', page_fit=False, paragraph_indent='1', background='scan')
    p.overrides_file.write_text('{"blocks": {}, "pages": {}}', encoding='utf-8')
    c = p.render_command(pages='3, 5-6', resolution='work')
    assert c[c.index('--font') + 1] == str(p.root / 'fonts' / 'X.ttf') and c[c.index('--tracking') + 1] == '0.05'
    assert '--no-page-fit' in c and c[c.index('--paragraph-indent') + 1] == '1' and c[c.index('--background') + 1] == 'scan'
    assert c[c.index('--overrides') + 1] == str(p.overrides_file) and c[c.index('--pages') + 1] == '3,5-6'
    assert c[c.index('--resolution') + 1] == 'work'


# ---------------------------------------------------------------- clear project data
def test_clear_data_removes_results_keeps_settings_and_chosen_parts(tmp_path, scans):
    p = PR.Project.create(tmp_path / 'p', 'mybook', scans)
    _with_doc(p)
    (p.book_dir() / 'stages').mkdir()
    (p.book_dir() / 'stages' / 'x.json').write_text('{}', encoding='utf-8')
    p.translations_file.parent.mkdir(parents=True)
    p.translations_file.write_text('{"units": {}}', encoding='utf-8')
    cache = p.translations_file.with_name('translations.cache.json')
    cache.write_text('{}', encoding='utf-8')
    p.render_dir.mkdir()
    (p.render_dir / 'page_001_L.png').write_bytes(b'x' * 10)
    p.edits_file.write_text('{"units": {}}', encoding='utf-8')
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    (outside / 'keep.txt').write_text('mine', encoding='utf-8')
    (p.pipeline_root / 'linked').symlink_to(outside, target_is_directory=True)     # a linked result folder
    parts = {x['key']: x for x in p.data_parts()}
    assert set(parts) == {'pipeline', 'translation', 'cache', 'render', 'review'}
    assert parts['pipeline']['default'] and not parts['cache']['default'] and not parts['review']['default']
    assert parts['render']['bytes'] == 10
    removed = p.clear_data([k for k, x in parts.items() if x['default']])
    assert not p.book_dir().exists() and not p.translations_file.exists() and not p.render_dir.exists()
    assert cache.exists() and p.edits_file.exists() and p.file.exists() and p.pipeline_root.is_dir()
    assert not (p.pipeline_root / 'linked').exists() and (outside / 'keep.txt').exists()   # the link, not its target
    assert len(removed) == 4
    assert PR.Project.load(p.root).name == 'mybook'                                    # settings intact
    p.clear_data(['cache', 'review'])
    assert not cache.exists() and not p.edits_file.exists() and p.data_parts() == []

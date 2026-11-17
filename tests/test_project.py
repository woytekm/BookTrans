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

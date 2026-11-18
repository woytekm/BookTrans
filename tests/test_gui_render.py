"""The render window under a display: builds, lays out the demo, navigates, selects blocks, applies adjustments and
settings, runs a full render.  Skipped without tkinter or a display."""
import json
import sys
import time

import pytest

tk = pytest.importorskip('tkinter')
pytest.importorskip('PIL.ImageTk')

try:
    _probe = tk.Tk()
    _probe.destroy()
except tk.TclError as e:
    pytest.skip('no display for Tk: %s' % e, allow_module_level=True)

import gui        # noqa: E402
import gui_demo   # noqa: E402


def pump(app, until=None, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        if until is None or until():
            return
        time.sleep(0.01)
    if until is not None:
        raise AssertionError('timed out waiting for the GUI')


@pytest.fixture(autouse=True)
def _no_real_home(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, 'RECENT_FILE', tmp_path / 'recent.json')


@pytest.fixture()
def win(tmp_path):
    gui_demo.make_demo(tmp_path / 'p', translate=True)
    app = gui.MainWindow(str(tmp_path / 'p'))
    app.withdraw()
    pump(app, lambda: app.rows)
    app.open_render()
    w = app.render_window
    w.withdraw()
    w.geometry('1400x900')
    pump(app, lambda: w._photo is not None and not w._busy)
    yield app, w
    if app.winfo_exists():
        app.quit_app()


def test_window_lays_out_and_lists_the_pages(win):
    app, w = win
    assert app.render_window is w and str(app.render_btn.cget('state')) == 'normal'
    assert w.tree.get_children() == ('0', '1', '2')
    vals = w.tree.item('1', 'values')
    assert vals[0] == '2' and 'empty 1' in vals[4]
    assert 'Laid out 3 pages' in w.status_lbl.cget('text')
    assert [k for k, _ in w._panels] == ['original', 'rebuilt']
    app.open_render()
    assert app.render_window is w                                        # one window per project


def test_navigation_views_and_zoom(win):
    app, w = win
    w.go(1)
    pump(app, lambda: w.pi == 1 and w._photo is not None)
    assert w.tree.selection() == ('1',) and 'Page 2' in w.head.cget('text')
    w.view.set('rebuilt')
    w.redraw()
    pump(app, lambda: [k for k, _ in w._panels] == ['rebuilt'])
    w.set_zoom(1.0)
    pump(app, lambda: abs(w._scale - 1.0) < 1e-6)
    assert w._photo.width() == w._img.width and '100%' in w.zoom_lbl.cget('text')


def test_click_selects_a_block_and_adjustments_apply(win, monkeypatch):
    app, w = win
    s = w.session
    cap = next(b for b in s.doc['pages'][0]['blocks'] if b.get('role') == 'caption')
    x = (cap['bbox'][0] + cap['bbox'][2]) / 2
    y = (cap['bbox'][1] + cap['bbox'][3]) / 2
    off = dict(w._panels)['rebuilt']
    cx, cy = w._origin[0] + off + x * w._scale, w._origin[1] + y * w._scale
    assert w.to_work(cx, cy) == pytest.approx((x, y), abs=0.01)
    w.select_block(s.block_at(0, *w.to_work(cx, cy)))
    assert w.sel == cap['block_id'] and cap['block_id'] in w.block_info.cget('text')
    assert str(w.block_apply.cget('state')) == 'normal'
    w.kv['size_scale'].set('0.85')
    w.kv['anchor'].set('right')
    w.apply_block()
    pump(app, lambda: not w._busy and 'Laid out' in w.status_lbl.cget('text'))
    saved = json.loads(app.project.overrides_file.read_text(encoding='utf-8'))
    assert saved['blocks'][cap['block_id']]['anchor'] == 'right'
    assert 'size_scale' in s.block_info(0, cap['block_id'])['report']['adjusted']
    errors = []
    import tkinter.messagebox as mb
    monkeypatch.setattr(mb, 'showerror', lambda *a, **k: errors.append(a))
    w.kv['size_scale'].set('abc')
    w.apply_block()
    assert errors and 'number' in errors[-1][1]
    w.clear_block()
    pump(app, lambda: not w._busy and 'Laid out' in w.status_lbl.cget('text'))
    assert s.block_adjustment(cap['block_id']) == {}
    # a body block: only "keep the original" applies
    body = next(b for b in s.doc['pages'][0]['blocks'] if b.get('role') == 'paragraph' and b.get('unit_id'))
    w.select_block(body['block_id'])
    assert str(w.block_entries[0].cget('state')) == 'disabled' and 'Page tab' in w.block_note.cget('text')


def test_page_adjustment_and_checks_list(win):
    app, w = win
    w.show_page(1)
    pump(app, lambda: w._photo is not None)
    kinds = [w.checks.item(i, 'values')[0] for i in w.checks.get_children()]
    assert 'blank' in kinds
    w.pv_size.set('0.95')
    w.apply_page()
    pump(app, lambda: not w._busy and 'Laid out' in w.status_lbl.cget('text'))
    assert w.session.plan['pages'][1]['body']['size_factor'] == 0.95 and 'Adjusted by hand' in w.page_info.cget('text')
    assert w.tree.item('1', 'values')[3].endswith('*')
    w.clear_page()
    pump(app, lambda: not w._busy and 'Laid out' in w.status_lbl.cget('text'))
    assert w.session.page_adjustment(1) == {}


def test_book_settings_apply_and_refuse_bad_values(win, monkeypatch):
    app, w = win
    errors = []
    import tkinter.messagebox as mb
    monkeypatch.setattr(mb, 'showerror', lambda *a, **k: errors.append(a))
    w.bv['size_scale'].set('9')
    w.apply_book()
    assert errors and 'size scale' in errors[-1][1]
    w.bv['size_scale'].set('1.0')
    w.bv['ink'].set('40,30,20')
    w.apply_book()
    pump(app, lambda: not w._busy and 'Laid out' in w.status_lbl.cget('text'))
    saved = json.loads(app.project.file.read_text(encoding='utf-8'))['render']
    assert saved['ink'] == '40,30,20' and saved['size_scale'] == 1.0
    assert w.session.plan['ink'] == [40, 30, 20] and 'fixed' in w.book_info.cget('text')


def test_render_book_runs_render_py(win, monkeypatch):
    app, w = win
    w.render_book()
    rw = w.run_window
    assert rw.mode == 'render' and rw.v['pdf'].get()
    seen = {}
    real = app.project.render_command

    def fake(pages=None, resolution=None, pdf=False):
        seen['cmd'] = real(pages, resolution, pdf)
        return [sys.executable, '-c', 'print("render: done")']
    monkeypatch.setattr(app.project, 'render_command', fake)
    rw.v['pages'].set('2')
    rw.start()
    pump(app, lambda: rw.run is not None and not rw.run.running)
    pump(app, lambda: 'finished' in rw.state.cget('text'))
    c = seen['cmd']
    assert c[1].endswith('render.py') and c[c.index('--pages') + 1] == '2' and '--pdf' in c


def test_clear_project_data_closes_windows_and_starts_afresh(win, monkeypatch):
    app, w = win
    import gui_dialogs
    d = gui_dialogs.ClearDataDialog(app, app.project)
    d.withdraw()
    assert set(d.vars) >= {'pipeline', 'translation'} and d.vars['pipeline'].get() and 'review' not in d.chosen()
    assert 'starts from the scans' in d.warn.cget('text')
    d.vars['cache'] = d.vars.get('cache') or __import__('tkinter').BooleanVar(value=False)
    d._clear()
    assert d.result == [k for k in d.vars if d.vars[k].get()]

    class Fake:                                       # the dialog, answered: the default parts
        def __init__(self, master, project):
            self.result = [p['key'] for p in project.data_parts() if p['default']]
    monkeypatch.setattr(gui, 'ClearDataDialog', Fake)
    monkeypatch.setattr(app, 'wait_window', lambda _w: None)
    app.clear_project()
    pump(app, lambda: not app.rows and 'Cleared' in app.status_lbl.cget('text'))
    assert not app.project.book_dir().exists() and not app.project.translations_file.exists()
    assert app.project.file.exists() and app.render_window is None and not w.winfo_exists()
    assert 'Run pipeline starts afresh' in app.status_lbl.cget('text')


def test_input_folder_is_shown_and_can_be_changed(win, monkeypatch, tmp_path):
    app, w = win
    from PIL import Image
    import gui_dialogs
    old = app.project.input_dir
    assert old in app.info_lbl.cget('text')
    new = tmp_path / 'scans2'
    new.mkdir()
    Image.new('L', (4, 3)).save(str(new / 'x.tif'))
    asked = []
    monkeypatch.setattr(gui_dialogs.filedialog, 'askdirectory', lambda **k: str(new))
    monkeypatch.setattr(gui_dialogs.messagebox, 'askyesno', lambda title, msg, **k: (asked.append(msg), True)[1])
    app.run_pipeline()
    rw = app.run_window
    assert old in rw.input_lbl.cget('text') and rw.resume.get()               # the demo has a result: resume offered
    rw._change_input()
    assert app.project.input_dir == str(new) and 'x.tif' in asked[-1] and 'without --resume' in asked[-1]
    assert str(new) in rw.input_lbl.cget('text') and '1 TIFF file' in rw.input_lbl.cget('text')
    assert not rw.resume.get() and str(new) in app.info_lbl.cget('text')
    assert json.loads(app.project.file.read_text(encoding='utf-8'))['input_dir'] == str(new)
    monkeypatch.setattr(gui_dialogs.filedialog, 'askdirectory', lambda **k: '')  # cancelled: nothing changes
    app.change_input()
    assert app.project.input_dir == str(new)

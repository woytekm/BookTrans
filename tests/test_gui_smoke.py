"""GUI smoke tests: the windows build, load the demo project and react.  Skipped without tkinter or a display.

Windows are withdrawn right after creation, so running the tests does not flash windows on a desktop."""
import json
import time

import pytest

tk = pytest.importorskip('tkinter')
pytest.importorskip('PIL.ImageTk')
pytest.importorskip('symspellpy')

try:
    _probe = tk.Tk()
    _probe.destroy()
except tk.TclError as e:                  # no display (headless run without xvfb)
    pytest.skip('no display for Tk: %s' % e, allow_module_level=True)

import envcheck      # noqa: E402
import gui           # noqa: E402
import gui_demo      # noqa: E402
import gui_dialogs   # noqa: E402
from project import Project   # noqa: E402


def pump(app, until=None, timeout=8.0):
    """Process events until ``until()`` is true (or the time is up, then fail)."""
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        if until is None or until():
            return
        time.sleep(0.01)
    if until is not None:
        raise AssertionError('timed out waiting for the GUI: %s' % getattr(app, 'status_lbl', None) and app.status_lbl.cget('text'))


@pytest.fixture(autouse=True)
def _no_real_home(monkeypatch, tmp_path):
    """Opening a project remembers it in ~/.booktrans_gui.json: the tests must not touch the real one."""
    monkeypatch.setattr(gui, 'RECENT_FILE', tmp_path / 'recent.json')


@pytest.fixture(scope='module')
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp('guidemo') / 'p'
    gui_demo.make_demo(d)
    return d


@pytest.fixture()
def app(demo_dir):
    a = gui.MainWindow(str(demo_dir))
    a.withdraw()
    pump(a, lambda: a.rows)
    yield a
    if a.winfo_exists():
        a.quit_app()


def test_page_list_shows_the_demo_pages(app):
    assert [r['page'] for r in app.rows] == [1, 2, 3]
    assert app.tree.get_children() == ('1', '2', '3')
    assert app._selected_page() == 1
    vals = dict(zip([c[0] for c in gui.COLUMNS], app.tree.item('2', 'values')))
    assert (vals['page'], vals['side'], vals['source']) == ('2', 'R', 'demo_0001.tif')
    assert vals['uncertain'] == '1' and vals['watch'] == '2'
    assert 'tinted' not in app.tree.item('2', 'tags') and 'attention' in app.tree.item('2', 'tags')
    assert 'corrected automatically 7' in app.totals.cget('text')
    assert app.source_lbl.cget('text') == 'showing document.corrected.json'


def test_the_whole_gui_is_dark_and_the_list_colours_are_explicit(app):
    import gui_widgets as gw
    st = gui.ttk.Style(app)
    assert st.lookup('Treeview', 'foreground') == gw.TREE_FG and st.lookup('Treeview', 'fieldbackground') == gw.TREE_BG
    assert st.lookup('TLabel', 'background') == gw.BG and st.lookup('TLabel', 'foreground') == gw.FG
    assert st.lookup('TButton', 'background') == gw.PANEL
    for tag in ('attention', 'watch'):
        assert str(app.tree.tag_configure(tag, 'foreground')) == gw.TREE_FG
    assert str(app.cget('background')) == gw.BG


def _luminance(hexcol):        # WCAG relative luminance
    r, g, b = ((v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4) for v in (int(hexcol[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_text_is_readable_on_every_background_it_is_drawn_on():
    import gui_widgets as gw
    def contrast(a, b):
        la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
        return (la + 0.05) / (lb + 0.05)
    for bg in (gw.BG, gw.PANEL, gw.FIELD, gw.ROW_TINT['attention'], gw.ROW_TINT['watch']):
        assert contrast(gw.FG, bg) >= 7, bg
    assert contrast(gw.SEL_FG, gw.SEL_BG) >= 5
    for bg in (gw.BG, gw.FIELD):
        assert contrast(gw.MUTED, bg) >= 4.5 and all(contrast(c, bg) >= 4.5 for c in gw.STATUS_COLORS.values())
        assert all(contrast(c, bg) >= 4.5 for c in gw.JSON_COLORS.values())


def test_colours_are_defined_in_one_place():
    import re
    from pathlib import Path
    for f in Path(gui.__file__).parent.glob('gui*.py'):
        if f.name in ('gui_widgets.py',):
            continue
        assert not re.search(r"#[0-9a-fA-F]{6}\b", f.read_text(encoding='utf-8')), '%s hard-codes a colour' % f.name


def test_selecting_a_page_fills_the_three_json_tabs(app):
    app.tree.selection_set('2')
    pump(app)
    model, review, marks = (app.views[k].get_text() for k in ('model', 'review', 'marks'))
    assert '"block_id": "s1-p3"' in model
    assert '"continues_from_previous_page": true' in review and '"unit_id": "u0004"' in review
    assert '"abbrev_space"' in marks and '"qwlstig"' in marks
    app.tree.selection_set('3')
    pump(app)
    assert 'Bruchsteinen' in app.views['marks'].get_text() and '"cls": "human"' in app.views['marks'].get_text()


def test_find_counts_and_highlights_matches(app):
    app.tree.selection_set('2')
    app.nb.select(2)
    pump(app)
    app.find_var.set('"cls"')
    pump(app)
    assert app.find_count.cget('text') == '4 matches'             # one per mark on page 2
    assert app.views['marks'].text.tag_ranges('current')
    app._find(True)
    app._find(False)
    app.find_var.set('no such text')
    pump(app)
    assert app.find_count.cget('text') == 'no match'


def test_sorting_by_a_column(app):
    app._sort_by('auto')
    assert app.tree.get_children() == ('3', '2', '1')             # ascending by auto: page 3 (0), page 2 (1), page 1 (6)
    app._sort_by('auto')
    assert app.tree.get_children() == ('1', '2', '3')


def test_correction_window_is_per_page_reused_and_unregistered_on_close(app):
    app.tree.selection_set('1')
    pump(app)
    app.open_correction()
    w = app.windows[1]
    w.withdraw()
    pump(app)
    assert w.head.cget('text').startswith('Page 1') and 'auto 6' in w.counts.cget('text')
    assert w._img is not None and w._img.size == (925, 1315)
    app.open_correction()
    assert app.windows[1] is w and len(app.windows) == 1
    w.close()
    assert app.windows == {}


def test_reload_updates_open_windows_and_keeps_the_selection(app):
    app.tree.selection_set('2')
    pump(app)
    app.open_correction()
    app.windows[2].withdraw()
    app.reload()
    pump(app, lambda: app.status_lbl.cget('text') == '3 pages loaded.')
    assert app._selected_page() == 2 and 2 in app.windows


def test_recent_projects_are_remembered_and_vanished_ones_dropped(app, tmp_path):
    assert app._recent() == [str(app.project.root)]
    gone = tmp_path / 'gone'
    gone.mkdir()
    (gone / 'project.json').write_text('{}')
    app._remember(gone)
    assert app._recent()[0] == str(gone)
    (gone / 'project.json').unlink()
    assert app._recent() == [str(app.project.root)]            # a project that no longer exists is not offered


def test_closing_the_project_clears_everything(app):
    app.open_correction()
    app.windows[1].withdraw()
    app.close_project()
    assert app.project is None and app.windows == {} and not app.tree.get_children()
    assert app.name_lbl.cget('text') == 'BookTrans'


def test_project_without_pipeline_result_explains_what_to_do(tmp_path, demo_dir):
    proj = Project.create(tmp_path / 'fresh', 'fresh', demo_dir / 'scans')
    a = gui.MainWindow(str(proj.root))
    a.withdraw()
    try:
        pump(a, lambda: 'not run yet' in a.info_lbl.cget('text') and 'No document.json' in a.views['model'].get_text())
        assert not a.tree.get_children() and str(a.open_btn.cget('state')) == 'disabled'
    finally:
        a.quit_app()


def test_new_project_dialog_validates_and_creates(app, tmp_path, demo_dir):
    d = gui_dialogs.NewProjectDialog(app, initial_dir=str(tmp_path))
    d.withdraw()
    d.grab_release()
    d._create()
    assert d.project is None and 'name' in d.error.cget('text')            # nothing filled in
    d.v['input'].set(str(demo_dir / 'scans'))
    pump(app)
    assert d.v['name'].get() == 'scans'                                    # taken from the input folder
    assert '2 TIFF files' in d.input_info.cget('text') and str(tmp_path / 'scans') in d.out_info.cget('text')
    d.v['name'].set('a/b')
    d._create()
    assert d.project is None and 'name' in d.error.cget('text')
    d.v['name'].set('book')
    d.v['input'].set(str(demo_dir / 'scans') + '/')                        # input changes again: the user's name stays
    pump(app)
    assert d.v['name'].get() == 'book'
    d.ocrfix.set('off')
    d._create()
    assert d.project is not None and d.project.root == tmp_path / 'book' and d.project.params['ocrfix'] == 'off'
    assert (tmp_path / 'book' / 'project.json').is_file()


def test_new_project_dialog_warns_about_an_empty_input_folder(app, tmp_path):
    (tmp_path / 'empty').mkdir()
    d = gui_dialogs.NewProjectDialog(app, initial_dir=str(tmp_path))
    d.withdraw()
    d.grab_release()
    d.v['input'].set(str(tmp_path / 'empty'))
    pump(app)
    assert 'No .tif' in d.input_info.cget('text')
    d.v['name'].set('x')
    d._create()
    assert d.project is None and 'no .tif' in d.error.cget('text')
    d.destroy()


def test_environment_window_lists_the_checks(app, monkeypatch):
    fake = [envcheck.Check('Group A', 'thing 1', envcheck.OK, 'fine', ''),
            envcheck.Check('Group A', 'thing 2', envcheck.FAIL, 'gone', 'install it'),
            envcheck.Check('Group B', 'thing 3', envcheck.WARN, 'meh', 'maybe fix it')]
    monkeypatch.setattr(envcheck, 'run_checks', lambda **kw: fake)
    w = gui_dialogs.EnvWindow(app, project=app.project)
    w.withdraw()
    pump(app, lambda: w.checks)
    assert w.summary.cget('text') == 'Something is missing'
    groups = w.tree.get_children()
    assert [w.tree.item(g, 'text') for g in groups] == ['Group A', 'Group B']
    first_fail = w.tree.get_children(groups[0])[1]
    w.tree.selection_set(first_fail)
    pump(app)
    assert 'install it' in w.hint.cget('text')
    w.destroy()


def test_command_dialog_warns_before_a_destructive_rerun(app):
    d = gui_dialogs.CommandDialog(app, app.project)
    d.withdraw()
    assert d.resume.get() is True and d.warn.cget('text') == ''            # a result exists: --resume is the default
    d.resume.set(False)
    d._fill()
    assert 'DELETES' in d.warn.cget('text') and '--resume' not in d.text.get('1.0', 'end')
    d.destroy()


def test_run_window_runs_the_project_command_and_reports_the_result(app, monkeypatch):
    import sys
    import time
    seen = []
    app.run_pipeline()
    w = app.run_window
    assert w is app.run_window and (app.run_pipeline() or app.run_window is w)       # one window, reused
    w.v['scale'].set('1.5')
    done = []
    w.on_done = lambda: done.append(1)
    monkeypatch.setattr(app.project, 'command', lambda resume=False: [sys.executable, '-c', 'print("stage1 hello"); print("done")'])
    monkeypatch.setattr(gui.messagebox if hasattr(gui, 'messagebox') else __import__('tkinter.messagebox').messagebox, 'askyesno', lambda *a, **k: True)
    w.resume.set(True)
    w.start()
    t0 = time.time()
    while not done and time.time() - t0 < 20:
        app.update()
        time.sleep(0.02)
    text = w.log.get('1.0', 'end')
    assert done and 'stage1 hello' in text and 'finished' in w.state.cget('text')
    assert app.project.params['scale'] == '1.5'               # the parameters set in the window are saved to project.json
    assert str(w.start_btn.cget('state')) == 'normal' and str(w.stop_btn.cget('state')) == 'disabled'


def test_rerun_ocr_correction_runs_stage_5_alone(app, monkeypatch):
    import sys
    import time
    import tkinter.messagebox as mb
    app.rerun_ocrfix()
    w = app.run_window
    assert w.mode == 'ocrfix' and 'OCR correction' in w.title() and not hasattr(w, 'resume_btn')
    assert len(w.controls) == 2                                    # only the OCR-correction mode and "suggest"
    app.run_pipeline()                                             # the other mode replaces an idle window
    assert app.run_window is not w and app.run_window.mode == 'pipeline'
    app.rerun_ocrfix()
    w = app.run_window
    done = []
    w.on_done = lambda: done.append(1)
    real = app.project.ocrfix_command()
    assert real[1].endswith('ocrfix.py') and '--edits' in real and '--decisions' in real
    monkeypatch.setattr(app.project, 'ocrfix_command', lambda: [sys.executable, '-c', 'print("ocrfix: 3 units")'])
    asked = []
    monkeypatch.setattr(mb, 'askyesno', lambda *a, **k: (asked.append(a), True)[1])
    w.unsaved = lambda: [7]
    w.start()
    assert asked and 'Page 7 has unsaved changes' in asked[0][1]
    infos = []
    monkeypatch.setattr(mb, 'showinfo', lambda *a, **k: infos.append(a))
    app.run_pipeline()                                             # a running window is not replaced
    assert app.run_window is w and infos and 'in progress' in infos[0][0]
    t0 = time.time()
    while not done and time.time() - t0 < 20:
        app.update()
        time.sleep(0.02)
    assert done and 'ocrfix: 3 units' in w.log.get('1.0', 'end') and 'finished' in w.state.cget('text')


def test_rerun_ocr_correction_reports_switched_off(app, monkeypatch):
    import tkinter.messagebox as mb
    errors = []
    monkeypatch.setattr(mb, 'showerror', lambda *a, **k: errors.append(a))
    app.rerun_ocrfix()
    w = app.run_window
    w.v['ocrfix'].set('off')
    w.start()
    assert errors and 'switched off' in errors[0][1] and w.run is None
    app.project.params['ocrfix'] = 'strict'                      # leave the project as the other tests expect it
    app.project.save()


def test_translation_window_saves_settings_and_runs(app, monkeypatch):
    import json
    import sys
    import time
    import tkinter.messagebox as mb
    app.run_translation()
    w = app.run_window
    assert w.mode == 'translate' and 'translation' in w.title().lower()
    assert str(w.endpoint_entry.cget('state')) == 'disabled'          # claude-code: no endpoint
    assert str(w.no_think_btn.cget('state')) == 'disabled'
    w.v['provider'].set('openai')
    app.update()
    assert str(w.endpoint_entry.cget('state')) == 'normal' and str(w.no_think_btn.cget('state')) == 'normal'
    assert 'LM Studio' in w.model_hint.cget('text')
    w.v['provider'].set('claude-code')
    w.v['model'].set('sonnet')
    w.v['pages'].set('1')
    errors, asked = [], []
    monkeypatch.setattr(mb, 'showerror', lambda *a, **k: errors.append(a))
    monkeypatch.setattr(mb, 'askyesno', lambda *a, **k: (asked.append(a), True)[1])
    w.v['retries'].set('x')
    w.start()
    assert errors and 'retries' in errors[-1][1] and w.run is None
    w.v['retries'].set('1')
    w.v['pages'].set('3-1')
    w.start()
    assert 'backwards' in errors[-1][1] and w.run is None
    w.v['pages'].set('1')
    seen = {}
    real = app.project.translate_command

    def fake(pages=None, dry_run=False, force=False):
        seen['cmd'] = real(pages, dry_run, force)
        out = app.project.translations_file
        code = ('import json, pathlib; p = pathlib.Path(%r); p.parent.mkdir(parents=True, exist_ok=True); '
                'p.write_text(json.dumps({"units": {"u0001": {"pl": "Tekst", "flags": []}, '
                '"u0002": {"pl": "Inny", "flags": ["x"]}}})); print("translate: done")' % str(out))
        return [sys.executable, '-c', code]
    monkeypatch.setattr(app.project, 'translate_command', fake)
    done = []
    w.on_done = lambda: (app._pipeline_done(), done.append(1))
    w.start()
    saved = json.loads(app.project.file.read_text(encoding='utf-8'))['translation']
    assert saved['model'] == 'sonnet' and saved['retries'] == 1 and 'pages' not in saved   # pages are per run
    c = seen['cmd']
    assert c[c.index('--model') + 1] == 'sonnet' and c[c.index('--pages') + 1] == '1'
    t0 = time.time()
    while not done and time.time() - t0 < 20:
        app.update()
        time.sleep(0.02)
    for _ in range(50):                                              # the reload runs in a worker thread
        app.update()
        time.sleep(0.02)
    assert done and 'finished' in w.state.cget('text')
    assert 'translation: 2 of' in app.info_lbl.cget('text') and '1 flagged' in app.info_lbl.cget('text')
    app.project.translations_file.unlink()
    app.project.data['translation'] = dict(__import__('project').DEFAULT_TRANSLATION)
    app.project.save()


def test_translation_window_force_asks_for_the_whole_book(app, monkeypatch):
    import sys
    import tkinter.messagebox as mb
    app.run_translation()
    w = app.run_window
    w.v['force'].set(True)
    asked = []
    monkeypatch.setattr(mb, 'askyesno', lambda *a, **k: (asked.append(a[0]), False)[1])
    w.start()                                                        # no pages: the whole book -> asked, declined
    assert asked == ['Re-translate the whole book?'] and w.run is None
    seen = {}
    monkeypatch.setattr(app.project, 'translate_command',
                        lambda pages=None, dry_run=False, force=False: (seen.update(pages=pages, force=force),
                                                                        [sys.executable, '-c', 'pass'])[1])
    monkeypatch.setattr(app.project, 'translation_warnings', lambda: [])
    asked.clear()
    w.v['pages'].set('2')
    w.start()                                                        # with pages: no question
    assert asked == [] and seen == {'pages': '2', 'force': True}
    w.stop()


def test_translation_window_warns_about_a_stale_corrected_copy(app, monkeypatch):
    import os
    import tkinter.messagebox as mb
    p = app.project
    corrected = p.files()['corrected']
    if not corrected.exists():
        return                                                       # the demo without symspellpy has no corrected copy
    p.edits_file.parent.mkdir(parents=True, exist_ok=True)
    had = p.edits_file.exists()
    if not had:
        p.edits_file.write_text('{"schema": "1.0", "units": {}}', encoding='utf-8')
    old = p.edits_file.stat().st_mtime
    t = corrected.stat().st_mtime
    os.utime(p.edits_file, (t + 10, t + 10))
    try:
        app.run_translation()
        w = app.run_window
        assert 'Re-run OCR correction' in w.warn.cget('text')
        asked = []
        monkeypatch.setattr(mb, 'askyesno', lambda *a, **k: (asked.append(a), False)[1])
        w.start()
        assert asked and asked[0][0] == 'Translate anyway?' and w.run is None
        w.v['source'].set('raw')
        app.update()
        assert 'raw' in w.warn.cget('text')
    finally:
        if had:
            os.utime(p.edits_file, (old, old))
        else:
            p.edits_file.unlink()


def test_run_window_rejects_bad_parameters(app, monkeypatch):
    import tkinter.messagebox as mb
    errors = []
    monkeypatch.setattr(mb, 'showerror', lambda *a, **k: errors.append(a))
    app.run_pipeline()
    w = app.run_window
    w.v['scale'].set('abc')
    w.start()
    assert errors and w.run is None


def test_compose_marks_draws_every_class_and_reports_hits(app):
    from PIL import Image
    import gui_correction as gc
    ix = app.index
    n = app.rows[0]['page']
    img = Image.open(ix.image_path(n)).convert('RGB')
    marks = ix.page_marks(n)
    assert marks
    size = (img.width // 2, img.height // 2)
    out, hits = gc.compose_marks(img, size, marks)
    assert out.size == size and len(hits) == sum(len(m['where']) for m in marks)
    from PIL import ImageChops
    assert ImageChops.difference(out, img.resize(size)).getbbox() is not None     # something was drawn
    none, no_hits = gc.compose_marks(img, size, marks, show=set())
    assert no_hits == [] and none.size == size
    for (x1, y1, x2, y2, m) in hits:                                          # hits lie inside the image, in its pixels
        assert 0 <= x1 < x2 <= size[0] + 4 and 0 <= y1 < y2 <= size[1] + 4
    assert gc.describe_mark(marks[0])


def test_correction_window_draws_marks_and_hover_names_them(app):
    app.tree.selection_set(str(app.rows[0]['page']))
    app.open_correction()
    w = list(app.windows.values())[0]
    w.update()
    assert w._hits, 'marks were not drawn'
    x1, y1, x2, y2, m = w._hits[0]

    class E:
        x, y = (x1 + x2) // 2 + w._origin[0], (y1 + y2) // 2 + w._origin[1]
    w._hover(E)
    assert w.info.cget('text')
    w.show['auto'].set(False)
    w._draw()
    assert all(h[4]['cls'] != 'auto' for h in w._hits)


def test_zoom_buttons_scale_the_page_and_fit_restores_it(app):
    app.tree.selection_set(str(app.rows[0]['page']))
    app.open_correction()
    w = list(app.windows.values())[0]
    w.update()
    assert w.zoom is None
    fit = w._scale
    w.zoom_by(1.25)
    assert w.zoom == pytest.approx(fit * 1.25) and w._photo.width() == int(w._img.width * w.zoom)
    w.zoom_by(1 / 1.25)
    w.zoom_by(1 / 1.25)
    assert w._scale < fit
    w.set_zoom(100)                                           # clamped
    assert w.zoom == 6.0
    w.set_zoom(1.0)
    assert w._photo.width() == w._img.width and 'scrollregion' in w.canvas.keys()
    assert w.canvas.cget('scrollregion')
    w.update()
    x1, y1, x2, y2, m = w._hits[0]                            # hover still finds the mark when zoomed
    class E:
        x, y = int((x1 + x2) / 2 - w.canvas.canvasx(0)) + w._origin[0], int((y1 + y2) / 2 - w.canvas.canvasy(0)) + w._origin[1]
    w._hover(E)
    assert w.info.cget('text')
    w.zoom_fit()
    assert w.zoom is None and w.zoom_lbl.cget('text').endswith('(fit)')


# ---------------------------------------------------------------- navigation and the editor
@pytest.fixture()
def edit_app(tmp_path):
    """A private demo project per test: saving writes review/unit_edits.json."""
    proj = gui_demo.make_demo(tmp_path / 'p')
    a = gui.MainWindow(str(proj.root))
    a.withdraw()
    pump(a, lambda: a.rows)
    yield a
    if a.winfo_exists():
        a.windows.clear()
        a.quit_app()


def _open(a, n):
    a.tree.selection_set(str(n))
    pump(a)
    a.open_correction()
    w = a.windows[n]
    w.withdraw()
    pump(a)
    return w


def _set_text(w, uid, new_text):
    """Change a unit's "text" in the editor the way a user would (through the JSON)."""
    d = json.loads(w.editor.get('1.0', 'end-1c'))
    next(u for u in d['units'] if u['unit_id'] == uid)['text'] = new_text
    w.editor.delete('1.0', 'end')
    w.editor.insert('1.0', json.dumps(d, ensure_ascii=False, indent=1))
    pump(w)


def test_prev_next_move_through_the_pages_in_one_window(edit_app):
    a = edit_app
    w = _open(a, 1)
    assert str(w.prev_btn.cget('state')) == 'disabled' and str(w.next_btn.cget('state')) == 'normal'
    w.go(1)
    assert w.page_no == 2 and list(a.windows) == [2] and a.windows[2] is w
    assert w.head.cget('text').startswith('Page 2') and a._selected_page() == 2
    assert w._orig['page'] == 2 and '"page": 2' in w.editor.get('1.0', 'end-1c')
    w.go(1)
    assert w.page_no == 3 and str(w.next_btn.cget('state')) == 'disabled'
    w.go(1)                                                     # at the end: nothing happens
    assert w.page_no == 3
    w.go(-1)
    w.go(-1)
    assert w.page_no == 1 and str(w.prev_btn.cget('state')) == 'disabled'


def test_next_shows_the_existing_window_of_that_page_instead_of_duplicating(edit_app):
    a = edit_app
    w1, w2 = _open(a, 1), _open(a, 2)
    w1.go(1)
    assert w1.page_no == 1 and a.windows == {1: w1, 2: w2}


def test_editing_a_unit_saves_an_edit_applies_it_and_marks_its_flags_as_reviewed(edit_app):
    a = edit_app
    w = _open(a, 1)
    before = next(r for r in a.rows if r['page'] == 1)
    assert before['uncertain'] + before['watch'] > 0
    uid = next(m['unit'] for m in w._marks if m['kind'] == 'flag')
    old = a.index.units[uid]['text']
    _set_text(w, uid, old + ' (geprüft)')
    assert w.dirty and 'Unsaved' in w.status.cget('text')
    assert w.save() is True
    f = a.project.edits_file
    saved = json.loads(f.read_text(encoding='utf-8'))
    assert saved['units'][uid]['text'] == old + ' (geprüft)' and saved['units'][uid]['base']
    u = a.index.units[uid]
    assert u['text'] == old + ' (geprüft)' and u['ocr_edited'] and u['text_ocrfix'] == old
    assert not w.dirty and 'Saved 1 unit' in w.status.cget('text')
    after = next(r for r in a.rows if r['page'] == 1)
    assert after['uncertain'] + after['watch'] < before['uncertain'] + before['watch'] and after['human'] > before['human']
    assert all(m['cls'] == 'human' for m in w._marks if m['unit'] == uid and m['kind'] == 'flag')
    review = json.loads(w.editor.get('1.0', 'end-1c'))                 # the editor shows the saved state
    assert next(x for x in review['units'] if x['unit_id'] == uid)['edited'] is True
    a.reload()                                                          # and the edit survives a reload from disk
    pump(a, lambda: a.status_lbl.cget('text') == '3 pages loaded.')
    assert a.index.units[uid]['text'] == old + ' (geprüft)'


def test_saving_back_to_the_pipeline_text_removes_the_edit(edit_app):
    a = edit_app
    w = _open(a, 1)
    uid = next(m['unit'] for m in w._marks if m['kind'] == 'flag')
    old = a.index.units[uid]['text']
    _set_text(w, uid, old + '!')
    assert w.save()
    _set_text(w, uid, old)
    assert w.save()
    assert json.loads(a.project.edits_file.read_text(encoding='utf-8'))['units'] == {}
    assert not a.index.units[uid].get('ocr_edited') and a.index.units[uid]['text'] == old


def test_a_forbidden_change_is_refused_and_nothing_is_written(edit_app):
    a = edit_app
    w = _open(a, 1)
    d = json.loads(w.editor.get('1.0', 'end-1c'))
    d['units'][0]['text_raw'] = 'changed'
    w.editor.delete('1.0', 'end')
    w.editor.insert('1.0', json.dumps(d))
    assert w.save() is False and 'text_raw' in w.status.cget('text') and not a.project.edits_file.exists()
    w.editor.delete('1.0', 'end')
    w.editor.insert('1.0', '{"units": [')
    assert w.save() is False and 'not valid JSON' in w.status.cget('text')
    w.revert()
    assert not w.dirty and json.loads(w.editor.get('1.0', 'end-1c'))['units']


def test_unsaved_changes_are_not_lost_silently(edit_app, monkeypatch):
    import tkinter.messagebox as mb
    a = edit_app
    w = _open(a, 1)
    uid = w._orig['units'][0]['unit_id']
    _set_text(w, uid, 'something else')
    answers = [None]                                          # Cancel
    monkeypatch.setattr(mb, 'askyesnocancel', lambda *x, **k: answers[0])
    w.go(1)
    assert w.page_no == 1 and w.dirty
    answers[0] = False                                        # Discard
    w.go(1)
    assert w.page_no == 2 and not a.project.edits_file.exists()
    _set_text(w, w._orig['units'][0]['unit_id'], 'again')
    answers[0] = True                                         # Save
    w.close()
    assert a.windows == {} and a.project.edits_file.exists()
    w2 = _open(a, 1)
    _set_text(w2, w2._orig['units'][0]['unit_id'], 'third')
    monkeypatch.setattr(mb, 'askyesno', lambda *x, **k: False)
    assert a._can_close_windows() is False
    w2.revert()
    assert a._can_close_windows() is True


def test_clicking_a_mark_puts_the_cursor_on_its_word_in_the_text(edit_app):
    a = edit_app
    w = _open(a, 1)
    seen = 0
    for m in w._marks:
        if not m['where']:
            continue
        assert w.locate(m), m
        rng = w.editor.tag_ranges('locate')
        word = w.editor.get(rng[0], rng[1])
        want = json.dumps(m['after'] if m['kind'] in ('auto', 'human') and m.get('after') else m['token'], ensure_ascii=False)[1:-1]
        assert word == want, (m['kind'], word, want)
        seen += 1
    assert seen >= 5


def test_a_stale_edit_is_reported_and_not_applied(edit_app):
    a = edit_app
    f = a.project.edits_file
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({'schema': '1.0', 'units': {'u0001': {'base': 'deadbeef', 'text': 'x', 'edited': 'now'}}}))
    old = a.index.units['u0001']['text']
    a.reload()
    pump(a, lambda: 'not applied' in a.status_lbl.cget('text'))
    assert a.stale_edits == ['u0001'] and a.index.units['u0001']['text'] == old


def test_an_image_finalised_in_another_thread_is_parked_not_freed_there(app):
    import threading
    import gui_widgets as gw
    from PIL import Image
    p = gw.SafePhoto(Image.new('RGB', (4, 4)), master=app)
    name = str(p)
    t = threading.Thread(target=lambda: gw.SafePhoto.__del__(p))
    t.start()
    t.join()
    assert p in gw._PENDING_IMAGES and name in app.tk.call('image', 'names')       # no Tk call was made from the thread
    gw.release_pending_images()
    assert not gw._PENDING_IMAGES and name not in app.tk.call('image', 'names')


def test_find_in_the_editor_highlights_counts_and_cycles(edit_app):
    a = edit_app
    w = _open(a, 1)
    text = w.editor.get('1.0', 'end-1c')
    want = text.lower().count('portal')
    assert want >= 2
    w.find_var.set('PORTAL')                                         # case-insensitive
    pump(w)
    assert len(w._matches) == want and w.find_count.cget('text') == '1 of %d' % want
    assert len(w.editor.tag_ranges('found')) == 2 * want and len(w.editor.tag_ranges('current')) == 2
    first = w.editor.index('insert')
    for k in range(2, want + 1):
        w.find_next(1)
        assert w.find_count.cget('text') == '%d of %d' % (k, want)
    w.find_next(1)                                                   # wraps around
    assert w.find_count.cget('text') == '1 of %d' % want and w.editor.index('insert') == first
    w.find_next(-1)
    assert w.find_count.cget('text') == '%d of %d' % (want, want)
    assert w.editor.get('insert', 'insert+6c').lower() == 'portal'
    w.find_var.set('no such word anywhere')
    assert w.find_count.cget('text') == 'no match' and not w.editor.tag_ranges('found')
    w.find_var.set('')
    assert w.find_count.cget('text') == ''


def test_find_still_works_after_the_text_was_edited_and_after_a_page_change(edit_app):
    a = edit_app
    w = _open(a, 1)
    w.find_var.set('portal')
    pump(w)
    n = len(w._matches)
    w.editor.insert('1.0', 'portal portal ')                          # typing adds matches before the cursor
    w.find_next(1)
    assert len(w._matches) == n + 2
    w.revert()                                                       # (unsaved typing would ask before leaving the page)
    assert len(w._matches) == n
    w.go(1)                                                          # a new page refills the editor: highlights follow
    pump(w)
    assert len(w._matches) == w.editor.get('1.0', 'end-1c').lower().count('portal')


# ---------------------------------------------------------------- decisions about flags
def _open_flag(w):
    return next(m for m in w._marks if m['kind'] == 'flag' and m['where'])


def test_accept_a_flag_as_it_is_turns_it_green_and_writes_the_decision(edit_app):
    a = edit_app
    w = _open(a, 1)
    m = _open_flag(w)
    before = next(r for r in a.rows if r['page'] == 1)
    labels = [i[0] for i in w.menu_for(m) if i]
    assert 'Accept as it is' in labels and any(l.startswith('Accept') and 'everywhere' in l for l in labels)
    assert dict(i for i in w.menu_for(m) if i)['Accept as it is']() is True
    f = a.project.decisions_file
    assert json.loads(f.read_text(encoding='utf-8'))['resolutions'] == {m['key']: '='}
    mm = next(x for x in w._marks if x['key'] == m['key'])
    assert mm['cls'] == 'human' and mm['decision'] == 'accepted'
    after = next(r for r in a.rows if r['page'] == 1)
    assert after['human'] == before['human'] + 1 and after['uncertain'] + after['watch'] == before['uncertain'] + before['watch'] - 1
    assert 'accepted as it is' in w.status.cget('text') and 'ocr_decisions.json' in w.status.cget('text')
    assert 'you decided' in gui_describe(mm)
    a.reload()                                                       # survives a reload from disk
    pump(a, lambda: a.status_lbl.cget('text') == '3 pages loaded.')
    assert next(x for x in a.index.page_marks(1) if x['key'] == m['key'])['cls'] == 'human'


def gui_describe(m):
    import gui_correction as gc
    return gc.describe_mark(m)


def test_replace_with_a_candidate_and_undo(edit_app):
    a = edit_app
    w = _open(a, 1)
    m = next(x for x in w._marks if x['kind'] == 'flag' and x['candidates'] and x['where'])
    items = dict(i for i in w.menu_for(m) if i)
    cand = m['candidates'][0]
    assert items['Replace with „%s”' % cand]() is True
    assert json.loads(a.project.decisions_file.read_text(encoding='utf-8'))['resolutions'][m['key']] == cand
    mm = next(x for x in w._marks if x['key'] == m['key'])
    assert mm['decision'] == 'replaced:%s' % cand and 'replace with %s' % cand in gui_describe(mm)
    undo = dict(i for i in w.menu_for(mm) if i)
    assert list(undo) == ['Find in the text', 'Undo my decision']        # a decided flag offers undo, not a second decision
    undo['Undo my decision']()
    assert json.loads(a.project.decisions_file.read_text(encoding='utf-8'))['resolutions'] == {}
    assert next(x for x in w._marks if x['key'] == m['key'])['cls'] != 'human'


def test_accept_a_word_everywhere_covers_every_occurrence(edit_app):
    a = edit_app
    w = _open(a, 1)
    m = _open_flag(w)
    same = [x for x in a.index.page_marks(1) if x['kind'] == 'flag' and x['token'].lower() == m['token'].lower()]
    w.accept_everywhere(m, True)
    assert json.loads(a.project.decisions_file.read_text(encoding='utf-8'))['accepted_words'] == [m['token']]
    assert all(x['decision'] == 'word accepted' and x['cls'] == 'human' for x in a.index.page_marks(1) if x['token'].lower() == m['token'].lower()) and same
    mm = next(x for x in w._marks if x['key'] == m['key'])
    assert 'Undo my decision' in [i[0] for i in w.menu_for(mm) if i]
    w.accept_everywhere(mm, False)
    assert json.loads(a.project.decisions_file.read_text(encoding='utf-8'))['accepted_words'] == []


def test_accept_all_watch_words_on_a_page_and_confirm_for_uncertain(edit_app, monkeypatch):
    import tkinter.messagebox as mb
    a = edit_app
    w = _open(a, 2)
    watch = {m['key'] for m in w._marks if m['kind'] == 'flag' and m['cls'] == 'watch'}
    unc = {m['key'] for m in w._marks if m['kind'] == 'flag' and m['cls'] == 'uncertain'}
    assert watch and unc
    assert w.accept_page(('watch',)) is True
    assert set(json.loads(a.project.decisions_file.read_text(encoding='utf-8'))['resolutions']) == watch
    asked = []
    monkeypatch.setattr(mb, 'askyesno', lambda *x, **k: asked.append(x) or False)
    assert w.accept_page(('uncertain', 'watch')) is False and asked        # asked, declined: nothing changed
    assert set(json.loads(a.project.decisions_file.read_text(encoding='utf-8'))['resolutions']) == watch
    monkeypatch.setattr(mb, 'askyesno', lambda *x, **k: True)
    assert w.accept_page(('uncertain', 'watch')) is True
    assert set(json.loads(a.project.decisions_file.read_text(encoding='utf-8'))['resolutions']) == watch | unc
    assert w.accept_page(('uncertain', 'watch')) is False                   # nothing left open
    row = next(r for r in a.rows if r['page'] == 2)
    assert row['uncertain'] == 0 and row['watch'] == 0


def test_a_broken_decisions_file_is_reported_not_ignored(edit_app):
    a = edit_app
    a.project.decisions_file.parent.mkdir(parents=True, exist_ok=True)
    a.project.decisions_file.write_text('{broken')
    a.reload()
    pump(a, lambda: 'Cannot load' in a.status_lbl.cget('text'))


def test_caption_dialog_rereads_and_applies_into_the_review_json(app, monkeypatch):
    import caption_tools
    import gui_correction
    page = next(r['page'] for r in app.rows
                if any(u.get('role') == 'caption' for u in app.index.page_review(r['page'])['units']))
    app.tree.selection_set(str(page))
    app.open_correction()
    w = app.windows[page]
    w.update()
    monkeypatch.setattr(caption_tools, 'read_again', lambda img, box, lang, ocr=None: ('37 PITSCHEN OS, PORTAL', 91.0))
    w.open_captions()
    dlg = [c for c in w.winfo_children() if isinstance(c, gui_correction.CaptionDialog)][0]
    dlg.update()
    assert dlg.listbox.size() >= 1 and dlg._photo is not None
    dlg.read_again()
    assert dlg.text.get('1.0', 'end-1c') == '37 PITSCHEN OS, PORTAL'
    uid = dlg.caps[0]['unit_id']
    dlg.apply()
    assert w.dirty
    units = {u['unit_id']: u for u in json.loads(w.editor.get('1.0', 'end-1c'))['units']}
    assert units[uid]['text'] == '37 PITSCHEN OS, PORTAL'
    w.editor.edit_modified(False)                      # not saved: closing the app must not ask about it


def test_the_app_icon_is_set(app):
    assert gui.ICON.is_file()
    assert getattr(app, '_app_icon', None) is not None and app._app_icon.width() == 512


def test_glossary_window_lists_layers_and_decides_candidates(app):
    import glossary_harvest as H
    import glossary_store as GS
    GS.add_project(app.project.root, {'de': 'Pitschen', 'pl': 'Byczyna (ten tom)', 'kind': 'place'})
    H.write_candidates(app.project.candidates_file, [
        {'de': 'Kalkau', 'pl': 'Kałków', 'kind': 'place', 'count': 2, 'alternatives': [], 'units': ['u1']},
        {'de': 'Portal', 'pl': 'portal', 'kind': 'term', 'count': 1, 'alternatives': [], 'units': ['u2']}])
    app.open_glossary()
    w = app.glossary_window
    w.update()
    layers = [w.rows[int(i)]['_layer'] for i in w.tree.get_children()]
    assert layers[0] == 'project' and 'central' in layers
    assert len(w.ctree.get_children()) == 2
    w.ctree.selection_set('0')
    w.decide('central')
    w.ctree.selection_set('0')
    w.decide('reject')
    assert not w.cands and any(r['de'] == 'Kalkau' and r['_layer'] == 'central' for r in w.rows)
    assert ('portal', 'term') in GS.rejected()
    app.open_glossary()
    assert app.glossary_window is w

"""booktrans.process (single TIFF) honours --resume like a book: stages 1-3 kept, stage 4 and document.json redone."""
import json
from pathlib import Path

import booktrans as B


def _fake(monkeypatch):
    calls = []

    def run(cmd):
        name = Path(str(cmd[0])).name
        calls.append(name)
        out = Path(str(cmd[cmd.index('-o') + 1]))
        out.mkdir(parents=True, exist_ok=True)
        if name == 'stage3.py':
            (out / 'page_structure_stage3.json').write_text('{}', encoding='utf-8')
        if name == 'stage4.py':
            (out / 'units.json').write_text('{"pages": [], "units": []}', encoding='utf-8')
    monkeypatch.setattr(B, 'run', run)
    monkeypatch.setattr(B, 'build_document', lambda units, s1, out, tiff: {'pages': [], 'units': []})
    return calls


def test_single_tiff_resume_keeps_stages_1_to_3(tmp_path, monkeypatch):
    calls = _fake(monkeypatch)
    tiff = tmp_path / 'a_0001.tif'
    tiff.write_bytes(b'')
    out, _ = B.process(tiff, tmp_path / 'o')
    assert calls == ['stage1.py', 'stage2.py', 'stage3.py', 'stage4.py']
    keep = out / 'document.corrected.json'
    keep.write_text('{}', encoding='utf-8')                     # stands for anything stage 5 or a human left there
    calls.clear()
    out2, _ = B.process(tiff, tmp_path / 'o', resume=True)
    assert out2 == out and calls == ['stage4.py'] and keep.exists()
    assert json.loads((out / 'document.json').read_text(encoding='utf-8')) == {'pages': [], 'units': []}
    calls.clear()
    B.process(tiff, tmp_path / 'o')                              # without --resume: everything, the folder is recreated
    assert calls == ['stage1.py', 'stage2.py', 'stage3.py', 'stage4.py'] and not keep.exists()


def test_single_tiff_resume_without_finished_stages_runs_everything(tmp_path, monkeypatch):
    calls = _fake(monkeypatch)
    tiff = tmp_path / 'a_0001.tif'
    tiff.write_bytes(b'')
    B.process(tiff, tmp_path / 'o', resume=True)
    assert calls == ['stage1.py', 'stage2.py', 'stage3.py', 'stage4.py']

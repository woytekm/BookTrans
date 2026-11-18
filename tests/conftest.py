"""Make the stage scripts importable the same way they run: from their own directory."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'src' / 'booktrans'))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _private_glossary(tmp_path_factory, monkeypatch):
    """Tests never touch the user's central glossary or configuration (glossary_store)."""
    d = tmp_path_factory.mktemp('central')
    monkeypatch.setenv('BOOKTRANS_CONFIG', str(d / 'config.json'))
    monkeypatch.setenv('BOOKTRANS_GLOSSARY', str(d / 'glossary'))

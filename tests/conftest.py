"""Make the stage scripts importable the same way they run: from their own directory."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'src' / 'booktrans'))

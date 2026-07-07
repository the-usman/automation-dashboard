"""Pytest bootstrap: put the repo root on sys.path.

The project isn't installed as a package, so top-level packages (`app`,
`social_trend_analyzer`, `reputation_analyzer`) are only importable when the repo
root is on the path. pytest's prepend import mode inserts `tests/`, not the root,
so tests failed with ModuleNotFoundError unless run with PYTHONPATH set. Being at
the rootdir, this conftest guarantees the root is importable for every test run.
"""
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

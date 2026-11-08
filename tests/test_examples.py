"""The offline example must keep working: it demonstrates adaptive selectors surviving a redesign."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "02_parse_and_adapt.py"


def test_adaptive_example_relocates_after_redesign():
    out = subprocess.run([sys.executable, str(EXAMPLE)], capture_output=True, text=True, timeout=60, check=True).stdout
    assert "before: ['$10.00', '$12.50']" in out
    assert "plain selector after redesign: []" in out
    assert "adaptive selector after redesign: ['$10.00', '$12.50']" in out

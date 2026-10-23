"""Verify the daily history page with synthetic data in Node when available."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for the rendered board check")
def test_daily_history_renders_kinds_endings_and_definitions():
    result = subprocess.run(["node", str(ROOT / "tests/history_render.js"),
                             str(ROOT / "bin/session-board.html")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout

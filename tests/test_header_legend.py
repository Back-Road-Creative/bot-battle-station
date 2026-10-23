"""The header totals and legend must agree for a mixed-bot board."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for the rendered board check")
def test_mixed_bot_header_and_legend_render():
    result = subprocess.run(["node", str(ROOT / "tests/header_legend_render.js"),
                             str(ROOT / "bin/session-board.html")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout

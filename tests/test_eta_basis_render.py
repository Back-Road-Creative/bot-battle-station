"""Run the board's job tooltip contract in Node when available."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for the rendered board check")
def test_job_tooltip_names_its_estimate_basis():
    result = subprocess.run(["node", str(ROOT / "tests/eta_basis_render.js"),
                             str(ROOT / "bin/session-board.html")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout

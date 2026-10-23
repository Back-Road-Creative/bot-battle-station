"""Per-session alert snooze uses the actual board page handlers."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which("node"), reason="Node is not available")
def test_snooze_is_per_session_and_expires():
    result = subprocess.run(["node", str(ROOT / "tests/board_snooze.js"),
                             str(ROOT / "bin/session-board.html")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "snooze ok"

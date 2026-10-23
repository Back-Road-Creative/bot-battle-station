"""Keyboard contracts for session tiles and ask controls."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which("node"), reason="Node is not available")
def test_tile_and_ask_can_be_opened_from_the_keyboard():
    result = subprocess.run(["node", str(ROOT / "tests/board_keyboard.js"),
                             str(ROOT / "bin/session-board.html")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "keyboard ok"

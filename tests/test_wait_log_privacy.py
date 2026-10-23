"""A prompt preview in the wait history must be bounded."""

import importlib.util
import json
from pathlib import Path


BOARD = Path(__file__).resolve().parents[1] / "bin/session-board.py"
spec = importlib.util.spec_from_file_location("session_board", BOARD)
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)


def test_wait_history_stores_only_a_bounded_ask_preview(tmp_path):
    path = tmp_path / "waits.jsonl"
    waits = board.WaitLog(path)
    row = {"sid": "abcd1234", "name": "example", "state": "NEEDS YOU",
           "ask": "x" * 10000, "waited": 0}
    waits.update([row], 1000)
    waits.update([], 1001)

    saved = json.loads(path.read_text())
    assert len(saved["ask"]) <= 240
    assert saved["ask"] == "x" * 240
    assert waits.done == [saved]


def test_short_ask_is_preserved(tmp_path):
    waits = board.WaitLog(tmp_path / "waits.jsonl")
    row = {"sid": "abcd1234", "name": "example", "state": "NEEDS YOU",
           "ask": "Approve the plan?", "waited": 0}
    waits.update([row], 1000)
    waits.update([], 1001)
    assert waits.done[0]["ask"] == "Approve the plan?"

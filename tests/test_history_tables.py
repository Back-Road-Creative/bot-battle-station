"""Daily history records observed ask sources and warning endings."""

import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path


BOARD = Path(__file__).resolve().parents[1] / "bin/session-board.py"
spec = importlib.util.spec_from_file_location("session_board_history", BOARD)
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)


def session(tr, status):
    return {"tr": tr, "jobs": [], "name": "example", "role": "", "sid": "abc12345",
            "status": status, "uptime": 60, "eta": "ready", "progress": {"text": "ready", "left": None,
            "at": None, "pct": None, "behind": False}, "plan": None,
            "tokens": {"today": 0, "cache": 0, "helpers": 0}}


def test_wait_history_distinguishes_prompt_from_configured_command(tmp_path, monkeypatch):
    now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
    tr = board.Transcript(tmp_path / "session.jsonl")
    prompt = board.row_json({**session(tr, "waiting"), "waiting_for": "Approve Bash"}, now)
    assert prompt["ask_kind"] == "prompt"

    monkeypatch.setattr(board, "CMD_RE", re.compile(r"(run this command)"))
    tr.reply = "Please run this command"
    command = board.row_json(session(tr, "idle"), now)
    assert command["ask_kind"] == "command"

    path = tmp_path / "waits.jsonl"
    waits = board.WaitLog(path)
    waits.update([prompt], now)
    waits.update([], now + 30)
    waits.update([command], now + 60)
    waits.update([], now + 90)
    assert [json.loads(line)["ask_kind"] for line in path.read_text().splitlines()] == ["prompt", "command"]


def test_warning_end_reason_only_claims_what_the_board_observed(tmp_path):
    now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
    path = tmp_path / "alerts.jsonl"
    alerts = board.AlertLog(path)

    def row(sid, warn):
        return {"sid": sid, "name": sid, "state": "RUNNING", "warn": warn, "jobs": []}

    alerts.update([row("cleared", ["context near full"]), row("missing", ["context near full"])], now)
    alerts.update([row("cleared", [])], now + 30)
    saved = {r["sid"]: r for r in map(json.loads, path.read_text().splitlines())}
    assert saved["cleared"]["end_reason"] == "condition_cleared"
    assert saved["missing"]["end_reason"] == "session_not_visible"
    assert alerts.stats(now + 60, [], [])["end_reasons"] == {
        "context near full": {"condition_cleared": 1, "session_not_visible": 1}}


def test_ask_kind_totals_keep_legacy_records_as_unknown():
    now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
    waits = [{"sid": "a", "start": now, "end": now + 10, "secs": 10, "ask_kind": "prompt"},
             {"sid": "b", "start": now, "end": now + 20, "secs": 20, "ask_kind": "command"},
             {"sid": "c", "start": now, "end": now + 30, "secs": 30}]
    stats = board.AlertLog(None).stats(now + 90, waits, [])
    assert stats["asks_by_kind"] == {"command": {"count": 1, "median": 20},
                                     "prompt": {"count": 1, "median": 10},
                                     "unknown": {"count": 1, "median": 30}}


def test_daily_history_is_available_without_opening_the_help_modal():
    html = BOARD.with_suffix(".html").read_text()
    assert html.index('id="today"') < html.index('id="legend"')

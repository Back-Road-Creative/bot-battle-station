"""CLI options must affect the web board and report only source availability."""

import importlib.util
import argparse
import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("board_cli", ROOT / "bin/session-board.py")
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)


def test_web_page_receives_configured_poll_interval(tmp_path):
    server = board.make_server(board.Board(tmp_path / "claude", None), 0, interval=5.5)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/").read().decode()
    finally:
        server.shutdown()
    assert "const POLL_INTERVAL_MS = 5500;" in page
    assert "setInterval(tick, POLL_INTERVAL_MS)" in page


def test_web_run_passes_cli_interval_to_server(tmp_path, monkeypatch):
    seen = {}

    class Server:
        server_address = ("127.0.0.1", 8765)

        def serve_forever(self):
            return None

    def make_server(*args):
        seen["interval"] = args[-1]
        return Server()

    monkeypatch.setattr(board, "make_server", make_server)
    args = argparse.Namespace(claude_dir=tmp_path / "claude", roster=None, codex_dir=tmp_path / "codex",
                              waits_log=None, alerts_log=None, once=False, tui=False, port=8765, interval=5.5)
    cfg = {"roster": None, "state_dir": tmp_path / "state", "claude_limits": None}
    assert board.run_board(args, cfg) == 0
    assert seen["interval"] == 5.5


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "0.1", "3601"])
def test_invalid_intervals_are_rejected_before_launch(value, capsys):
    with pytest.raises(SystemExit) as error:
        board.main(["--interval", value, "--once", "--claude-dir", "/tmp/empty-board-test-input"])
    assert error.value.code == 2
    assert "interval" in capsys.readouterr().err.lower()


def test_doctor_reports_codex_and_limits_sources_without_values(tmp_path, monkeypatch, capsys):
    claude = tmp_path / "claude"
    (claude / "sessions").mkdir(parents=True)
    codex = tmp_path / "codex"
    day = codex / "sessions" / "2026" / "10" / "23"
    day.mkdir(parents=True)
    sid = "0c0dec00-aaaa-7bbb-8ccc-ddddeeee0001"
    (day / "rollout.jsonl").write_text(json.dumps({"type": "session_meta", "payload": {"id": sid}}) + "\n")
    limits = tmp_path / "private-limits.json"
    limits.write_text(json.dumps({"five_hour": {"used_percentage": 72, "resets_at": int(time.time()) + 3600}}))
    cfg = tmp_path / "board.toml"
    cfg.write_text(f'claude_limits = "{limits}"\n')
    monkeypatch.setattr(board, "codex_open_files", lambda: None)
    assert board.main(["--doctor", "--claude-dir", str(claude), "--codex-dir", str(codex),
                       "--config", str(cfg)]) == 0
    report = capsys.readouterr().out
    for part in ("codex sessions dir: found", "codex live sessions: 1", "Claude limits file: found",
                 "Claude limits windows: 1"):
        assert part in report
    for secret in (str(tmp_path), sid, "72", "private-limits"):
        assert secret not in report

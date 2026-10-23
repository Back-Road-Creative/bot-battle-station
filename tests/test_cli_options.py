"""CLI options must affect the web board and report only source availability."""

import importlib.util
import argparse
import io
import json
import stat
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


def write_from_status_line(monkeypatch, payload: bytes, config: Path) -> int:
    with monkeypatch.context() as local:
        local.setattr(board.sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
        return board.main(["--write-limits", "--config", str(config)])


def test_write_limits_saves_only_supported_windows_for_the_board(tmp_path, monkeypatch, capsys):
    target = tmp_path / "private" / "limits.json"
    config = tmp_path / "board.toml"
    config.write_text(f'claude_limits = "{target}"\n')
    resets = int(time.time()) + 3600
    payload = json.dumps({"cwd": "/secret/project", "secret_token": "do-not-save", "rate_limits": {
        "five_hour": {"used_percentage": 73, "resets_at": resets, "extra": "do-not-save"},
        "seven_day": {"used_percentage": 24, "resets_at": resets},
        "other_window": {"used_percentage": 99}}}).encode()
    assert write_from_status_line(monkeypatch, payload, config) == 0
    saved = target.read_text()
    assert json.loads(saved) == {"five_hour": {"used_percentage": 73, "resets_at": resets},
                                 "seven_day": {"used_percentage": 24, "resets_at": resets}}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(target.parent.glob("*.tmp"))
    assert board.Board(tmp_path / "claude", None, limits_file=target).bots([], time.time())["claude"]["limits"] == [
        {"name": "5h", "used": 73, "resets": resets}, {"name": "weekly", "used": 24, "resets": resets}]
    assert capsys.readouterr().out == ""


def test_write_limits_keeps_previous_file_when_no_limits_arrive(tmp_path, monkeypatch):
    target = tmp_path / "limits.json"
    target.write_text('{"five_hour":{"used_percentage":12}}\n')
    config = tmp_path / "board.toml"
    config.write_text(f'claude_limits = "{target}"\n')
    before = target.read_bytes()
    assert write_from_status_line(monkeypatch, b'{"cwd":"/secret/project"}', config) == 0
    assert target.read_bytes() == before


@pytest.mark.parametrize("payload", [pytest.param(b'not json', id="invalid-json"),
                                     pytest.param(b'{"rate_limits": []}', id="wrong-shape"),
                                     pytest.param(b'{"rate_limits":{"five_hour":{"used_percentage":{"secret":"x"}}}}',
                                                  id="nested-value"),
                                     pytest.param(b'{"rate_limits":{"five_hour":{"used_percentage":' +
                                                  b'9' * 1000 + b'}}}', id="huge-number"),
                                     pytest.param(b'x' * 1_048_577, id="oversize")])
def test_write_limits_rejects_bad_input_without_replacing_file(tmp_path, monkeypatch, capsys, payload):
    target = tmp_path / "limits.json"
    target.write_text('{"five_hour":{"used_percentage":12}}\n')
    config = tmp_path / "board.toml"
    config.write_text(f'claude_limits = "{target}"\n')
    before = target.read_bytes()
    assert write_from_status_line(monkeypatch, payload, config) == 2
    assert target.read_bytes() == before
    error = capsys.readouterr().err
    assert "limits" in error.lower()
    assert str(tmp_path) not in error


def test_write_limits_replace_failure_keeps_old_file_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "limits.json"
    target.write_text("old\n")

    def fail_replace(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(board.os, "replace", fail_replace)
    with pytest.raises(OSError):
        board.write_limits(io.BytesIO(b'{"rate_limits":{"five_hour":{"used_percentage":30}}}'), target)
    assert target.read_text() == "old\n"
    assert not list(tmp_path.glob(".claude-limits-*.tmp"))

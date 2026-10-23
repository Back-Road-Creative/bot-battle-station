"""Reproducers for local server and configuration edge cases."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


BOARD = Path(__file__).resolve().parents[1] / "bin/session-board.py"
spec = importlib.util.spec_from_file_location("session_board_hardening", BOARD)
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)


def test_server_threads_do_not_keep_shutdown_waiting(tmp_path):
    server = board.make_server(board.Board(tmp_path / "sessions", None), 0)
    try:
        assert server.daemon_threads is True
    finally:
        server.server_close()


def test_config_expands_a_home_state_directory(tmp_path):
    config = tmp_path / "session-board.toml"
    config.write_text('state_dir = "~/board-state"\n')
    assert board.configure(config)["state_dir"] == Path.home() / "board-state"


def test_config_ignores_an_unrelated_empty_git_directory(tmp_path):
    (tmp_path / ".git").mkdir()
    config = tmp_path / "nested" / "session-board.toml"
    config.parent.mkdir()
    config.write_text('state_dir = "board-state"\n')
    assert board.configure(config)["state_dir"] == config.parent / "board-state"


def test_invalid_config_exits_cleanly_without_a_traceback(tmp_path):
    config = tmp_path / "broken.toml"
    config.write_text("state_dir = [broken\n")
    result = subprocess.run([sys.executable, str(BOARD), "--once", "--config", str(config)],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert "invalid config" in result.stderr
    assert "Traceback" not in result.stderr


def test_version_flag_reports_the_release_version():
    result = subprocess.run([sys.executable, str(BOARD), "--version"], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.strip() == "0.3.0"


def test_missing_start_time_is_unknown_not_1970(tmp_path):
    sid = "0a1b2c3d-1111-4222-8333-444455556666"
    claude = tmp_path / "claude"
    (claude / "sessions").mkdir(parents=True)
    (claude / "sessions" / f"{os.getpid()}.json").write_text(json.dumps({
        "pid": os.getpid(), "sessionId": sid, "status": "idle", "name": "example"
    }))
    transcript = claude / "projects" / "example" / f"{sid}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("")
    now = time.time()
    [row] = board.Board(claude, None).snapshot(now)
    public = board.row_json(row, now)
    assert public["started"] is None
    assert public["uptime"] == "unknown"


def test_demo_removes_its_temporary_files_after_once(monkeypatch, capsys):
    created = []
    original = tempfile.mkdtemp

    def record(*args, **kwargs):
        path = original(*args, **kwargs)
        created.append(Path(path))
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", record)
    assert board.main(["--demo", "--once"]) == 0
    capsys.readouterr()
    assert created
    assert all(not path.exists() for path in created)


def test_jsonl_append_is_one_bounded_utf8_write(tmp_path, monkeypatch):
    path = tmp_path / "state" / "history.jsonl"
    writes = []
    real_write = os.write

    def record(fd, data):
        writes.append(data)
        return real_write(fd, data)

    monkeypatch.setattr(board.os, "write", record)
    record_at_limit = {"text": "é" * 20}
    line = (json.dumps(record_at_limit, ensure_ascii=False) + "\n").encode("utf-8")
    assert board.append_jsonl(path, record_at_limit, max_bytes=len(line)) is True
    assert writes == [line]
    assert path.read_bytes() == line
    assert board.append_jsonl(path, {"text": "é" * 21}, max_bytes=len(line)) is False
    assert writes == [line]
    assert path.read_bytes() == line

    def short_write(fd, data):
        real_write(fd, data[:3])
        return 3

    monkeypatch.setattr(board.os, "write", short_write)
    assert board.append_jsonl(path, {"text": "next"}) is False
    assert path.read_bytes() == line, "a short write must not leave a partial JSONL line"


def test_wait_and_alert_history_use_the_bounded_appender(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(board, "append_jsonl", lambda path, rec: calls.append((path.name, rec)))
    waits = board.WaitLog(tmp_path / "waits.jsonl")
    waits.update([{"sid": "a", "name": "A", "ask": "Approve", "ask_kind": "prompt",
                   "state": "NEEDS YOU", "waited": 0}], 100)
    waits.update([], 101)
    alerts = board.AlertLog(tmp_path / "alerts.jsonl")
    alerts.update([{"sid": "a", "name": "A", "state": "RUNNING", "warn": ["job behind schedule"],
                    "jobs": [{"late": True, "stalled": False, "label": "job"}]}], 100)
    alerts.update([], 101)
    assert [name for name, _ in calls] == ["waits.jsonl", "alerts.jsonl"]


def test_oversized_warning_is_not_persisted_as_a_partial_line(tmp_path):
    path = tmp_path / "alerts.jsonl"
    alerts = board.AlertLog(path)
    alerts.update([{"sid": "a", "name": "A", "state": "RUNNING", "warn": ["job behind schedule"],
                    "jobs": [{"late": True, "stalled": False, "label": "x" * board.LOG_LINE_MAX_BYTES}]}], 100)
    alerts.update([], 101)
    assert len(alerts.done) == 1, "the current board can still count the warning"
    assert not path.exists(), "an oversized history line is discarded before the file is touched"

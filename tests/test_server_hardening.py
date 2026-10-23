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
    assert result.stdout.strip() == "0.2.0"


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

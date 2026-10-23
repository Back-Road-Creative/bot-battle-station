"""Job estimates expose how much finished-run history supports them."""

import importlib.util
from pathlib import Path


BOARD = Path(__file__).resolve().parents[1] / "bin/session-board.py"
spec = importlib.util.spec_from_file_location("session_board_eta_basis", BOARD)
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)


def test_running_jobs_publish_their_finished_run_count(tmp_path):
    tr = board.Transcript(tmp_path / "session.jsonl")
    tr.tasks = {
        "finished": {"kind": "test", "label": "old suite", "start": 700, "end": 800, "status": "completed"},
        "estimated": {"kind": "test", "label": "current suite", "start": 900, "end": None, "status": "running"},
        "first": {"kind": "build", "label": "first build", "start": 910, "end": None, "status": "running"},
    }
    jobs = board.Board(tmp_path, None).jobs(tr, "abc12345", 0, 950)
    assert [(j["label"], j["runs"]) for j in jobs] == [
        ("old suite", 1), ("current suite", 1), ("first build", 0),
    ]

    row = {"tr": tr, "jobs": jobs, "name": "example", "role": "", "sid": "abc12345",
           "status": "shell", "uptime": 250, "eta": "waiting for a job",
           "progress": {"text": "waiting for a job", "left": None, "at": None, "pct": None, "behind": False},
           "plan": None, "tokens": {"today": 0, "cache": 0, "helpers": 0}}
    public = board.row_json(row, 950)
    assert [(j["label"], j["runs"]) for j in public["jobs"]] == [
        ("current suite", 1), ("first build", 0),
    ]
    assert public["jobs"][0]["when"].startswith("~")
    assert public["jobs"][1]["when"].startswith("first run")

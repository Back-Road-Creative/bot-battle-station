"""bin/session-board.py: live session registry + transcripts -> dashboard rows, no model calls."""

import importlib.util
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "bin"
_spec = importlib.util.spec_from_file_location("session_board", BIN / "session-board.py")
sb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sb)

SID = "0a1b2c3d-1111-4222-8333-444455556666"


def iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


def proc_start() -> str:
    return Path("/proc/self/stat").read_text().rsplit(")", 1)[-1].split()[19]


def make_box(tmp_path: Path, records: list[dict], status: str = "busy", started: float | None = None):
    claude = tmp_path / "claude"
    (claude / "sessions").mkdir(parents=True)
    started = started if started is not None else time.time() - 3600
    (claude / "sessions" / f"{os.getpid()}.json").write_text(json.dumps({
        "pid": os.getpid(), "sessionId": SID, "procStart": proc_start(), "name": "project-4a",
        "status": status, "startedAt": int(started * 1000)}))
    proj = claude / "projects" / "-srv-project"
    proj.mkdir(parents=True)
    (proj / f"{SID}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    return claude, proj


def bash_launch(tool_id: str, task_id: str, t: float, desc: str, cmd: str) -> list[dict]:
    return [
        {"type": "assistant", "timestamp": iso(t), "message": {"content": [
            {"type": "tool_use", "id": tool_id, "name": "Bash",
             "input": {"command": cmd, "description": desc, "run_in_background": True}}]}},
        {"type": "user", "timestamp": iso(t + 1), "toolUseResult": {"backgroundTaskId": task_id},
         "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id}]}},
    ]


def notice(task_id: str, t: float, status: str = "completed") -> dict:
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": iso(t),
            "content": f"<task-notification>\n<task-id>{task_id}</task-id>\n<status>{status}</status>"}


def test_background_job_eta_is_median_of_finished_same_kind(tmp_path):
    now = time.time()
    recs = (bash_launch("t1", "b1", now - 1000, "suite one", "pytest tests/a")
            + [notice("b1", now - 700)]                      # finished in 300 s
            + bash_launch("t2", "b2", now - 120, "suite two", "pytest tests/b"))
    claude, _ = make_box(tmp_path, recs)
    [row] = sb.Board(claude, None).snapshot(now)
    running = [j for j in row["jobs"] if not j["done"]]
    assert [j["label"] for j in running] == ["suite two"]
    assert 170 <= running[0]["eta"] <= 182  # 300 s median - ~121 s elapsed
    assert row["eta"] == "~2m · 40%" and row["progress"]["pct"] == 40  # ~121 s of a usual 300 s
    assert sum(j["done"] for j in row["jobs"]) == 1


def test_watcher_has_no_eta_and_session_waits_on_event(tmp_path):
    now = time.time()
    claude, _ = make_box(tmp_path, bash_launch("t1", "b1", now - 60, "watch PR",
                                               "waitfor --pr 5"), status="shell")
    [row] = sb.Board(claude, None).snapshot(now)
    assert row["jobs"][0]["kind"] == "watch" and row["jobs"][0]["eta"] is None
    assert row["eta"] == "waiting for a job"


def test_jobs_launched_before_this_process_are_ignored(tmp_path):
    now = time.time()
    claude, _ = make_box(tmp_path, bash_launch("t1", "b1", now - 7200, "old", "sleep 9"),
                         status="idle", started=now - 60)
    [row] = sb.Board(claude, None).snapshot(now)
    assert row["jobs"] == [] and row["eta"] == "ready"


def test_task_stop_and_summary_title_pr(tmp_path):
    now = time.time()
    recs = bash_launch("t1", "b1", now - 50, "loop", "sleep 99") + [
        {"type": "assistant", "timestamp": iso(now - 10), "message": {"content": [
            {"type": "tool_use", "id": "t9", "name": "TaskStop", "input": {"task_id": "b1"}}]}},
        {"type": "ai-title", "aiTitle": "Live session dashboard"},
        {"type": "pr-link", "prUrl": "https://x/pull/12", "prNumber": 12},
        {"type": "system", "subtype": "away_summary", "content": "Goal: ship the board."},
        {"type": "user", "timestamp": iso(now - 5), "origin": {"kind": "task-notification"},
         "message": {"content": "<task-notification><task-id>b1</task-id><status>killed</status>"}},
    ]
    claude, _ = make_box(tmp_path, recs)
    [row] = sb.Board(claude, None).snapshot(now)
    assert row["jobs"][0]["status"] == "stopped"
    tr = row["tr"]
    assert (tr.title, tr.summary, list(tr.prs.values())) == ("Live session dashboard", "Goal: ship the board.", ["#12"])
    assert tr.now_text.startswith("TaskStop")


def test_subagent_job_uses_history_of_its_type(tmp_path):
    now = time.time()
    claude, proj = make_box(tmp_path, [])
    old = proj / "older-session" / "subagents"
    old.mkdir(parents=True)
    (old / "agent-a1.meta.json").write_text(json.dumps({"agentType": "infra-tdd", "finished": True}))
    (old / "agent-a1.jsonl").write_text(json.dumps({"timestamp": iso(now - 1000)}) + "\n")
    os.utime(old / "agent-a1.jsonl", (now - 400, now - 400))   # ran 600 s
    sub = proj / SID / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-b2.meta.json").write_text(json.dumps(
        {"agentType": "infra-tdd", "description": "Gate fix", "finished": False}))
    (sub / "agent-b2.jsonl").write_text(json.dumps({"timestamp": iso(now - 100)}) + "\n" + json.dumps(
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "bin/x.py"}}]}}) + "\n")
    [row] = sb.Board(claude, None).snapshot(now)
    [job] = row["jobs"]
    assert job["label"] == "Gate fix" and job["now"] == "Edit: bin/x.py"
    assert 495 <= job["eta"] <= 505


def test_roster_name_wins_and_appended_lines_are_read_incrementally(tmp_path):
    now = time.time()
    claude, proj = make_box(tmp_path, [{"type": "ai-title", "aiTitle": "first"}])
    roster = tmp_path / "roster.tsv"
    roster.write_text("0a1b2c3d\tproject-64\tcoordinator\t2026-10-19\n")
    board = sb.Board(claude, roster)
    [row] = board.snapshot(now)
    assert (row["name"], row["role"], row["sid"]) == ("project-64", "coordinator", "0a1b2c3d")
    with (proj / f"{SID}.jsonl").open("a") as fh:
        fh.write(json.dumps({"type": "ai-title", "aiTitle": "second"}) + "\n")
    [row] = board.snapshot(now)
    assert row["tr"].title == "second"


def test_dead_or_reused_pid_is_not_listed(tmp_path):
    claude, _ = make_box(tmp_path, [])
    reg = claude / "sessions" / f"{os.getpid()}.json"
    reg.write_text(json.dumps({**json.loads(reg.read_text()), "procStart": "1"}))
    assert sb.Board(claude, None).snapshot(time.time()) == []


def test_plain_frame_fits_width_and_names_session(tmp_path, capsys):
    now = time.time()
    claude, _ = make_box(tmp_path, bash_launch("t1", "b1", now - 30, "x" * 300, "pytest"))
    assert sb.main(["--once", "--claude-dir", str(claude), "--roster", str(tmp_path / "none"), "--config", str(TEST_CONFIG)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert any("project-4a" in ln and "0a1b2c3d" in ln for ln in out)
    width = max(len(ln) for ln in out)
    assert all(len(ln) <= width for ln in out) and "\033" not in "".join(out)


def test_spends_no_tokens():
    """The board reads files only: no model client, no network, no child process."""
    src = (BIN / "session-board.py").read_text()
    for banned in ("import anthropic", "import subprocess", "import socket", "urllib", "http.client",
                   "claude -p", "os.system", "os.popen"):
        assert banned not in src, banned


PLAN = """# Leftovers plan

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | eval red | **IN-FLIGHT** | x |
| 2 | suites | **NOT STARTED (design known)** | x |
| 3 | lock | **DONE** | x |
| 4 | old PR | **STALE → CLOSE (decided)** | x |
| 5 | R3 | **DONE for harness steps; remainder left out** | x |
| 6 | ci thread | **MERGED** | x |
"""


def test_finished_jobs_do_not_read_as_a_finished_session(tmp_path, capsys):
    """Every background job done while the plan has open rows: the card must say the plan is open."""
    now = time.time()
    plan = tmp_path / "repo" / "plans" / "2026-10-19-leftovers-plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(PLAN)
    recs = (bash_launch("t1", "b1", now - 900, "suite", "pytest") + [notice("b1", now - 600)] + [
        # injected context (git status) names another plan often; only the session's own words count
        *[{"type": "user", "timestamp": iso(now - 950), "message": {"content": " M plans/noise-plan.md"}}] * 5,
        *[{"type": "assistant", "timestamp": iso(now - 940), "message": {"content": [
            {"type": "text", "text": "Reading plans/2026-10-19-leftovers-plan.md"}]}}] * 3,
    ])
    noise = tmp_path / "repo" / "plans" / "noise-plan.md"
    noise.write_text(PLAN.replace("**IN-FLIGHT**", "**DONE**"))
    claude, _ = make_box(tmp_path, recs, status="idle")
    reg = claude / "sessions" / f"{os.getpid()}.json"
    reg.write_text(json.dumps({**json.loads(reg.read_text()), "cwd": str(tmp_path / "repo")}))
    [row] = sb.Board(claude, None).snapshot(now)
    assert row["plan"] == {"name": "2026-10-19-leftovers-plan", "file": "2026-10-19-leftovers-plan.md", "done": 3,
                           "total": 6, "open": ["1 eval red", "2 suites", "4 old PR"], "left": None}
    assert row["eta"] == "3 steps left · 50%"  # open steps: the session is not finished; no pace yet
    sb.main(["--once", "--claude-dir", str(claude), "--roster", str(tmp_path / "none"), "--config", str(TEST_CONFIG)])
    out = capsys.readouterr().out
    assert "3/6" in out and "2026-10-19-leftovers-plan" in out
    assert "1/1 done" not in out and "0 running · 1 finished" in out


def test_undescribed_command_is_named_by_worktree_and_real_work():
    cmd = ("cd /srv/repo/trees/bc-a2 && export GIT_CONFIG_COUNT=1; npm ci > ../a.log 2>&1; "
           "echo npmci=$?; timeout 1500 bash /srv/repo/bin/pytest-slot.sh -- npm test > ../s.log 2>&1")
    assert sb.command_label(cmd) == "bc-a2 · bash bin/pytest-slot.sh -- npm test"
    assert sb.command_label("cd /srv/repo/trees/bc-a8; python3 - <<'EOF'") == "bc-a8 · python3 -"


def finished_runs(now: float, secs: list[int], cmd: str = "pytest tests/a") -> list[dict]:
    """Finished background runs of one kind, an hour apart, ending well before `now`."""
    recs = []
    for i, d in enumerate(secs):
        start = now - 3600 * (len(secs) - i) - d
        recs += bash_launch(f"h{i}", f"hb{i}", start, f"run {i}", cmd) + [notice(f"hb{i}", start + d)]
    return recs


TEST_CONFIG = Path(__file__).with_name("session-board-test.toml")


@pytest.fixture(autouse=True)
def box_config():
    """The suite runs under TEST_CONFIG; no-config tests switch it off."""
    sb.configure(TEST_CONFIG)
    yield
    sb.configure(TEST_CONFIG)


@pytest.fixture(autouse=True)
def no_process_table(monkeypatch):
    """Tests run inside pytest, whose children are not the jobs: /proc says nothing here."""
    monkeypatch.setattr(sb, "proc_children", lambda: None)
    monkeypatch.setattr(sb, "codex_open_files", lambda: None)  # nor which rollouts a Codex process holds


def test_behind_only_past_the_80th_percentile_of_four_runs():
    """Half of healthy runs pass the median, so the median is the estimate, not the alarm line."""
    runs = [200, 250, 300, 400]  # median 275, 80th percentile 340
    assert sb.eta(260, runs) == (15, False)
    assert sb.eta(290, runs) == (50, False)  # past the usual, still in the normal spread
    assert sb.eta(400, runs) == (0, True)
    assert sb.eta(1000, [300, 300, 300]) == (None, False)  # three runs are too few to call it late
    assert sb.eta(100, [300]) == (200, False)


def test_a_job_whose_process_is_gone_is_not_running(tmp_path, monkeypatch):
    """A task killed without a notice would show WAITING ON JOB forever; /proc says it has ended."""
    now = time.time()
    recs = bash_launch("t1", "b1", now - 900, "suite one", "pytest tests/a")
    claude, _ = make_box(tmp_path, recs, status="idle")
    monkeypatch.setattr(sb, "proc_children", lambda: {os.getpid(): []})
    [row] = sb.Board(claude, None).snapshot(now)
    assert [j["status"] for j in row["jobs"]] == ["ended without notice"] and row["jobs"][0]["done"]
    assert sb.row_json(row, now)["state"] == "IDLE"
    monkeypatch.setattr(sb, "proc_children", lambda: {os.getpid(): [now - 899]})  # its shell is alive
    [row] = sb.Board(claude, None).snapshot(now)
    assert sb.row_json(row, now)["state"] == "WAITING ON JOB"


def test_proc_children_reads_this_process_family():
    kids = sb.__dict__["_proc_children"]()  # the real reader, under the autouse stand-in
    if kids is not None:  # Linux: pytest is a child of its parent, started before now
        assert any(abs(t - time.time()) < 86400 * 30 for t in kids.get(os.getppid(), []))


def test_page_defines_every_level_state_and_stat():
    """Every signal on the page says what it means and what you do about it, in one legend."""
    page = (BIN / "session-board.html").read_text()
    for part in ('id="legend"', "What you do", "DEFCON 1", "DEFCON 5", "NEEDS YOU", "WAITING ON JOB",
                 "behind schedule", "helper silent", "context near full", "wait on jobs", "you answer in"):
        assert part in page, part
    assert "no link to the board" not in page  # the old tooltip named a signal that no longer exists
    assert "onJobs.length >= 3" not in page  # waiting on CI is not yours to act on: never red


def test_late_job_says_how_far_behind_its_usual(tmp_path, capsys):
    now = time.time()
    recs = (finished_runs(now, [300, 300, 300, 300])
            + bash_launch("t2", "b2", now - 600, "suite two", "pytest tests/b"))   # typical 300 s, now 600 s
    claude, _ = make_box(tmp_path, recs)
    [row] = sb.Board(claude, None).snapshot(now)
    assert row["eta"] == "5m behind" and row["progress"]["behind"]
    sb.main(["--once", "--claude-dir", str(claude), "--roster", str(tmp_path / "none"), "--config", str(TEST_CONFIG)])
    assert "5m behind (usual 5m)" in capsys.readouterr().out


def test_top_plan_without_status_table_shows_no_plan(tmp_path):
    """A runner-up plan the session mentioned less is not its plan; show nothing rather than it."""
    plans = tmp_path / "repo" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("# no table here\n")
    (plans / "older.md").write_text(PLAN)
    say = lambda name: {"type": "assistant", "timestamp": iso(time.time()), "message": {"content": [  # noqa: E731
        {"type": "text", "text": f"see plans/{name}"}]}}
    claude, _ = make_box(tmp_path, [say("current.md")] * 5 + [say("older.md")] * 3, status="idle")
    reg = claude / "sessions" / f"{os.getpid()}.json"
    reg.write_text(json.dumps({**json.loads(reg.read_text()), "cwd": str(tmp_path / "repo")}))
    [row] = sb.Board(claude, None).snapshot(time.time())
    assert row["plan"] is None and row["eta"] == "ready"


def test_web_page_and_api_serve_on_loopback_only(tmp_path):
    import threading
    import urllib.request
    now = time.time()
    claude, _ = make_box(tmp_path, bash_launch("t1", "b1", now - 30, "watch PR", "waitfor"),
                         status="shell")
    server = sb.make_server(sb.Board(claude, None), 0)
    assert server.server_address[0] == "127.0.0.1"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        data = json.loads(urllib.request.urlopen(base + "/api").read())
        page = urllib.request.urlopen(base + "/").read().decode()
    finally:
        server.shutdown()
    [s] = data["sessions"]
    assert (s["name"], s["sid"], s["state"], s["eta"]) == ("project-4a", "0a1b2c3d", "WAITING ON JOB", "waiting for a job")
    assert s["jobs"] == [{"kind": "watch", "label": "watch PR", "watch": True, "late": False, "stalled": False,
                          "when": "waiting for a job", "frac": None}]
    assert data["waits"] == {"answered": 0, "median": None, "longest": None, "waiting": 0}
    assert data["cores"] == os.cpu_count() and data["alerts"]["by_kind"]["wait on job"]["count"] == 1
    assert len(s["timeline"]) == 60 and set(s["meters"]) == {"working", "shipped", "pace"}
    assert "SESSION" in page and "innerHTML" not in page  # transcript text is rendered with textContent


def plan_box(tmp_path, recs, status="idle"):
    plan = tmp_path / "repo" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(PLAN)
    claude, _ = make_box(tmp_path, recs, status=status)
    reg = claude / "sessions" / f"{os.getpid()}.json"
    reg.write_text(json.dumps({**json.loads(reg.read_text()), "cwd": str(tmp_path / "repo")}))
    return claude


def say(t, text):
    return {"type": "assistant", "timestamp": iso(t), "message": {"content": [{"type": "text", "text": text}]}}


def mark_done(t, step):
    return {"type": "assistant", "timestamp": iso(t), "message": {"content": [{
        "type": "tool_use", "id": f"e{t}", "name": "Edit", "input": {
            "file_path": "/r/plans/p.md", "old_string": f"| {step} | **IN-FLIGHT** |",
            "new_string": f"| {step} | **DONE** |"}}]}}


def test_plan_minutes_left_come_from_the_sessions_own_pace(tmp_path):
    """Named the plan 3000 s ago, marked 2 steps done since: 1000 s a step, 3 steps left = 50 min."""
    now = time.time()
    recs = [say(now - 3000, "Executing plans/p.md")] * 3 + [mark_done(now - 2000, "a"), mark_done(now - 1000, "b")]
    [row] = sb.Board(plan_box(tmp_path, recs), None).snapshot(now)
    assert row["plan"]["left"] == 3000 and row["eta"] == "~50m · 50%"


def test_meters_strip_and_context_are_measured_from_the_transcript(tmp_path):
    now = time.time()
    use = {"type": "assistant", "timestamp": iso(now - 30), "message": {
        "usage": {"input_tokens": 2, "cache_read_input_tokens": 99_998, "cache_creation_input_tokens": 100_000},
        "content": [{"type": "tool_use", "id": f"u{i}", "name": "Read", "input": {}} for i in range(4)]}}
    failed = {"type": "user", "timestamp": iso(now - 29), "message": {"content": [
        {"type": "tool_result", "tool_use_id": "u0", "is_error": True}]}}
    compact = {"type": "system", "subtype": "compact_boundary",
               "compactMetadata": {"trigger": "auto", "preTokens": 400_000}}
    link = {"type": "pr-link", "prUrl": "https://x/pull/7", "prNumber": 7, "timestamp": iso(now - 20)}
    claude, _ = make_box(tmp_path, [compact, use, failed, link], started=now - 600)
    s = sb.row_json(sb.Board(claude, None).snapshot(now)[0], now)
    assert s["context"] == {"used": 200_000, "full": 400_000, "pct": 50, "basis": "own", "compactions": 1}
    assert s["meters"]["shipped"] == 1 and s["prs_today"] == ["https://x/pull/7"]
    strip = s["timeline"]
    assert strip.startswith("-" * 49) and "w" in strip[-2:] and "y" in strip  # 10 min old, active 30 s ago
    assert s["meters"]["working"] == round(100 * strip.count("w") / (60 - strip.count("-")))


BANNED = ("typical", "overdue", "on event", "your turn", "rows open", "rows done")


def test_screen_words_stay_plain(tmp_path, capsys):
    """The page and every worded value stay in plain words: none of the old jargon."""
    page = (BIN / "session-board.html").read_text().lower()
    now = time.time()
    recs = (bash_launch("t1", "b1", now - 2000, "a", "pytest") + [notice("b1", now - 1700)]
            + bash_launch("t2", "b2", now - 600, "b", "pytest") + bash_launch("t3", "b3", now - 60, "w", "waitfor"))
    claude, _ = make_box(tmp_path, recs)
    sb.main(["--once", "--claude-dir", str(claude), "--roster", str(tmp_path / "none"), "--config", str(TEST_CONFIG)])
    text = json.dumps(sb.row_json(sb.Board(claude, None).snapshot(now)[0], now)).lower() + capsys.readouterr().out.lower()
    for word in BANNED:
        assert word not in page and word not in text, word


OTHER_WAIT = "Every Day-1 unit has merged.\n\n1. Tell me when the docs session has wrapped, so I can push the grader commit."
MERGE = "Seven PRs wait on CI.\n\n1. Merge #830 once its last check finishes:\n\ndeploy-now 830 --repo o/r --wait --yes"


def ask_row(tmp_path, reply, status, jobs=(), extra=None):
    now = time.time()
    claude, _ = make_box(tmp_path, [*jobs, say(now - 5, reply)], status=status)
    if extra:
        reg = claude / "sessions" / f"{os.getpid()}.json"
        reg.write_text(json.dumps({**json.loads(reg.read_text()), **extra}))
    return sb.row_json(sb.Board(claude, None).snapshot(now)[0], now)


def test_needs_you_only_on_a_deterministic_block(tmp_path):
    """Red only when Claude Code reports a prompt open, or the reply holds a command for you to run.

    A numbered list or a question in a reply is wording, not a block: it shows as the calm "next" line.
    """
    watch = bash_launch("t1", "b1", time.time() - 60, "watch PR", "waitfor --pr 5")
    cases = [
        (OTHER_WAIT, "idle", (), None, "IDLE", ""),
        (MERGE, "shell", watch, None, "NEEDS YOU", "deploy-now 830 --repo o/r --wait --yes"),
        ("Not live yet.\n\nNothing for you to do yet.", "shell", watch, None, "WAITING ON JOB", ""),
        ("Done.\n\n1. Nothing needed from you now.", "idle", (), None, "IDLE", ""),
        ("Built.\n\n1. Refresh http://localhost:8765 and say if the look is wrong.", "idle", (), None, "IDLE", ""),
        ("Pushed.\n\n1. Nothing to run. #5320 lands after #5319.", "idle", (), None, "IDLE", ""),
        ("Should I split it?", "idle", (), None, "IDLE", ""),
        ("Working on it.", "waiting", (), {"waitingFor": "approve Bash"}, "NEEDS YOU", "approve Bash"),
        (MERGE, "busy", (), None, "RUNNING", ""),
    ]
    for i, (reply, status, jobs, extra, state, asks) in enumerate(cases):
        s = ask_row(tmp_path / str(i), reply, status, jobs, extra)
        assert (s["state"], s["ask"][:len(asks)] if asks else s["ask"]) == (state, asks), (i, s["state"], s["ask"])
    s = ask_row(tmp_path / "other", OTHER_WAIT, "idle")
    assert s["waits_on"].startswith("Tell me when the docs session has wrapped")  # a note, never a state
    s = ask_row(tmp_path / "next", "Built.\n\n1. Refresh the page and say if the look is wrong.", "idle")
    assert s["for_you"] == "Refresh the page and say if the look is wrong." and s["warn"] == []


def test_topic_and_next_come_from_the_sessions_own_summary(tmp_path):
    topics = {
        "We're getting the docs site search backlog merged; seven PRs are waiting.":
            "Getting the docs site search backlog merged",
        "You asked me to finish the harness and CI leftovers plan, and every Day-1 fix merged.":
            "Finish the harness and CI leftovers plan",
        "Finishing the parser plan: five fixes passed.": "Finishing the parser plan",
        "You asked for a sharper session dashboard, and it now shows % done.": "A sharper session dashboard",
        "We're getting #829 live on example.com. It's merged.": "Getting #829 live on example.com",
    }
    for summary, want in topics.items():
        assert sb.topic(summary) == want
    away = {"type": "system", "subtype": "away_summary", "timestamp": iso(time.time() - 120),
            "content": "We're clearing the search backlog; two PRs wait. Next, run `make x` when CI is green."}
    claude, _ = make_box(tmp_path, [{"type": "ai-title", "aiTitle": "Old opening title"}, away], status="idle")
    s = sb.row_json(sb.Board(claude, None).snapshot(time.time())[0], time.time())
    assert (s["topic"], s["next"], s["started_as"]) == ("Clearing the search backlog", "Run make x when CI is green",
                                                         "Old opening title")


def test_plan_heading_names_the_topic_when_there_is_no_summary(tmp_path):
    plans = tmp_path / "repo" / "plans"
    plans.mkdir(parents=True)
    (plans / "search.md").write_text("# Docs site: search backlog — plan (2026-10-19)\n\nno table\n")
    claude, _ = make_box(tmp_path, [say(time.time(), "see plans/search.md")] * 3, status="busy")
    reg = claude / "sessions" / f"{os.getpid()}.json"
    reg.write_text(json.dumps({**json.loads(reg.read_text()), "cwd": str(tmp_path / "repo")}))
    s = sb.row_json(sb.Board(claude, None).snapshot(time.time())[0], time.time())
    assert s["plan"] is None and s["plan_title"] == "Docs site: search backlog"
    assert s["topic"] == "Docs site: search backlog"


def test_tokens_today_count_each_reply_once_and_include_helpers(tmp_path):
    now = time.time()
    use = {"input_tokens": 10, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 50_000,
           "output_tokens": 200}
    rec = lambda t, mid: {"type": "assistant", "timestamp": iso(t), "message": {  # noqa: E731
        "id": mid, "usage": use, "content": [{"type": "text", "text": "x"}]}}
    yesterday = now - 86_400 - 3600
    claude, proj = make_box(tmp_path, [rec(yesterday, "m0"), rec(now - 60, "m1"), rec(now - 59, "m1"),
                                       rec(now - 30, "m2")], started=yesterday)
    sub = proj / SID / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a.meta.json").write_text(json.dumps({"agentType": "x", "finished": True}))
    (sub / "agent-a.jsonl").write_text(json.dumps(rec(now - 20, "h1")) + "\n")
    s = sb.row_json(sb.Board(claude, None).snapshot(now)[0], now)
    assert s["tokens"] == {"today": 3 * 1210, "cache": 3 * 50_000, "helpers": 1210}


def test_a_server_is_running_not_late_and_does_not_hold_the_session(tmp_path):
    now = time.time()
    recs = (bash_launch("t1", "b1", now - 2000, "quick", "python3 x.py") + [notice("b1", now - 1990)]
            + bash_launch("t2", "b2", now - 1500, "board", "exec python3 bin/session-board.py --port 8765"))
    s = ask_row(tmp_path, "Live.\n\nNothing for you now.", "idle", recs)
    assert s["state"] == "IDLE" and s["eta"] == "ready"
    assert s["jobs"] == [{"kind": "serve", "label": "board", "watch": True, "late": False, "stalled": False, "when": "running",
                          "frac": None}]


def test_working_counts_reply_time_not_time_watching_a_job(tmp_path):
    """A 10-minute reply with no records in between is working; a watcher running alone is not."""
    now = time.time()
    end = now - 60
    recs = bash_launch("t1", "b1", now - 1800, "watch", "waitfor --pr 1") + [
        {"type": "user", "timestamp": iso(end - 600), "message": {"content": "go"}},
        {"type": "system", "subtype": "turn_duration", "durationMs": 600_000, "timestamp": iso(end)}]
    claude, _ = make_box(tmp_path, recs, status="shell", started=now - 1800)
    s = sb.row_json(sb.Board(claude, None).snapshot(now)[0], now)
    strip = s["timeline"]
    assert strip[-12:-1].count("w") >= 10 and strip[-25:-15] == "j" * 10
    assert s["meters"]["working"] == round(100 * strip.count("w") / (60 - strip.count("-")))


def test_context_size_falls_back_to_what_this_box_measured(tmp_path):
    now = time.time()
    use = {"type": "assistant", "timestamp": iso(now - 30), "message": {"id": "m", "usage": {
        "input_tokens": 0, "cache_read_input_tokens": 50_000, "cache_creation_input_tokens": 0}, "content": []}}
    claude, proj = make_box(tmp_path, [use])
    other = "11111111-2222-3333-4444-555555555555"
    (proj / f"{other}.jsonl").write_text(json.dumps({"type": "system", "subtype": "compact_boundary",
                                                     "compactMetadata": {"trigger": "auto", "preTokens": 200_000}}) + "\n")
    reg = json.loads((claude / "sessions" / f"{os.getpid()}.json").read_text())
    (claude / "sessions" / "other.json").write_text(json.dumps({**reg, "sessionId": other, "startedAt": 1}))
    rows = {r["sid"]: sb.row_json(r, now) for r in sb.Board(claude, None).snapshot(now)}
    assert rows["0a1b2c3d"]["context"] == {"used": 50_000, "full": 200_000, "pct": 25, "basis": "box",
                                           "compactions": 0}


def test_page_shows_real_tokens_not_a_zero_badge():
    page = (BIN / "session-board.html").read_text()
    assert "0 TOKENS" not in page and "tokens today" in page


def test_needs_you_is_an_alarm_not_a_tint(tmp_path):
    """A session waiting on you is red and pulsing on the page, in the tab title and in the terminal."""
    page = (BIN / "session-board.html").read_text()
    assert ".NEEDS { --c: var(--red)" in page and "@keyframes alarm" in page
    assert "document.title" in page and "prefers-reduced-motion" in page
    claude, _ = make_box(tmp_path, [say(time.time(), "Ready.\n\n" + MERGE.splitlines()[-1])], status="idle")
    [row] = sb.Board(claude, None).snapshot(time.time())
    assert sb.status_of(row) == ("NEEDS YOU", sb.RED_T)


def test_needs_you_takes_over_the_screen_with_an_opt_in_siren():
    """Seen across the room: a full-screen alert until acknowledged, a siren and a desktop notice once armed."""
    page = (BIN / "session-board.html").read_text()
    for part in ('id="alert"', "ACKNOWLEDGE", "AudioContext", "Notification", "DEFCON", 'id="arm"'):
        assert part in page, part
    assert 'store.get("siren") === "on"' in page  # off until you arm it: browsers block sound before a click anyway


def test_defcon_ranks_the_box_by_what_needs_jp():
    """1 someone needs you, 2 a job is behind schedule, 3 waiting on jobs or sessions, 4 working, 5 all quiet."""
    def r(state, late=False):
        return {"state": state, "jobs": [{"late": late}] if late else []}
    assert sb.defcon([]) == (5, "all quiet")
    assert sb.defcon([r("IDLE"), r("RUNNING")])[0] == 4
    assert sb.defcon([r("IDLE"), r("WAITING ON JOB")])[0] == 3
    assert sb.defcon([r("WAITING ON JOB", late=True), r("IDLE")]) == (2, "1 job behind schedule")
    assert sb.defcon([r("NEEDS YOU"), r("NEEDS YOU"), r("WAITING ON JOB", late=True)]) == (1, "2 sessions need you")


def test_defcon_two_covers_silent_helpers_and_full_context():
    """A helper silent 20 min and a session about to compact are worth a look, not a siren."""
    def r(state="RUNNING", late=False, stalled=False, pct=10):
        return {"state": state, "jobs": [{"late": late or stalled, "stalled": stalled}], "context": {"pct": pct}}
    assert sb.defcon([r(stalled=True)]) == (2, "1 helper silent 20m+")
    assert sb.defcon([r(pct=93)]) == (2, "1 session near its context limit")
    assert sb.defcon([r(late=True), r(pct=90)]) == (2, "1 job behind schedule · 1 session near its context limit")
    assert sb.defcon([r(pct=89)])[0] == 4


def test_wait_log_records_how_long_each_ask_waited_for_jp(tmp_path):
    """Each NEEDS YOU spell is logged from the ask's own time to when it stopped asking, and survives a restart."""
    path, now = tmp_path / "waits.jsonl", time.time()
    log = sb.WaitLog(path)
    log.update([{"sid": "a", "name": "ws-a", "state": "NEEDS YOU", "ask": "Approve", "waited": 60}], now)
    log.update([{"sid": "a", "name": "ws-a", "state": "NEEDS YOU", "ask": "Approve", "waited": 62}], now + 2)
    assert log.stats(now) == {"answered": 0, "median": None, "longest": None, "waiting": 1}
    log.update([{"sid": "a", "name": "ws-a", "state": "RUNNING", "ask": "", "waited": None}], now + 240)
    [rec] = [json.loads(line) for line in path.read_text().splitlines()]
    assert (rec["sid"], rec["name"], rec["ask"], round(rec["secs"])) == ("a", "ws-a", "Approve", 300)
    assert sb.WaitLog(path).stats(now + 240) == {"answered": 1, "median": 300, "longest": 300, "waiting": 0}
    page = (BIN / "session-board.html").read_text()
    assert "data.waits" in page


def test_kiosk_launcher_starts_the_board_and_opens_a_full_screen_window():
    """One Windows script: starts the board in WSL if it is not answering, opens a full-screen app window
    on the spare monitor with sound allowed, and with -Install runs itself at every Windows login."""
    ps1 = (BIN / "session-board-kiosk.ps1").read_text()
    for part in ("[switch]$Install", "GetFolderPath('Startup')", "wsl.exe", "bin/session-board.py",
                 "--app=", "--start-fullscreen", "--autoplay-policy=no-user-gesture-required", "AllScreens"):
        assert part in ps1, part


def test_watcher_gets_the_usual_ci_wait_but_is_never_behind(tmp_path):
    now = time.time()
    recs = (bash_launch("t1", "b1", now - 2000, "watch one", "waitfor --pr 1") + [notice("b1", now - 1700)]
            + bash_launch("t2", "b2", now - 1600, "watch two", "waitfor --pr 2") + [notice("b2", now - 1300)]
            + bash_launch("t3", "b3", now - 60, "watch three", "waitfor --pr 3"))
    claude, _ = make_box(tmp_path, recs, status="shell")
    board = sb.Board(claude, None)
    [row] = board.snapshot(now)
    [job] = [j for j in row["jobs"] if not j["done"]]
    assert 235 <= job["eta"] <= 241 and not job["late"]  # usual 300 s wait - ~61 s
    assert row["eta"] == "~3m · 20%" and now + 235 <= row["progress"]["at"] <= now + 241
    [row] = board.snapshot(now + 600)  # past the usual: no guess, and still not behind
    [job] = [j for j in row["jobs"] if not j["done"]]
    assert job["eta"] is None and not job["late"] and row["eta"] == "waiting for a job"


def test_sessions_line_up_in_order_of_completion():
    """NEEDS YOU first (longest ask first), then soonest done, then no estimate (longest wait first), idle last (the last one finished at the bottom)."""
    def r(name, state, left=None, behind=False, waited=0, started=0):
        return {"name": name, "state": state, "waited": waited, "started": started,
                "progress": {"left": left, "behind": behind}}
    rows = [r("idle-new", "IDLE", waited=20, started=5), r("idle-old", "IDLE", waited=900, started=9), r("slow", "RUNNING", left=900), r("ci-new", "WAITING ON JOB", waited=60),
            r("ask-new", "NEEDS YOU", waited=30), r("late", "RUNNING", behind=True), r("fast", "WAITING ON JOB", left=120),
            r("ci-old", "WAITING ON JOB", waited=3000), r("ask-old", "NEEDS YOU", waited=600)]
    assert [x["name"] for x in sb.finish_order(rows)] == [
        "ask-old", "ask-new", "late", "fast", "slow", "ci-old", "ci-new", "idle-old", "idle-new"]
    page = (Path(sb.__file__).with_name("session-board.html")).read_text(encoding="utf-8")
    assert "data.sessions.sort(" not in page  # the page keeps the server's order, so 1-9 match the tiles


def test_warnings_are_yellow_measured_and_silent(tmp_path):
    """Yellow is only what the board measures: a job past its usual, a silent helper, context near full."""
    now = time.time()
    recs = finished_runs(now, [100, 100, 100, 100]) + bash_launch("t2", "b2", now - 500, "suite two", "pytest tests/b")
    s = ask_row(tmp_path, "Running the suite.", "shell", recs)
    assert s["state"] == "WAITING ON JOB" and s["warn"] == ["job behind schedule"]
    page = (BIN / "session-board.html").read_text()
    assert ".tile.warn" in page and "s.warn" in page


def test_header_numbers_say_what_they_measure():
    """No tool-error meter (a red test is TDD, not a fault), PRs touched not shipped, load per core, a finish clock."""
    page = (BIN / "session-board.html").read_text()
    for gone in ('"CLEAN"', '"PRs today"', '"left", "The longest'):
        assert gone not in page, gone
    assert "WAITING ON SESSION" not in page
    for part in ('"PRs touched"', "data.cores", '"all done by"', "s.waits_on"):
        assert part in page, part


def test_alert_log_records_each_warning_spell_and_what_followed(tmp_path):
    """One line per warning spell; the day's stats say how often each kind cleared alone or led to NEEDS YOU."""
    path, now = tmp_path / "alerts.jsonl", datetime.now().replace(hour=12, minute=0).timestamp()
    late = {"label": "pytest tests/a", "late": True, "stalled": False}
    silent = {"label": "review", "type": "Explore", "late": True, "stalled": True}

    def row(sid, state="WAITING ON JOB", warn=(), jobs=(), compactions=0):
        return {"sid": sid, "name": "ws-" + sid, "state": state, "warn": list(warn), "jobs": list(jobs),
                "context": {"compactions": compactions}}
    log = sb.AlertLog(path)
    log.update([row("a", warn=["job behind schedule"], jobs=[late]), row("b", "RUNNING", ["helper silent 20m+"], [silent])], now)
    log.update([row("a", "IDLE"), row("b", "RUNNING", ["helper silent 20m+"], [silent])], now + 120)
    log.update([row("a", "IDLE"), row("b", "NEEDS YOU")], now + 300)
    recs = [json.loads(line) for line in path.read_text().splitlines()]
    assert {(r["sid"], r["kind"], round(r["secs"])) for r in recs} == {
        ("a", "job behind schedule", 120), ("a", "wait on job", 120), ("b", "helper silent 20m+", 300)}
    waits = [{"sid": "b", "start": now + 300, "end": now + 420, "secs": 120}]
    st = sb.AlertLog(path).stats(now + 600, waits, [row("a", "IDLE", compactions=2)])
    assert st["by_kind"]["job behind schedule"] == {"count": 1, "median": 120, "to_you": 0}
    assert st["by_kind"]["helper silent 20m+"] == {"count": 1, "median": 300, "to_you": 100}
    assert st["behind_by_job"] == {"pytest tests/a": 1} and st["silent_by_type"] == {"Explore": 1}
    assert st["job_wait_by_hour"] == {"12": 2} and st["asks_by_hour"] == {"12": {"count": 1, "median": 120}}
    assert st["compactions"] == {"ws-a": 2}
    page = (BIN / "session-board.html").read_text()
    assert 'id="today"' in page and "data.alerts" in page


def test_a_page_bug_is_not_reported_as_offline():
    """OFFLINE means /api did not answer; a render error says so instead of blaming the link (2026-10-19:
    a shadowed today() threw in header() and the board read OFFLINE while the server answered in 0.2 s)."""
    page = (BIN / "session-board.html").read_text()
    assert "function dayStats()" in page and "function today()" not in page
    fetch_try = page.split("async function tick()")[1].split("\n}\n")[0]
    assert "render();" not in fetch_try.split("catch")[0].split("try {")[1], "render() inside the fetch try"
    assert "PAGE ERROR" in page


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node not on PATH: the page render check (#5325) runs the page in node")
def test_page_renders_a_real_api_snapshot(tmp_path):
    """Run the page's own render() on /api output in node: a runtime error (a shadowed name, a missing field)
    would show the board as broken while every Python test passed."""
    import subprocess
    import threading
    import urllib.request
    now = time.time()
    recs = finished_runs(now, [100] * 4) + bash_launch("t2", "b2", now - 500, "suite two", "pytest tests/b")
    claude, _ = make_box(tmp_path, recs, status="shell")
    server = sb.make_server(sb.Board(claude, None), 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        api = urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/api").read()
    finally:
        server.shutdown()
    (tmp_path / "api.json").write_bytes(api)

    def run(*mode):
        return subprocess.run(["node", str(Path(__file__).with_name("session_board_render.js")), str(BIN / "session-board.html"),
                               str(tmp_path / "api.json"), *mode], capture_output=True, text=True, timeout=60)
    out = run()
    assert out.stdout.strip() == "render ok", out.stdout + out.stderr
    # OFFLINE is a loud banner, but only after 15 s of failed polls in a row: one dropped poll is not an outage.
    lines = run("offline").stdout.splitlines()
    on = [int(line.split()[0][2:]) for line in lines if line.startswith("t=") and line.endswith(" on")]
    assert on and on[0] == 16 and on == list(range(16, 21, 2)), lines  # 15 s of failures, then it stays up
    assert lines[-1] == "back off", lines  # one good answer clears it
    page = (BIN / "session-board.html").read_text()
    assert 'id="link"' in page and "AbortController" in page and "OFFLINE_AFTER" in page


def test_with_no_config_only_claude_codes_own_state_is_red(tmp_path):
    """The public copy has no config: no command wording alarms, no box watcher names, no plan folder."""
    cfg = sb.configure(None)
    assert sb.ask("Run this:\n\n    sudo deploy-now 5 --wait --yes")[0] != "you"
    assert sb.job_kind("waitfor --pr 5", "Bash") != "watch"
    assert sb.job_kind("anything", "Monitor") == "watch"  # Claude Code's own watcher tool needs no config
    assert cfg["roster"] is None and cfg["plan_dirs"] == []
    now = time.time()
    claude = plan_box(tmp_path, [say(now - 60, "Executing plans/p.md")] * 3)
    [row] = sb.Board(claude, None).snapshot(now)
    assert row["plan"] is None


def test_config_names_this_boxs_asks_watchers_plans_and_files(tmp_path):
    conf = tmp_path / "board.toml"
    conf.write_text("ask_commands = ['deploy-now\\b.*']\nwatch_commands = ['waitfor']\n"
                    "plan_dirs = ['notes']\nroster = 'roster.tsv'\nstate_dir = 'state'\n")
    cfg = sb.configure(conf)
    assert sb.ask("1. Run it\n\n    deploy-now site") == ("you", "deploy-now site")
    assert sb.job_kind("waitfor build", "Bash") == "watch"
    assert cfg["roster"] == tmp_path / "roster.tsv" and cfg["state_dir"] == tmp_path / "state"
    plan = tmp_path / "repo" / "notes" / "todo.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# Todo\n\n- [x] one\n- [ ] two\n- [X] three\n- [ ] four\n")
    now = time.time()
    claude, _ = make_box(tmp_path, [say(now - 60, "Working through notes/todo.md")] * 3, status="idle")
    reg = claude / "sessions" / f"{os.getpid()}.json"
    reg.write_text(json.dumps({**json.loads(reg.read_text()), "cwd": str(tmp_path / "repo")}))
    [row] = sb.Board(claude, None).snapshot(now)
    assert (row["plan"]["done"], row["plan"]["total"], row["plan"]["open"]) == (2, 4, ["two", "four"])
    assert sb.done_cells("- [ ] a\n- [x] b") == 1  # ticking a box marks a step done, as a DONE cell does


def test_defaults_follow_claude_config_dir_and_xdg_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert sb.default_claude_dir() == tmp_path / "cc"
    assert sb.configure(None)["state_dir"] == tmp_path / "xdg" / "bot-battle-station"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert sb.default_claude_dir() == Path.home() / ".claude"


def test_api_answers_only_a_loopback_host_and_carries_its_version(tmp_path):
    """DNS rebinding: a page on evil.example whose name flips to 127.0.0.1 must not read /api."""
    import http.client
    import threading
    claude, _ = make_box(tmp_path, [], status="idle")
    server = sb.make_server(sb.Board(claude, None), 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def get(path, host):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        conn.request("GET", path, headers={"Host": host})
        resp = conn.getresponse()
        return resp.status, resp.read()
    try:
        for host in (f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}", "localhost"):
            assert get("/api", host)[0] == 200, host
        assert json.loads(get("/api", "localhost")[1])["api"] == 1
        for host in ("evil.example", f"evil.example:{port}", f"localhost.evil.example:{port}"):
            assert get("/api", host)[0] == 403, host
            assert get("/", host)[0] == 403, host
    finally:
        server.shutdown()


def test_demo_box_shows_every_state_from_made_up_sessions(tmp_path):
    """--demo: a synthetic box for screenshots and first runs; nothing in it comes from this machine."""
    sb.configure(None)
    claude = sb.demo_box(tmp_path)
    now = time.time()
    board = sb.Board(claude, None)
    rows = [sb.row_json(r, now) for r in board.snapshot(now)]
    assert {r["state"] for r in rows} == {"NEEDS YOU", "RUNNING", "WAITING ON JOB", "IDLE"}
    assert any(j["late"] for r in rows for j in r["jobs"])          # a job behind its usual: DEFCON 2 on its own
    assert any(r["context"]["pct"] >= 90 for r in rows)
    assert sb.defcon(rows)[0] == 1
    assert sb.defcon([r for r in rows if r["state"] != "NEEDS YOU"])[0] == 2
    text = json.dumps(rows)
    assert str(Path.home()) not in text and os.environ.get("USER", "nobody") not in text


def test_demo_shows_a_made_up_load_and_a_page_can_open_acknowledged(monkeypatch, capsys):
    """A demo screenshot shows a made-up load, and ?ack opens the grid under the takeover."""
    monkeypatch.setattr(sb, "loadavg", sb.loadavg)
    monkeypatch.setattr(sb, "proc_children", sb.proc_children)
    monkeypatch.setattr(sb.shutil, "get_terminal_size", lambda *a, **k: os.terminal_size((160, 40)))
    assert sb.main(["--demo", "--once"]) == 0
    assert sb.loadavg() == sb.DEMO_LOAD and sb.DEMO_LOAD in capsys.readouterr().out
    page = (BIN / "session-board.html").read_text()
    assert 'new URLSearchParams(location.search).has("ack")' in page


def test_doctor_counts_what_it_reads_and_shows_no_values(tmp_path):
    """--doctor: which inputs the board found and which keys they carry, for a bug report; no paths, no text."""
    now = time.time()
    claude, _ = make_box(tmp_path, bash_launch("t1", "b1", now - 60, "secret words", "pytest x")
                         + [notice("b1", now - 30)])
    report = "\n".join(sb.doctor(claude))
    for part in ("registry files: 1", "live sessions: 1", "transcripts: 1", "task notifications: 1",
                 "sessionId", "procStart"):
        assert part in report, part
    assert "version: missing" in report
    for leak in (str(tmp_path), SID, "secret words", "project-4a"):
        assert leak not in report, leak


def test_a_claude_code_newer_than_the_tested_range_is_named(tmp_path):
    claude, _ = make_box(tmp_path, [], status="idle")
    reg = claude / "sessions" / f"{os.getpid()}.json"
    reg.write_text(json.dumps({**json.loads(reg.read_text()), "version": "2.3.0"}))
    board = sb.Board(claude, None)
    board.snapshot(time.time())
    assert sb.untested_versions(board.versions) == ["2.3.0"]
    assert sb.untested_versions({"2.1.303", "1.0.9"}) == []
    page = (BIN / "session-board.html").read_text()
    assert 'id="untested"' in page and "data.untested" in page


def test_without_proc_a_session_is_live_while_its_pid_is(tmp_path, monkeypatch):
    """macOS and the like have no /proc: liveness falls back to signal 0, which cannot see pid reuse."""
    import subprocess
    monkeypatch.setattr(sb, "PROC", tmp_path / "no-proc")
    claude, _ = make_box(tmp_path, [], status="idle")
    assert len(sb.Board(claude, None).snapshot(time.time())) == 1
    gone = subprocess.Popen(["true"])
    gone.wait()
    reg = claude / "sessions" / f"{os.getpid()}.json"
    reg.write_text(json.dumps({**json.loads(reg.read_text()), "pid": gone.pid}))
    assert sb.Board(claude, None).snapshot(time.time()) == []


def test_a_page_that_hangs_up_mid_answer_is_not_a_traceback(tmp_path, capsys):
    """The page drops a slow /api after 5 s; the server's write then fails, which is nobody's error."""
    claude, _ = make_box(tmp_path, [], status="idle")
    server = sb.make_server(sb.Board(claude, None), 0)
    try:
        for exc in (BrokenPipeError, ConnectionResetError):
            try:
                raise exc
            except exc:
                server.handle_error(None, ("127.0.0.1", 1))
        assert capsys.readouterr().err == ""
    finally:
        server.server_close()


def test_kiosk_names_no_user_or_checkout_of_its_own():
    """Shared as-is: the WSL user defaults to the distro's own and the command and checkout are parameters."""
    ps1 = (BIN / "session-board-kiosk.ps1").read_text()
    assert '[string]$User = ""' in ps1 and "/home/" not in ps1
    assert '[string]$Command = "python3 bin/session-board.py"' in ps1
    flags = next(line for line in ps1.splitlines() if line.strip().startswith("$flags ="))
    assert "-Command" in flags and "-Repo" in flags


def test_a_config_beside_the_script_is_the_default_and_loads():
    """A box's own session-board.toml (the public copy ships none) loads with asks and a roster; none means neutral."""
    conf = BIN / "session-board.toml"
    cfg, have = sb.configure(conf), conf.is_file()
    assert (sb.CMD_RE is not sb.NEVER, bool(cfg["roster"]), bool(sb.WATCH_COMMANDS)) == (have, have, have)


CODEX_OPEN_FILES = sb.codex_open_files  # the autouse fixture stubs it; the held-open test needs the real one
CODEX_ID = "0c0dec00-aaaa-7bbb-8ccc-ddddeeee0001"


def codex_rec(t: float, kind: str, **payload) -> dict:
    top = "session_meta" if kind == "session_meta" else "event_msg"
    return {"timestamp": iso(t), "type": top, "payload": payload if top == "session_meta" else {"type": kind, **payload}}


def codex_limits(used: float, resets: int) -> dict:
    return {"limit_id": "codex", "primary": {"used_percent": used, "window_minutes": 10080, "resets_at": resets},
            "secondary": {"used_percent": 12.0, "window_minutes": 300, "resets_at": resets - 3600},
            "plan_type": "demo"}


def make_codex(tmp_path: Path, now: float, sid: str = CODEX_ID, parent: str | None = None,
               last: str = "task_started", name: str = "rollout") -> Path:
    codex = tmp_path / "codex"
    day = codex / "sessions" / "2026" / "10" / "20"
    day.mkdir(parents=True, exist_ok=True)
    recs = [codex_rec(now - 600, "session_meta", id=sid, cwd="/srv/project-9", originator="codex_cli",
                      cli_version="0.1.0", parent_thread_id=parent),
            codex_rec(now - 590, "user_message", message="tidy the parser"),
            codex_rec(now - 580, "task_started"),
            codex_rec(now - 60, "agent_message", message="Splitting the parser into two passes."),
            codex_rec(now - 50, "token_count", info={"last_token_usage": {"input_tokens": 129200},
                                                     "model_context_window": 258400},
                      rate_limits=codex_limits(59.0, int(now) + 86400))]
    if last == "task_complete":
        recs.append(codex_rec(now - 40, "task_complete"))
    path = day / f"{name}-{sid}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))
    with (codex / "session_index.jsonl").open("a") as fh:
        fh.write(json.dumps({"id": sid, "thread_name": "Tidy the parser"}) + "\n")
    return codex


def test_codex_session_its_process_holds_open_is_a_row_with_its_bot(tmp_path, monkeypatch):
    """A Codex session is live while a process holds its rollout open; a sub-thread or a closed file is not a row."""
    now = time.time()
    codex = make_codex(tmp_path, now)
    make_codex(tmp_path, now, sid="0c0dec00-aaaa-7bbb-8ccc-ddddeeee0002", parent=CODEX_ID, name="guard")
    make_codex(tmp_path, now, sid="0c0dec00-aaaa-7bbb-8ccc-ddddeeee0003", name="closed")
    monkeypatch.setattr(sb, "codex_open_files", CODEX_OPEN_FILES)
    monkeypatch.setattr(sb, "CODEX_PROC_RE", __import__("re").compile(rb"."))  # this test's process holds them
    held = [open(p) for p in sorted(codex.glob("sessions/*/*/*/*.jsonl")) if "closed" not in p.name]
    try:
        [row] = sb.Board(tmp_path / "claude", None, codex_dir=codex).codex_rows(now)
    finally:
        for fh in held:
            fh.close()
    assert (row["bot"], row["sid"], row["state"], row["title"]) == ("codex", "0c0dec00", "RUNNING", "Tidy the parser")
    assert row["name"] == "project-9" and row["now"] == "Splitting the parser into two passes."
    assert row["context"]["pct"] == 50 and row["jobs"] == [] and row["warn"] == []


def test_codex_without_a_process_table_counts_recent_rollouts_and_reads_idle(tmp_path, monkeypatch):
    now = time.time()
    codex = make_codex(tmp_path, now, last="task_complete")
    [row] = sb.Board(tmp_path / "claude", None, codex_dir=codex).codex_rows(now)
    assert row["state"] == "IDLE"
    old = next(codex.glob("sessions/*/*/*/*.jsonl"))
    os.utime(old, (now - 3600, now - 3600))
    assert sb.Board(tmp_path / "claude", None, codex_dir=codex).codex_rows(now) == []


def test_bot_stats_count_each_bot_and_read_both_limits(tmp_path, monkeypatch):
    """Codex limits come from its newest rollout; Claude's from the file a status line writes, else none."""
    now = time.time()
    claude, _ = make_box(tmp_path, [])
    codex = make_codex(tmp_path, now)
    limits = tmp_path / "claude-limits.json"
    board = sb.Board(claude, None, codex_dir=codex, limits_file=limits)
    stats = board.bots(board.codex_rows(now) + [{"bot": "claude"}], now)
    assert stats["claude"] == {"sessions": 1, "limits": [], "age": None}
    assert stats["codex"]["sessions"] == 1 and stats["codex"]["plan"] == "demo"
    assert stats["codex"]["limits"] == [{"name": "weekly", "used": 59, "resets": int(now) + 86400},
                                        {"name": "5h", "used": 12, "resets": int(now) + 82800}]
    limits.write_text(json.dumps({"five_hour": {"used_percentage": 41.6, "resets_at": int(now) + 900},
                                  "seven_day": {"used_percentage": 73.2, "resets_at": int(now) + 7200}}))
    stats = board.bots([], now)
    assert stats["claude"]["limits"] == [{"name": "5h", "used": 42, "resets": int(now) + 900},
                                         {"name": "weekly", "used": 73, "resets": int(now) + 7200}]
    assert stats["claude"]["age"] is not None and stats["claude"]["sessions"] == 0


def test_limits_keep_only_valid_current_windows(tmp_path):
    """A limits value the page cannot trust is dropped, never shown and never a crash: a non-number, NaN or bool
    percentage, a window whose reset has passed, a file that is not an object; a percentage over 100 is shown as 100."""
    now = time.time()
    limits = tmp_path / "claude-limits.json"
    board = sb.Board(tmp_path / "claude", None, limits_file=limits)
    limits.write_text(json.dumps({"five_hour": {"used_percentage": "lots", "resets_at": int(now) + 900},
                                  "seven_day": {"used_percentage": 140, "resets_at": "soon"}}))
    assert board.bots([], now)["claude"]["limits"] == [{"name": "weekly", "used": 100, "resets": None}]
    limits.write_text(json.dumps({"five_hour": {"used_percentage": float("nan"), "resets_at": int(now) + 900},
                                  "seven_day": {"used_percentage": True, "resets_at": int(now) + 900}}))
    assert board.bots([], now)["claude"]["limits"] == []
    limits.write_text(json.dumps({"five_hour": {"used_percentage": 88, "resets_at": int(now) - 60},
                                  "seven_day": {"used_percentage": -5, "resets_at": int(now) + 900}}))
    assert board.bots([], now)["claude"]["limits"] == [{"name": "weekly", "used": 0, "resets": int(now) + 900}]
    limits.write_text("[1, 2]")
    assert board.bots([], now)["claude"]["limits"] == []
    bad = {"primary": {"used_percent": "x", "window_minutes": 300, "resets_at": int(now) + 60},
           "secondary": {"used_percent": 30, "window_minutes": "week", "resets_at": "soon"}}
    assert sb.codex_limits(bad, now) == [{"name": "?", "used": 30, "resets": None}]
    assert sb.codex_limits({"primary": {"used_percent": 30, "window_minutes": 300, "resets_at": int(now) - 1}}, now) == []
    assert sb.codex_limits("not an object", now) == []


def test_api_tags_every_session_with_its_bot_and_carries_bot_stats(tmp_path, monkeypatch):
    import threading
    import urllib.request
    now = time.time()
    claude, _ = make_box(tmp_path, [])
    codex = make_codex(tmp_path, now)
    server = sb.make_server(sb.Board(claude, None, codex_dir=codex), 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        data = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/api").read())
    finally:
        server.shutdown()
    assert sorted(s["bot"] for s in data["sessions"]) == ["claude", "codex"]
    assert data["bots"]["claude"]["sessions"] == 1 and data["bots"]["codex"]["sessions"] == 1
    page = (BIN / "session-board.html").read_text()
    assert 'id="bots"' in page and "s.bot" in page


def test_demo_has_a_codex_session_and_limits_for_both_bots(tmp_path, monkeypatch):
    sb.configure(None)
    claude = sb.demo_box(tmp_path)
    now = time.time()
    board = sb.Board(claude, None, codex_dir=tmp_path / "codex", limits_file=tmp_path / "state" / "claude-limits.json")
    rows = board.codex_rows(now)
    stats = board.bots(rows, now)
    assert [r["bot"] for r in rows] == ["codex"] and stats["codex"]["limits"] and stats["claude"]["limits"]
    assert str(Path.home()) not in json.dumps([rows, stats])

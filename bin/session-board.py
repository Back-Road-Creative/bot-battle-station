#!/usr/bin/env python3
"""Live terminal dashboard of the open Claude Code (and Codex) sessions on this box.

    python3 bin/session-board.py            # web page at http://localhost:8765 (open it in Windows)
    python3 bin/session-board.py --tui      # full-screen terminal board, q quits
    python3 bin/session-board.py --once     # one plain frame to stdout (pipes, tests)
    python3 bin/session-board.py --demo     # made-up sessions in every state, for a first look or a screenshot
    python3 bin/session-board.py --doctor   # what it found to read, for a bug report (counts and key names only)

Reads only files Claude Code and Codex already write, so it spends no tokens and calls
no model, outbound network or subprocess (the page is served on 127.0.0.1 only):

    $CLAUDE_CONFIG_DIR (else $HOME/.claude)/sessions/<pid>.json   live registry: name, sessionId, status
    .../projects/*/<sessionId>.jsonl              transcript: title, last prompt, away
                                              summary, tool calls, background tasks
    .../projects/*/<sessionId>/subagents/agent-*.meta.json   sub-agent jobs
    $CODEX_HOME (else $HOME/.codex)/sessions/YYYY/MM/DD/rollout-*.jsonl   Codex sessions a codex
                                              process holds open, and Codex's own usage limits
    <state_dir>/claude-limits.json            Claude's usage limits, if your status line writes them

An optional TOML config (``--config``; default ``session-board.toml`` beside this file) names what
is particular to one box: commands that mean "you run this" (``ask_commands``), watcher commands
(``watch_commands``), plan folders (``plan_dirs``), a roster TSV of names (``roster``) and where the
logs go (``state_dir``) and the Claude limits file (``claude_limits``). With none, only Claude Code's own prompt state is red.

A session is identified the way its status line shows it: the roster name and
role when registered, else the derived name, plus the first 8 hex of the id.

ETAs are medians of what already finished, never a guess from a model: a
sub-agent's is the median run time of finished sub-agents of the same type, a
background command's is the median of finished commands of the same kind in
that session, a busy reply's is the session's median reply length, and a plan's
is its steps left times the session's median minutes per step (measured from its
own edits marking steps done). A session's ETA is the longest of those, shown with
its % done. A watcher (the Monitor tool, a `watch_commands` match) has no ETA.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import select
import shutil
import statistics
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

__version__ = "0.2.0"

RESET = "\033[0m"
NEVER = re.compile(r"(?!)")
PLAN_RE = re.compile(rb"(?!)")  # set by configure() from plan_dirs
PLAN_DIRS: list[str] = []
ASSISTANT_RE = re.compile(rb'"type":\s*"assistant"')
DONE_RE = re.compile(r"^\W*(DONE|MERGED|CLOSED|SHIPPED|COMPLETE|COMPLETED)\b", re.I)
NOTIFY_RE = re.compile(r"<task-id>([^<]+)</task-id>.*?<status>([^<]+)</status>", re.S)
AGENT_HISTORY_CAP = 400
DEFAULT_CONTEXT = 1_000_000  # context size assumed until the session auto-compacts once
HOUR = 60  # minutes on the timeline strip
PROC = Path("/proc")  # Linux's process table; where it is missing, liveness falls back to signal 0
TESTED = (2, 1)  # newest Claude Code major.minor whose files this board was checked against
PLAN_MIN_MENTIONS = 3  # fewer is a passing reference, not the plan the session executes


def parse_ts(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def fmt_dur(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    s = int(max(seconds, 0))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{s % 3600 // 60:02d}m"


BOX_RE = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.+)$")  # a markdown checklist item


def done_cells(text: str) -> int:
    """Finished table cells plus ticked checklist boxes; an edit that raises the count marked steps done."""
    lines = str(text).splitlines()
    return (sum(bool(DONE_RE.match(c.strip())) for line in lines if line.lstrip().startswith("|")
                for c in line.strip().strip("|").split("|"))
            + sum(bool((m := BOX_RE.match(line)) and m.group(1) != " ") for line in lines))


SERVE_RE = re.compile(r"--port\b|http\.server|\brunserver\b|\bserve\b|npm (run )?(dev|start)\b")
QUIET = ("watch", "serve")  # jobs with no end of their own: no ETA, never behind
WATCH_COMMANDS: list[str] = []  # set by configure(): this box's watcher commands besides the Monitor tool


def job_kind(command: str, tool: str) -> str:
    if tool == "Monitor" or any(w in command for w in WATCH_COMMANDS):
        return "watch"
    if SERVE_RE.search(command):
        return "serve"  # a server runs until stopped; it holds nothing up
    if "pytest" in command or "test" in command:
        return "test"
    return "cmd"


SETUP_RE = re.compile(r"(cd|export|echo|set|source|true|unset|[A-Za-z_]\w*=)\b")
RUNNER_RE = re.compile(r"pytest|npm |node |make |bash |python3? ")


def command_label(command: str) -> str:
    """A readable name for an undescribed command: its worktree plus the step doing the work."""
    where = re.match(r"\s*cd\s+([^\s;&|]+)", command)
    steps = [s.strip() for s in re.split(r"&&|;|\|\||\n", command) if s.strip()]
    work = [s for s in steps if not SETUP_RE.match(s)] or steps or [command]
    runs = [s for s in work if RUNNER_RE.match(re.sub(r"^timeout\s+\S+\s+", "", s))]
    step = runs[-1] if runs else work[0]  # setup runs first; the last runner is the real work
    step = re.sub(r"^timeout\s+\S+\s+", "", step).split(" | ")[0]
    step = re.sub(r"\s*(\d?>>?\s*\S+|2>&1|<<-?\s*'?\w+'?)", "", step)
    step = re.sub(r"(?<![\w.~-])/(?:[^\s/]+/)*?([^\s/]+/[^\s/]+)(?=\s|$)", r"\1", step)  # a path: its last two parts
    place = Path(where.group(1).strip("\"'")).name if where else ""
    return f"{place} · {step}" if place else step


CMD_RE = NEVER  # set by configure() from ask_commands: a reply line holding a command for you to run
NOTHING_RE = re.compile(r"\bnothing\b[^.\n]{0,25}\b(for|from) you\b|\bno action\b[^.\n]{0,20}\byou\b", re.I)
OTHER_SESSION_RE = re.compile(r"\b(when|once|after|until)\b[^.\n]{0,60}\bsessions?\b[^.\n]{0,40}"
                              r"\b(wrap|finish|end|done|close|stop)", re.I)
ITEM_RE = re.compile(r"^\s*1\.\s+(.+)$", re.M)
TOPIC_CUT_RE = re.compile(r"(?<=[\w)])\.\s|;\s|:\s|,\s(?:and|but)\s|\s—\s")


def ask(reply: str) -> tuple[str, str]:
    """What a last reply leaves you: ("you", cmd) only for a command to run, ("session", what) when it waits on
    another session, ("next", what) for its first numbered action or closing question, else ("none", "").

    Only "you" is a block: a numbered list or a question is wording, so it never alarms.
    """
    cmd = CMD_RE.search(reply)
    if cmd:
        return "you", cmd.group(1).strip()
    items = ITEM_RE.findall(reply)
    first = items[-1].strip() if items else ""
    if OTHER_SESSION_RE.search(first or reply):
        return "session", clip(first or reply.strip().splitlines()[-1], 200)
    if NOTHING_RE.search(reply):
        return "none", ""
    if first:
        return "next", clip(first, 200)
    last = reply.strip().splitlines()[-1] if reply.strip() else ""
    return ("next", clip(last, 200)) if last.endswith("?") else ("none", "")


def topic(summary: str) -> str:
    """The subject of a summary the session wrote: its first clause, without "We're" or "You asked me to"."""
    head = TOPIC_CUT_RE.split(" ".join(str(summary).split()), 1)[0].strip().rstrip(".")
    head = re.sub(r"^(we[’']re|we are|i[’']m|i am)\s+", "", head, flags=re.I)
    head = re.sub(r"^you asked (me )?(for|to)\s+", "", head, flags=re.I)
    return clip(head[:1].upper() + head[1:], 90)


def next_step(summary: str) -> str:
    """The "Next, ..." sentence of a summary, as an instruction."""
    found = re.search(r"\bNext,?\s+(.+?)(?:\.\s|\.?$)", " ".join(str(summary).split()))
    text = found.group(1).replace("`", "") if found else ""
    return clip(text[:1].upper() + text[1:], 160)


def plan_title(path: Path) -> str:
    """A plan's own heading, without its date or "— plan" tail."""
    try:
        with path.open(encoding="utf-8") as fh:
            head = next((ln for ln in fh if ln.startswith("# ")), "")
    except OSError:
        return ""
    return clip(re.split(r"\s+—\s+|\s+\(", head[2:].strip(), maxsplit=1)[0], 90)


LATE_RUNS = 4  # fewer finished runs than this say nothing about what "late" is


def eta(elapsed: float, history: list[float]) -> tuple[float | None, bool]:
    """``(remaining seconds, behind)`` from finished runs of the same kind; None when there is no guess.

    The median is the estimate. Half of healthy runs pass it, so behind means past the 80th
    percentile of at least LATE_RUNS runs; between the two the estimate stretches to that line.
    """
    if not history:
        return None, False
    med = statistics.median(history)
    if elapsed <= med:
        return med - elapsed, False
    if len(history) < LATE_RUNS:
        return None, False
    line = statistics.quantiles(history, n=5, method="inclusive")[3]
    return max(line - elapsed, 0), elapsed > line


def _proc_children() -> dict[int, list[float]] | None:
    """Each process's children's start times (epoch s), from /proc; None where there is no /proc."""
    try:
        btime = next(float(x.split()[1]) for x in (PROC / "stat").read_text().splitlines()
                     if x.startswith("btime"))
        tick = os.sysconf("SC_CLK_TCK")
    except (OSError, StopIteration, ValueError):
        return None
    kids: dict[int, list[float]] = {}
    for stat in PROC.glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        kids.setdefault(int(fields[1]), []).append(btime + int(fields[19]) / tick)
    return kids


proc_children = _proc_children
DEMO_LOAD = "0.42"


def _loadavg() -> str:
    """The 1-minute load average, or ? where there is no /proc."""
    try:
        return (PROC / "loadavg").read_text().split()[0]
    except (OSError, IndexError):
        return "?"


loadavg = _loadavg


class Transcript:
    """Incremental reader of one session transcript: each refresh parses only appended bytes."""

    def __init__(self, path: Path):
        self.path, self.offset, self.partial = path, 0, b""
        self.title = self.last_prompt = self.summary = self.branch = self.reply = ""
        self.now_text, self.now_ts = "", None
        self.summary_ts: float | None = None
        self.usage: dict[str, tuple] = {}     # message id -> (ts, new tokens, cache reads), once per reply
        self.turn_spans: list[tuple[float, float]] = []  # (start, end) of each finished reply
        self.turn_start: float | None = None
        self.turn_durations: list[float] = []
        self.prs: dict[str, str] = {}
        self.plans: dict[str, int] = {}       # plan file name -> times the transcript names it
        self.launches: dict[str, dict] = {}   # tool_use_id -> launch info
        self.tasks: dict[str, dict] = {}      # background task id -> job
        self.tools = self.errors = self.compactions = self.context = 0
        self.full: int | None = None          # tokens at the last auto-compact: when this session fills
        self.active: set[int] = set()         # minutes (epoch // 60) with model activity
        self.pr_ts: dict[str, float] = {}     # PR url -> when the session first linked it
        self.plan_first: dict[str, float] = {}           # plan -> first time the session named it
        self.steps_done: dict[str, list[float]] = {}     # plan -> times its edits marked steps done

    def refresh(self) -> None:
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.__init__(self.path)
            with self.path.open("rb") as fh:
                fh.seek(self.offset)
                data = fh.read()
        except OSError:
            return
        self.offset += len(data)
        lines = (self.partial + data).split(b"\n")
        self.partial = lines.pop()
        for raw in lines:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            for name in PLAN_RE.findall(raw) if ASSISTANT_RE.search(raw) else ():
                key = name.decode()  # the session's own words only; injected context names stray plans
                self.plans[key] = self.plans.get(key, 0) + 1
                self.plan_first.setdefault(key, parse_ts(rec.get("timestamp")) or 0)
            self.feed(rec)

    def feed(self, rec: dict) -> None:
        kind, ts = rec.get("type"), parse_ts(rec.get("timestamp"))
        if rec.get("gitBranch"):
            self.branch = rec["gitBranch"]
        if kind == "ai-title":
            self.title = rec.get("aiTitle") or self.title
        elif kind == "last-prompt":
            self.last_prompt = rec.get("lastPrompt") or self.last_prompt
        elif kind == "pr-link" and rec.get("prUrl"):
            self.prs[rec["prUrl"]] = f"#{rec.get('prNumber')}"
            self.pr_ts.setdefault(rec["prUrl"], ts or 0)
        elif kind == "system":
            if rec.get("subtype") == "away_summary" and isinstance(rec.get("content"), str):
                self.summary, self.summary_ts = rec["content"], ts
            elif rec.get("subtype") == "turn_duration" and rec.get("durationMs"):
                self.turn_durations.append(rec["durationMs"] / 1000)
                if ts:
                    self.turn_spans.append((ts - rec["durationMs"] / 1000, ts))
                self.turn_start = None
            elif rec.get("subtype") == "compact_boundary":
                self.compactions += 1
                meta = rec.get("compactMetadata") or {}
                if meta.get("trigger") == "auto" and meta.get("preTokens"):
                    self.full = meta["preTokens"]
        elif kind == "queue-operation" and isinstance(rec.get("content"), str):
            self.notify(rec["content"], ts)
        elif kind == "assistant":
            self.assistant(rec, ts)
        elif kind == "user":
            self.user(rec, ts)

    def notify(self, text: str, ts: float | None) -> None:
        for task_id, status in NOTIFY_RE.findall(text):
            job = self.tasks.get(task_id.strip())
            if job and job["end"] is None:
                job["end"], job["status"] = ts, status.strip()

    def assistant(self, rec: dict, ts: float | None) -> None:
        message = rec.get("message") or {}
        content = message.get("content")
        usage = message.get("usage") or {}
        if usage:
            self.context = sum(usage.get(k) or 0 for k in
                               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            # one reply is split over several records that repeat its usage: count it once
            self.usage[message.get("id") or rec.get("uuid") or str(ts)] = (ts or 0, sum(
                usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "output_tokens")),
                usage.get("cache_read_input_tokens") or 0)
        if ts:
            self.active.add(int(ts // 60))
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text", "").strip():
                self.now_text, self.now_ts = block["text"].strip().splitlines()[0], ts
                self.reply = block["text"].strip()
            elif block.get("type") == "tool_use":
                name, args = block.get("name", "?"), block.get("input") or {}
                self.tools += 1
                plan = PLAN_RE.search(str(args.get("file_path") or "").encode())
                if plan and ts:
                    edits = args.get("edits") if isinstance(args.get("edits"), list) else [args]
                    marked = sum(done_cells(e.get("new_string", "")) - done_cells(e.get("old_string", ""))
                                 for e in edits if isinstance(e, dict))
                    self.steps_done.setdefault(plan.group(1).decode(), []).extend([ts] * max(marked, 0))
                desc = (args.get("description") or (command_label(args["command"]) if args.get("command") else "")
                        or args.get("prompt") or "")
                self.now_text, self.now_ts = f"{name}: {str(desc).splitlines()[0] if desc else ''}", ts
                if name == "TaskStop" and args.get("task_id") in self.tasks:
                    job = self.tasks[args["task_id"]]
                    if job["end"] is None:
                        job["end"], job["status"] = ts, "stopped"
                elif args.get("run_in_background") or name == "Monitor":
                    cmd = str(args.get("command") or args.get("prompt") or "")
                    self.launches[block.get("id", "")] = {
                        "label": str(desc).splitlines()[0][:80] if desc else name,
                        "kind": job_kind(cmd, name), "start": ts, "tool": name}

    def user(self, rec: dict, ts: float | None) -> None:
        content = (rec.get("message") or {}).get("content")
        origin = rec.get("origin") or {}
        if isinstance(content, str) and origin.get("kind") == "task-notification":
            self.notify(content, ts)
            if self.turn_start is None:
                self.turn_start = ts  # a finished background task wakes the session: a new turn
            return
        if self.turn_start is None and not rec.get("toolUseResult"):
            self.turn_start = ts  # first user record after a finished turn opens the next
        if isinstance(content, str):
            return
        result = rec.get("toolUseResult")
        task_id = None
        if isinstance(result, dict):
            task_id = result.get("backgroundTaskId") or result.get("taskId")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            self.errors += bool(block.get("type") == "tool_result" and block.get("is_error"))
            launch = self.launches.pop(block.get("tool_use_id", ""), None)
            if launch and task_id:
                self.tasks[task_id] = {**launch, "end": None, "status": "running", "spawned": ts or launch["start"]}

    def step_pace(self, plan: str) -> float | None:
        """Seconds per plan step: from first naming the plan to its last edit marking a step done."""
        marks, first = self.steps_done.get(plan), self.plan_first.get(plan)
        return (max(marks) - first) / len(marks) if marks and first and max(marks) > first else None

    def history(self, kind: str) -> list[float]:
        return [j["end"] - j["start"] for j in self.tasks.values()
                if j["kind"] == kind and j["end"] and j["start"] and j["status"] == "completed"]

    def tokens(self, since: float) -> tuple[int, int]:
        """(new tokens, cache reads) of the replies since ``since``."""
        rows = [u for u in self.usage.values() if u[0] >= since]
        return sum(u[1] for u in rows), sum(u[2] for u in rows)


def step_name(cells: list[str], col: int) -> str:
    """A plan row's name: its first cell, joined to the next when the first is only an id like W3."""
    words = [re.sub(r"[*`]", "", c).strip() for i, c in enumerate(cells) if i != col]
    words = [w for w in words if w]
    if len(words) > 1 and len(words[0]) <= 4:
        return clip(f"{words[0]} {words[1]}", 70)
    return clip(words[0], 70) if words else "?"


def plan_progress(path: Path) -> tuple[int, int, list[str]] | None:
    """``(done, total, open step names)`` over every table with a Status column and every ``- [ ]`` checklist
    item; None without either."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    done = total = 0
    col, open_steps = None, []
    for line in text.splitlines():
        box = BOX_RE.match(line)
        if box:
            total += 1
            done += box.group(1) != " "
            if box.group(1) == " ":
                open_steps.append(clip(re.sub(r"[*`]", "", box.group(2)).strip(), 70))
        if not line.lstrip().startswith("|"):
            col = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if col is None:
            lowered = [c.lower().strip("*") for c in cells]
            col = lowered.index("status") if "status" in lowered else -1
        elif col >= 0 and len(cells) > col and not set(cells[col]) <= set("-: "):
            total += 1
            finished = bool(DONE_RE.match(cells[col]))
            done += finished
            if not finished:
                open_steps.append(step_name(cells, col))
    return (done, total, open_steps) if total else None


def main_root(path: Path) -> Path:
    """The main checkout for ``path`` (a linked worktree's ``.git`` file names it)."""
    for base in [path, *path.parents]:
        git = base / ".git"
        if git.is_file():
            target = git.read_text(encoding="utf-8").partition("gitdir:")[2].strip()
            return Path(target).parents[2] if "/worktrees/" in target else base
        if git.is_dir() and (git / "HEAD").is_file():
            return base
    return path


def first_ts(path: Path) -> float | None:
    try:
        with path.open("rb") as fh:
            return parse_ts(json.loads(fh.readline()).get("timestamp"))
    except (OSError, ValueError, AttributeError):
        return None


def last_action(path: Path) -> str:
    """The sub-agent's most recent tool call or text line, from the file's tail."""
    try:
        with path.open("rb") as fh:
            fh.seek(max(path.stat().st_size - 65536, 0))
            lines = fh.read().split(b"\n")[1:]
    except OSError:
        return ""
    for raw in reversed(lines):
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        content = ((rec.get("message") or {}).get("content") if isinstance(rec, dict) else None)
        if rec.get("type") != "assistant" or not isinstance(content, list):
            continue
        for block in reversed(content):
            if block.get("type") == "tool_use":
                args = block.get("input") or {}
                what = args.get("description") or args.get("file_path") or command_label(str(args.get("command") or ""))
                return f"{block.get('name')}: {what}"
            if block.get("type") == "text" and block.get("text", "").strip():
                return block["text"].strip().splitlines()[0]
    return ""


CODEX_PROC_RE = re.compile(rb"codex")  # a process whose command line names Codex; its open rollouts are live sessions
CODEX_RECENT = 600  # no process table: a rollout written this recently counts as a live session
CODEX_TAIL = 512 * 1024  # a rollout runs to megabytes; its newest records sit at the end
CODEX_WINDOWS = {300: "5h", 10080: "weekly"}  # rate-limit window minutes -> the name Codex's own UI gives it
CLAUDE_WINDOWS = (("five_hour", "5h"), ("seven_day", "weekly"))  # the status line's rate_limits keys


def codex_open_files() -> set[Path] | None:
    """Rollout files a Codex process holds open: its live sessions. None where there is no /proc."""
    if not (PROC / "self").exists():
        return None
    held = set()
    for pid in PROC.glob("[0-9]*"):
        try:
            if not CODEX_PROC_RE.search((pid / "cmdline").read_bytes()):
                continue
            held.update(Path(t) for t in (os.readlink(fd) for fd in (pid / "fd").iterdir()) if t.endswith(".jsonl"))
        except OSError:
            continue  # gone, or another user's
    return held


def codex_rollout(path: Path) -> dict:
    """What a Codex rollout says: its session record (the first line) and the newest of each kind at its end."""
    out = {"meta": {}, "running": False, "said": "", "said_ts": None, "asked": "", "info": None, "limits": None,
           "limits_ts": None}
    with path.open("rb") as fh:
        first = fh.readline()
        size = fh.seek(0, 2)
        fh.seek(max(len(first), size - CODEX_TAIL))
        lines = [first, *fh.read().splitlines()]
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue  # the tail's cut first line, or a line still being written
        pay = rec.get("payload") or {}
        kind, ts = pay.get("type") if rec.get("type") == "event_msg" else rec.get("type"), parse_ts(rec.get("timestamp"))
        if kind == "session_meta":
            out["meta"] = pay
        elif kind in ("task_started", "task_complete", "turn_aborted"):
            out["running"] = kind == "task_started"
        elif kind == "agent_message":
            out["said"], out["said_ts"] = str(pay.get("message") or ""), ts
        elif kind == "user_message":
            out["asked"] = str(pay.get("message") or "")
        elif kind == "token_count":
            out["info"] = pay.get("info") or out["info"]
            if pay.get("rate_limits"):
                out["limits"], out["limits_ts"] = pay["rate_limits"], ts
    return out


def limit_window(name: str, used, resets, now: float) -> dict | None:
    """One usage window as the page shows it, or None when its numbers can't be trusted: a percentage that is not a
    finite number is dropped, one outside 0-100 is clamped, and a window whose reset has passed is stale, so dropped."""
    if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used):
        return None
    if isinstance(resets, str):
        resets = parse_ts(resets)
    if isinstance(resets, bool) or not isinstance(resets, (int, float)) or not math.isfinite(resets) or resets <= 0:
        resets = None
    if resets is not None and resets <= now:
        return None
    return {"name": name, "used": round(min(max(used, 0), 100)), "resets": int(resets) if resets else None}


def codex_limits(limits, now: float) -> list[dict]:
    """Codex's rate-limit windows, worded the way the page shows both bots."""
    out = []
    for key in ("primary", "secondary"):
        w = limits.get(key) if isinstance(limits, dict) else None
        if isinstance(w, dict):
            mins = w.get("window_minutes")
            name = (CODEX_WINDOWS.get(mins, f"{mins // 60}h") if isinstance(mins, int) and mins > 0 else "?")
            if (win := limit_window(name, w.get("used_percent"), w.get("resets_at"), now)):
                out.append(win)
    return out


class Board:
    def __init__(self, claude_dir: Path, roster: Path | None, codex_dir: Path | None = None,
                 limits_file: Path | None = None):
        self.claude_dir, self.roster_path = claude_dir, roster
        self.codex_dir, self.limits_file = codex_dir, limits_file
        self.rollouts: dict[Path, tuple[tuple[int, int], dict]] = {}  # path -> ((mtime_ns, size), what it says)
        self.transcripts: dict[str, Transcript] = {}
        self.agent_hist: dict[str, list[float]] | None = None
        self.helpers: dict[Path, Transcript] = {}  # sub-agent transcripts, read for their tokens
        self.versions: set[str] = set()  # Claude Code versions the live sessions run

    def roster(self) -> dict[str, tuple[str, str]]:
        rows: dict[str, tuple[str, str]] = {}
        try:
            text = self.roster_path.read_text(encoding="utf-8") if self.roster_path else ""
        except OSError:
            return rows
        for line in text.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0].strip() and not line.startswith("#"):
                rows[parts[0].strip()] = (parts[1].strip(), parts[2].strip())
        return rows

    def live_sessions(self) -> list[dict]:
        by_sid, proc = {}, (PROC / "self").exists()
        for reg in sorted((self.claude_dir / "sessions").glob("*.json")):
            try:
                info = json.loads(reg.read_text(encoding="utf-8"))
                if proc:
                    stat = (PROC / str(info["pid"]) / "stat").read_text()
                else:
                    os.kill(int(info["pid"]), 0)  # no /proc: alive, though a reused pid looks alive too
            except PermissionError:
                pass  # signal 0 refused: the pid is alive under another user
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if proc and info.get("procStart") and str(info["procStart"]) != stat.rsplit(")", 1)[-1].split()[19]:
                continue  # pid reused by another process
            if info.get("version"):
                self.versions.add(str(info["version"]))
            if info.get("sessionId"):
                previous = by_sid.get(info["sessionId"])
                if previous is None or (info.get("startedAt") or 0) >= (previous.get("startedAt") or 0):
                    by_sid[info["sessionId"]] = info
        return sorted(by_sid.values(), key=lambda s: (not s.get("startedAt"), s.get("startedAt") or 0))

    def agent_history(self) -> dict[str, list[float]]:
        """Run times of finished sub-agents by type, newest AGENT_HISTORY_CAP; built once."""
        if self.agent_hist is None:
            self.agent_hist = {}
            metas = sorted(self.claude_dir.glob("projects/*/*/subagents/*.meta.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)[:AGENT_HISTORY_CAP]
            for meta_path in metas:
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    log = meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))
                    start, end = first_ts(log), log.stat().st_mtime
                except (OSError, ValueError):
                    continue
                if meta.get("finished") and start:
                    self.agent_hist.setdefault(meta.get("agentType", "?"), []).append(end - start)
        return self.agent_hist

    def snapshot(self, now: float) -> list[dict]:
        roster, rows, kids = self.roster(), [], proc_children()
        for info in self.live_sessions():
            sid = info.get("sessionId", "")
            tr = self.transcripts.get(sid)
            if tr is None:
                found = next(iter(self.claude_dir.glob(f"projects/*/{sid}.jsonl")), None)
                if found is None:
                    continue
                tr = self.transcripts[sid] = Transcript(found)
            tr.refresh()
            name, role = info.get("name", "session"), ""
            for prefix in sorted(roster, key=len, reverse=True):
                if sid.startswith(prefix):
                    name, role = roster[prefix]
                    break
            started_ms = info.get("startedAt")
            started_unknown = not isinstance(started_ms, (int, float)) or started_ms <= 0
            started = now if started_unknown else started_ms / 1000
            jobs = self.jobs(tr, sid, started, now, None if kids is None else kids.get(info.get("pid"), []))
            cwd = Path(info.get("cwd") or ".")
            rows.append({"name": name, "role": role, "sid": sid[:8], "status": info.get("status", "?"),
                         "uptime": now - started, "started_unknown": started_unknown,
                         "tr": tr, "jobs": jobs, "plan": self.plan(tr, cwd),
                         "plan_title": next(filter(None, map(plan_title, self.plan_paths(tr, cwd))), ""),
                         "waiting_for": str(info.get("waitingFor") or ""), "tokens": self.tokens(tr, now)})
        fulls = [t.full for t in self.transcripts.values() if t.full]
        pooled = [d for t in self.transcripts.values() for d in t.turn_durations]
        paces = [p for t in self.transcripts.values() for p in map(t.step_pace, t.steps_done) if p]
        for row in rows:
            plan = row["plan"]
            if plan:  # this session's own pace per step, else every session's
                pace = row["tr"].step_pace(plan["file"]) or (statistics.median(paces) if paces else None)
                plan["left"] = pace * (plan["total"] - plan["done"]) if pace else None
            row["progress"] = self.progress(row["status"], row["tr"], row["jobs"], now, pooled, plan)
            row["eta"] = row["progress"]["text"]
            row["box_full"] = statistics.median(fulls) if fulls else None
        return rows

    def rollout(self, path: Path) -> dict:
        """One Codex rollout, re-read only when it changed."""
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
        if self.rollouts.get(path, (None,))[0] != key:
            self.rollouts[path] = (key, codex_rollout(path))
        return self.rollouts[path][1]

    def codex_rows(self, now: float) -> list[dict]:
        """Live Codex sessions as page rows: its own threads, not the sub-threads (reviews, helpers) they start."""
        if not self.codex_dir or not (self.codex_dir / "sessions").is_dir():
            return []
        held, roster, names, by_sid = codex_open_files(), self.roster(), {}, {}
        try:
            for line in (self.codex_dir / "session_index.jsonl").read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                names[entry.get("id")] = str(entry.get("thread_name") or "")
        except (OSError, ValueError, AttributeError):
            pass
        sessions = (self.codex_dir / "sessions").resolve()
        if held is None:
            paths = [p for p in sessions.glob("*/*/*/*.jsonl") if now - p.stat().st_mtime < CODEX_RECENT]
        else:
            paths = [p for p in held if p.is_relative_to(sessions)]
        for path in sorted(paths):
            try:
                ro = self.rollout(path)
                mtime = path.stat().st_mtime
            except OSError:
                continue
            meta = ro["meta"]
            if not meta.get("id") or meta.get("parent_thread_id"):
                continue
            sid = str(meta["id"])
            name, role = Path(str(meta.get("cwd") or "codex")).name, ""
            for prefix in sorted(roster, key=len, reverse=True):
                if sid.startswith(prefix):
                    name, role = roster[prefix]
                    break
            started = parse_ts(meta.get("timestamp")) or mtime
            last = ro["said_ts"] or mtime
            info = ro["info"] or {}
            used = int((info.get("last_token_usage") or {}).get("input_tokens") or 0)
            full = int(info.get("model_context_window") or 0) or DEFAULT_CONTEXT
            title = names.get(sid) or clip(ro["asked"], 80)
            row = {
                "bot": "codex", "name": name, "role": role, "sid": sid[:8],
                "state": "RUNNING" if ro["running"] else "IDLE",
                "eta": "working" if ro["running"] else "ready",
                "progress": {"text": "", "left": None, "at": None, "pct": None, "behind": False},
                "started": started, "uptime": fmt_dur(now - started), "title": title, "topic": title, "next": "",
                "started_as": names.get(sid, ""), "plan_title": "", "ask": "", "waits_on": "", "for_you": "",
                "warn": ["context near full"] if used >= 0.9 * full else [], "reply": ro["said"][-1500:],
                "summary_age": "", "tokens": {"today": 0, "cache": 0, "helpers": 0}, "now": clip(ro["said"], 200),
                "ago": fmt_dur(now - last), "waited": now - last, "summary": ro["asked"],
                "branch": str((meta.get("git") or {}).get("branch") or ""), "prs": [], "prs_today": [], "plan": None,
                "finished": 0, "meters": {"working": None, "shipped": 0, "pace": None}, "timeline": "-" * HOUR,
                "context": {"used": used, "full": full, "pct": min(round(100 * used / full), 100), "basis": "own",
                            "compactions": 0},
                "jobs": []}
            previous = by_sid.get(sid)
            if previous is None or mtime >= previous[0]:
                by_sid[sid] = (mtime, row)
        return [row for _, row in by_sid.values()]

    def bots(self, rows: list[dict], now: float) -> dict:
        """Per-bot numbers for the page's top strip: live sessions, and each bot's usage limits where a file has them."""
        claude: dict = {"sessions": sum(r.get("bot") == "claude" for r in rows), "limits": [], "age": None}
        try:
            data = json.loads(self.limits_file.read_text(encoding="utf-8"))
            claude["age"] = now - self.limits_file.stat().st_mtime
        except (AttributeError, OSError, ValueError):  # no file named, none written yet, or half-written
            data = {}
        for key, label in CLAUDE_WINDOWS:
            w = data.get(key) if isinstance(data, dict) else None
            if isinstance(w, dict) and (win := limit_window(label, w.get("used_percentage"), w.get("resets_at"), now)):
                claude["limits"].append(win)
        codex: dict = {"sessions": sum(r.get("bot") == "codex" for r in rows), "limits": [], "plan": "", "age": None}
        days = sorted((self.codex_dir / "sessions").glob("*/*/*")) if self.codex_dir else []
        newest = max(days[-1].glob("*.jsonl"), key=lambda p: p.stat().st_mtime, default=None) if days else None
        if newest:
            try:
                ro = self.rollout(newest)
            except OSError:
                ro = {"limits": None}
            if isinstance(ro["limits"], dict):
                codex.update(limits=codex_limits(ro["limits"], now), plan=str(ro["limits"].get("plan_type") or ""),
                             age=now - ro["limits_ts"] if ro["limits_ts"] else None)
        return {"claude": claude, "codex": codex}

    def tokens(self, tr: Transcript, now: float) -> dict:
        """Tokens the session spent today (input, cache writes, output; cache reads apart), helpers included."""
        midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        new, cache = tr.tokens(midnight)
        helpers = 0
        for log in sorted(tr.path.with_suffix("").glob("subagents/*.jsonl")):
            helper = self.helpers.setdefault(log, Transcript(log))
            helper.refresh()
            h_new, h_cache = helper.tokens(midnight)
            helpers, cache = helpers + h_new, cache + h_cache
        return {"today": new + helpers, "cache": cache, "helpers": helpers}

    @staticmethod
    def plan_paths(tr: Transcript, cwd: Path) -> list[Path]:
        """The plan file this session names most (PLAN_MIN_MENTIONS times or more), never a runner-up."""
        top = max(tr.plans, key=tr.plans.get, default=None)
        if not top or tr.plans[top] < PLAN_MIN_MENTIONS:
            return []
        return [root / d / top for root in (cwd, main_root(cwd)) for d in PLAN_DIRS]

    @staticmethod
    def plan(tr: Transcript, cwd: Path) -> dict | None:
        """Progress of the plan file this session names most, if it exists and has a Status table."""
        for path in Board.plan_paths(tr, cwd):  # no Status table: no PLAN line
            progress = plan_progress(path)
            if progress:
                return {"name": path.stem, "file": path.name, "done": progress[0],
                        "total": progress[1], "open": progress[2][:20]}
        return None

    def jobs(self, tr: Transcript, sid: str, started: float, now: float,
             children: list[float] | None = None) -> list[dict]:
        """`children`: start times of the session's child processes (None: unknown). A background
        shell with no child process started when it was is gone, though no notice said so."""
        jobs, gone = [], set()
        if children is not None:
            free = sorted(children)
            for tid, job in sorted(tr.tasks.items(), key=lambda kv: kv[1].get("spawned") or 0):
                if job["end"] is not None or job.get("tool") != "Bash":
                    continue
                at = job.get("spawned") or job["start"] or 0
                mine = next((c for c in free if at - 5 <= c <= at + 30), None)
                if mine is None:
                    gone.add(tid)
                else:
                    free.remove(mine)
        for tid, job in tr.tasks.items():
            if job["start"] and job["start"] >= started - 1:
                done = job["end"] is not None or tid in gone
                elapsed = ((job["end"] or now) if done else now) - job["start"]
                hist = (self.watch_history() if job["kind"] == "watch" else [] if job["kind"] in QUIET
                        else tr.history(job["kind"]))
                rem, late = eta(elapsed, hist)
                if job["kind"] == "watch":  # the usual CI wait; past it there is no guess, and a wait is never behind
                    rem, late = rem or None, False
                jobs.append({"kind": job["kind"], "label": job["label"], "done": done, "start": job["start"],
                             "status": "ended without notice" if tid in gone else job["status"], "elapsed": elapsed, "eta": rem, "late": late,
                             "typical": statistics.median(hist) if hist else None, "runs": len(hist)})
        sub_dir = tr.path.with_suffix("") / "subagents"
        for meta_path in sorted(sub_dir.glob("*.meta.json")):
            log = meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))
            try:
                meta, mtime = json.loads(meta_path.read_text(encoding="utf-8")), log.stat().st_mtime
            except (OSError, ValueError):
                continue
            start = first_ts(log) or mtime
            if start < started - 1:
                continue
            done = bool(meta.get("finished"))
            elapsed = (mtime if done else now) - start
            hist = self.agent_history().get(meta.get("agentType", "?"), [])
            rem, late = eta(elapsed, hist)
            jobs.append({"kind": "agent", "type": meta.get("agentType", "agent"), "label": meta.get("description", ""),
                         "done": done, "start": start, "status": "done" if done else "running", "elapsed": elapsed,
                         "eta": rem, "late": late, "now": "" if done else last_action(log),
                         "typical": statistics.median(hist) if hist else None, "runs": len(hist),
                         "stalled": not done and now - mtime > 1200})
        return jobs

    def watch_history(self) -> list[float]:
        """How long finished watchers (Monitor, `watch_commands`) waited, across every session on the box."""
        return [d for t in self.transcripts.values() for d in t.history("watch")]

    @staticmethod
    def progress(status, tr: Transcript, jobs: list[dict], now: float, pooled: list[float],
                 plan: dict | None = None) -> dict:
        """Minutes left and % done: the longest of running jobs, a busy reply and the plan's steps left.

        % is the plan's when there is one (the whole job), else the slowest job's or the reply's.
        """
        lefts, pct, behind = [], None, None
        running = [j for j in jobs if not j["done"] and j["kind"] != "serve"]
        known =[j for j in running if j["eta"] is not None]
        if known:
            slow = max(known, key=lambda j: j["elapsed"] / (j["typical"] or 1))
            pct = min(slow["elapsed"] / slow["typical"], 1) if slow["typical"] else None
            if all(j["late"] for j in known):
                behind = max(j["elapsed"] - (j["typical"] or 0) for j in known)
            else:
                lefts.append(max(j["eta"] for j in known if not j["late"]))
        elif status == "busy" and tr.turn_start and (tr.turn_durations or pooled):
            usual = statistics.median(tr.turn_durations or pooled)
            spent = now - tr.turn_start
            pct = min(spent / usual, 1)
            if spent > usual:
                behind = spent - usual
            else:
                lefts.append(usual - spent)
        steps = plan["total"] - plan["done"] if plan else 0
        if plan:
            pct = plan["done"] / plan["total"]
            if steps and plan.get("left") is not None:
                lefts.append(plan["left"])
        left = max(lefts) if lefts else None
        pct_txt = f" · {round(100 * pct)}%" if pct is not None else ""
        if left is not None and (behind is None or steps):
            text = f"~{fmt_dur(left)}{pct_txt}"
        elif behind is not None:
            text = f"{fmt_dur(behind)} behind"
        elif running:
            text = "waiting for a job"
        elif steps:
            text = f"{steps} steps left{pct_txt}"
        elif status == "busy":
            text = "first run, no estimate yet"
        else:
            text = "ready" if status == "idle" else "—"
        return {"text": text, "left": left, "at": None if left is None else now + left, "pct": None if pct is None else round(100 * pct),
                "behind": behind is not None}


SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def rgb(r: int, g: int, b: int) -> str:
    return f"\033[38;2;{r};{g};{b}m"


CYAN, ICE, MAGENTA, AMBER_T, RED_T, SLATE, TEXT, MINT = (
    (0, 229, 255), (190, 240, 255), (255, 64, 214), (255, 184, 0), (255, 70, 90), (84, 104, 138),
    (205, 222, 240), (80, 255, 170))
BOLD = "\033[1m"


class Ink:
    """Colours for one frame; every method returns '' when colour is off (pipes, tests)."""

    def __init__(self, on: bool):
        self.on = on

    def __call__(self, tone: tuple[int, int, int], bold: bool = False) -> str:
        return (BOLD if bold else "") + rgb(*tone) if self.on else ""

    def reset(self) -> str:
        return RESET if self.on else ""

    def blend(self, a: tuple, b: tuple, t: float) -> str:
        return rgb(*(round(x + (y - x) * t) for x, y in zip(a, b))) if self.on else ""


def clip(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


def vis(text: str) -> int:
    return len(ANSI_RE.sub("", text))


def gauge(ink: Ink, frac: float, width: int, hot: bool = False) -> str:
    """█░ bar; the filled part fades cyan→magenta (all red when hot)."""
    fill = max(0, min(width, round(width * frac)))
    cells = [ink(RED_T) if hot else ink.blend(CYAN, MAGENTA, i / max(width - 1, 1)) for i in range(fill)]
    return "".join(f"{c}█" for c in cells) + ink(SLATE) + "░" * (width - fill)


def asked(row: dict) -> tuple[str, str]:
    """What the session waits for: the prompt the registry shows, else what its last reply asks."""
    if row["status"] == "waiting":
        return "you", row.get("waiting_for") or "answer the prompt in the session"
    return ("none", "") if row["status"] == "busy" else ask(row["tr"].reply)


def status_of(row: dict) -> tuple[str, tuple]:
    """NEEDS YOU only when the session asks you to act; waiting on a job is not that.

    A reply that says it waits on another session is wording, so it is a note on the tile (``waits_on``), not a state.
    """
    kind = asked(row)[0]
    if kind == "you":
        return "NEEDS YOU", RED_T
    if row["status"] == "busy":
        return "RUNNING", CYAN
    if row["status"] == "shell" or any(not j["done"] and j["kind"] != "serve" for j in row["jobs"]):
        return "WAITING ON JOB", AMBER_T
    return "IDLE", SLATE


def row_topic(row: dict) -> str:
    """What the session is about: its own latest summary, else its plan's heading, else its first title."""
    tr = row["tr"]
    return ((topic(tr.summary) if tr.summary else "") or row.get("plan_title") or tr.title
            or clip(tr.last_prompt, 80))


def card(row: dict, width: int, now: float, color: bool) -> list[str]:
    ink = Ink(color)
    label, tone = status_of(row)
    inner = width - 4
    edge = ink(tone)
    spin = SPIN[int(now * 8) % len(SPIN)] if color and label == "RUNNING" else "●"
    name = row["name"] + (f" · {row['role']}" if row["role"] else "")
    left = f"━ ◆ {name} ━ {row['sid']} "
    right = f" {spin} {label} ━ ETA {row['eta']} ━"
    fill = max(width - 2 - len(left) - len(right), 1)
    lines = [f"{edge}┏━{ink(ICE, True)}{left[1:]}{edge}{'━' * fill}{ink(tone, True)}{right[:-1]}{edge}━┓"]

    def put(*parts: tuple[tuple, str]) -> None:
        """One body line from (tone, text) parts, clipped and padded to the card."""
        out, room = "", inner
        for tone_, text in parts:
            text = clip(text, room) if len(" ".join(str(text).split())) > room else str(text)
            out, room = out + ink(tone_) + text, room - len(text)
            if room <= 0:
                break
        lines.append(f"{edge}┃ {out}{' ' * max(room, 0)}{edge} ┃")

    tr = row["tr"]
    prs = list(tr.prs.values())
    pr_txt = (" · PR " + " ".join(prs[-3:]) + (f" +{len(prs) - 3}" if len(prs) > 3 else "")) if prs else ""
    meta = f"⎇ {tr.branch or '?'} · up {'unknown' if row.get('started_unknown') else fmt_dur(row['uptime'])}{pr_txt}"
    title = row_topic(row) or "—"
    meta = clip(meta, max(inner - min(len(title), 40) - 2, 10))
    title = clip(title, inner - len(meta) - 2)
    put((TEXT, title), (SLATE, " " * (inner - len(title) - len(meta)) + meta))
    ago = f" · {fmt_dur(now - tr.now_ts)} ago" if tr.now_ts else ""
    kind, what = asked(row)
    if kind == "you":
        put((RED_T, "⚠ ASKS "), (ICE, what))
    elif kind == "session":
        put((AMBER_T, "▸ WAIT "), (ICE, what))
    else:
        put((CYAN, "▸ NOW  "), (ICE, clip(tr.now_text or "—", inner - 7 - len(ago))), (SLATE, ago))
    put((SLATE, "  SUM  "), (TEXT, tr.summary or f"last ask: {tr.last_prompt or '—'}"))
    plan = row.get("plan")
    if plan:
        done, total = plan["done"], plan["total"]
        txt = clip(f"{done}/{total} steps done · {round(100 * done / total)}% · {plan['name']}", inner - 28)
        lines.append(f"{edge}┃ {ink(SLATE)}  PLAN {gauge(ink, done / total, 20)} {ink(MINT if done == total else MAGENTA)}"
                     f"{txt}{' ' * (inner - 28 - len(txt))}{edge} ┃")
    jobs = row["jobs"]
    if jobs:
        done = sum(j["done"] for j in jobs)
        put((SLATE, "  JOBS "), (CYAN if done < len(jobs) else SLATE, f"{len(jobs) - done} running"),
            (SLATE, f" · {done} finished"))
        running = [j for j in jobs if not j["done"]][-5:]
        for i, job in enumerate(running):
            branch = "└" if i == len(running) - 1 else "├"
            hot = bool(job.get("stalled") or job["late"])
            if job["kind"] in QUIET:
                dial = (ink(AMBER_T) + (SPIN[int(now * 8) % len(SPIN)] if color else "◌")
                        + f" {'waiting' if job['kind'] == 'watch' else 'running'} · {fmt_dur(job['elapsed'])}")
            elif job["eta"] is None:
                dial = ink(SLATE) + f"first run · {fmt_dur(job['elapsed'])}"
            else:
                usual = job["typical"] or 0
                frac = 1.0 if hot else job["elapsed"] / usual if usual else 0
                over = (f" {fmt_dur(job['elapsed'] - usual)} behind (usual {fmt_dur(usual)})"
                        if job["late"] else f" ~{fmt_dur(job['eta'])} left")
                dial = (gauge(ink, frac, 10, hot) + ink(RED_T if hot else TEXT)
                        + f" {fmt_dur(job['elapsed'])}{over}" + (" STALLED" if job.get("stalled") else ""))
            tag = f"{job['kind']:<5} "
            room = inner - 4 - len(tag) - vis(dial) - 1
            what = job["label"] + (f" ({job['type']})" if job.get("type") else "")
            what += f" — {job['now']}" if job.get("now") else ""
            what = clip(what, max(room, 4))
            lines.append(f"{edge}┃ {ink(SLATE)}  {branch} {ink(AMBER_T if job['kind'] == 'watch' else CYAN)}{tag}"
                         f"{ink(TEXT)}{what}{' ' * max(room - len(what), 0)} {dial}{edge} ┃")
    lines.append(f"{edge}┗{'━' * (width - 2)}┛{ink.reset()}")
    return lines


def frame(rows: list[dict], width: int, now: float, color: bool) -> list[str]:
    ink = Ink(color)
    width -= 1  # never write the last column: some terminals wrap there
    busy = sum(r["status"] == "busy" for r in rows)
    need = sum(status_of(r)[0] == "NEEDS YOU" for r in rows)
    running = sum(not j["done"] for r in rows for j in r["jobs"])
    load = loadavg()
    title = "◢◤ SESSION GRID ◥◣ "
    sub = f"live claude code telemetry · {sum(r['tokens']['today'] for r in rows):,} tokens today"
    clock = f"{datetime.fromtimestamp(now):%H:%M:%S}"
    out = [f"{ink(CYAN, True)}{title}{ink(SLATE)}{clip(sub, width - len(title) - len(clock) - 1):<{width - len(title) - len(clock)}}"
           f"{ink(ICE, True)}{clock}{ink.reset()}"]
    level, _ = defcon([row_json(r, now) for r in rows])
    chips = [("DEFCON", level, RED_T if level == 1 else AMBER_T if level == 2 else MINT),
             ("SESSIONS", len(rows), CYAN), ("RUNNING", busy, CYAN), ("JOBS", running, CYAN),
             ("NEED YOU", need, RED_T if need else SLATE),
             ("LOAD", load, AMBER_T if load != "?" and float(load) > (os.cpu_count() or 1) else TEXT)]
    out.append("".join(f"{ink(SLATE)}▕ {ink(tone, True)}{val} {ink(SLATE)}{key} ▏" for key, val, tone in chips)
               + ink.reset())
    # scan line: a bright segment sweeps across a dim rule
    head = int(now * 40) % (width + 24) - 12 if color else -99
    out.append("".join((ink.blend(SLATE, CYAN, max(0.0, 1 - abs(x - head) / 12)) if abs(x - head) < 12
                        else ink(SLATE)) + "━" for x in range(width)) + ink.reset())
    for row in rows:
        out += card(row, width, now, color)
    if not rows:
        out.append(f"{ink(MAGENTA)}  ▸ NO ACTIVE SESSIONS DETECTED_{ink.reset()}")
    out.append(f"{ink(SLATE)}  q quit · reads $HOME/.claude files only, calls no model{ink.reset()}")
    return out


def timeline(row: dict, now: float) -> str:
    """The last hour, one letter a minute: w replying, j only a job running, y idle, - not started.

    A reply counts from its start to its end (turn_duration), so a long tool call with no records is working.
    """
    start, end = int((now - row["uptime"]) // 60), int(now // 60)
    spans = [(int(j["start"] // 60), int((j["start"] + j["elapsed"]) // 60)) for j in row["jobs"]
             if j["start"] and j["kind"] != "serve"]
    tr = row["tr"]
    replies = [(int(a // 60), int(b // 60)) for a, b in tr.turn_spans]
    if row["status"] == "busy" and tr.turn_start:
        replies.append((int(tr.turn_start // 60), end))
    out = ""
    for m in range(end - HOUR + 1, end + 1):
        if m < start:
            out += "-"
        elif m in tr.active or any(a <= m <= b for a, b in replies):
            out += "w"
        else:
            out += "j" if any(a <= m <= b for a, b in spans) else "y"
    return out


def meters(row: dict, strip: str) -> dict:
    """Four plain measures of how a session is doing; None where there is nothing to measure yet."""
    tr = row["tr"]
    lived = [c for c in strip if c != "-"]
    turns = tr.turn_durations
    pace = None
    if len(turns) >= 6:  # its last 5 replies against its own usual
        ratio = statistics.median(turns[-5:]) / statistics.median(turns)
        pace = "faster" if ratio < 0.8 else "slower" if ratio > 1.25 else "normal"
    return {"working": round(100 * sum(c == "w" for c in lived) / len(lived)) if lived else None,
            "shipped": len(tr.prs), "pace": pace}


def row_json(row: dict, now: float) -> dict:
    """One session for the web page: the fields a tile shows, already worded."""
    tr, jobs = row["tr"], row["jobs"]
    running = [j for j in jobs if not j["done"]]

    def when(job: dict) -> str:
        if job["kind"] == "watch":
            return "waiting for a job"
        if job["kind"] == "serve":
            return "running"
        if job["late"]:
            return f"{fmt_dur(job['elapsed'] - (job['typical'] or 0))} behind"
        if job["eta"] is not None:
            return f"~{fmt_dur(job['eta'])} left"
        if job["typical"]:  # past its usual, too few runs to call it late
            return f"usually {fmt_dur(job['typical'])}, now {fmt_dur(job['elapsed'])}"
        return f"first run · {fmt_dur(job['elapsed'])}"

    strip = timeline(row, now)
    full = tr.full or row.get("box_full") or DEFAULT_CONTEXT  # own auto-compact size, else this box's usual
    basis = "own" if tr.full else "box" if row.get("box_full") else "assumed"
    kind, what = asked(row)
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    plan = row.get("plan")
    return {
        "bot": "claude", "name": row["name"], "role": row["role"], "sid": row["sid"], "state": status_of(row)[0],
        "eta": row["eta"], "progress": row["progress"],
        "started": None if row.get("started_unknown") else now - row["uptime"],
        "uptime": "unknown" if row.get("started_unknown") else fmt_dur(row["uptime"]),
        "title": row_topic(row), "topic": row_topic(row), "next": next_step(tr.summary),
        "started_as": tr.title, "plan_title": row.get("plan_title", ""), "ask": what if kind == "you" else "",
        "ask_kind": ("prompt" if row["status"] == "waiting" else "command") if kind == "you" else None,
        "waits_on": what if kind == "session" else "",
        "for_you": what if kind == "next" else "", "warn": warnings(running, round(100 * tr.context / full)),
        "reply": tr.reply[-1500:], "summary_age": fmt_dur(now - tr.summary_ts) if tr.summary_ts else "",
        "tokens": row["tokens"], "now": clip(tr.now_text, 200),
        "ago": fmt_dur(now - tr.now_ts) if tr.now_ts else "", "waited": now - tr.now_ts if tr.now_ts else None,
        "summary": tr.summary or tr.last_prompt,
        "branch": tr.branch, "prs": list(tr.prs.values())[-5:],
        "prs_today": sorted(url for url, t in tr.pr_ts.items() if t >= midnight),
        "plan": plan and {k: plan[k] for k in ("name", "done", "total", "open", "left")},
        "finished": len(jobs) - len(running), "meters": meters(row, strip), "timeline": strip,
        "context": {"used": tr.context, "full": full, "pct": min(round(100 * tr.context / full), 100),
                    "basis": basis, "compactions": tr.compactions},
        "jobs": [{"kind": j["kind"], "label": j["label"] + (f" — {j['now']}" if j.get("now") else ""),
                  "watch": j["kind"] in QUIET, "late": bool(j["late"] or j.get("stalled")),
                  "stalled": bool(j.get("stalled")), "when": when(j), **({"type": j["type"]} if j.get("type") else {}),
                  "frac": j["elapsed"] / j["typical"] if j.get("typical") else None,
                  **({"runs": j.get("runs", 0)} if j["kind"] not in QUIET else {})} for j in running],
    }


def finish_order(rows: list[dict]) -> list[dict]:
    """Page rows in order of completion: NEEDS YOU first (longest ask first), then soonest done (behind
    counts as due now), then no estimate (longest wait first), idle last in the order they finished."""
    def key(r: dict) -> tuple:
        p = r["progress"]
        if r["state"] == "NEEDS YOU":
            return 0, -(r["waited"] or 0)
        if r["state"] == "IDLE":
            return 3, -(r["waited"] or 0)  # finished earliest first, the last one finished at the bottom
        if p["behind"] or p["left"] is not None:
            return 1, 0 if p["behind"] else p["left"]
        return 2, -(r["waited"] or 0)
    return sorted(rows, key=key)


def warnings(running: list[dict], context_pct: int) -> list[str]:
    """A session's yellow: only what the board measures, never what a reply says."""
    return [w for w, on in (("job behind schedule", any(j["late"] and not j.get("stalled") for j in running)),
                            ("helper silent 20m+", any(j.get("stalled") for j in running)),
                            ("context near full", context_pct >= 90)) if on]


def defcon(rows: list[dict]) -> tuple[int, str]:
    """The box's alert level from the page rows: 1 someone needs you ... 5 all quiet. Only 1 is yours to act on."""
    def n(count: int, word: str) -> str:
        return f"{count} {word}{'' if count == 1 else 's'}"
    need = sum(r["state"] == "NEEDS YOU" for r in rows)
    if need:
        return 1, f"{n(need, 'session')} need{'s' if need == 1 else ''} you"
    jobs = [j for r in rows for j in r["jobs"]]
    late = sum(j["late"] and not j.get("stalled") for j in jobs)
    silent = sum(bool(j.get("stalled")) for j in jobs)
    full = sum(r.get("context", {}).get("pct", 0) >= 90 for r in rows)  # it compacts soon and slows down
    warn = [f"{n(late, 'job')} behind schedule" if late else "", f"{n(silent, 'helper')} silent 20m+" if silent else "",
            f"{n(full, 'session')} near {'its' if full == 1 else 'their'} context limit" if full else ""]
    if any(warn):
        return 2, " · ".join(w for w in warn if w)
    if any(r["state"].startswith("WAITING") for r in rows):
        return 3, "waiting on jobs"
    if any(r["state"] == "RUNNING" for r in rows):
        return 4, "sessions working"
    return 5, "all quiet"


class WaitLog:
    """How long you took to answer: one JSON line per NEEDS YOU spell, from the ask to when it stopped asking.

    The start is the ask's own time (its reply's timestamp), so a board restart mid-wait loses nothing.
    """

    def __init__(self, path: Path | None):
        self.path, self.open, self.done = path, {}, []
        try:
            self.done = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError, AttributeError):
            pass

    def update(self, rows: list[dict], now: float) -> None:
        asking = {r["sid"]: r for r in rows if r["state"] == "NEEDS YOU"}
        for sid, r in asking.items():
            if sid not in self.open:
                self.open[sid] = {"sid": sid, "name": r["name"], "ask": str(r["ask"])[:240],
                                  "ask_kind": r.get("ask_kind") or "unknown",
                                  "start": now - (r["waited"] or 0)}
        for sid in [sid for sid in self.open if sid not in asking]:
            rec = self.open.pop(sid)
            rec.update(end=now, secs=round(now - rec["start"], 1))
            self.done.append(rec)
            if self.path:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(rec) + "\n")
                except OSError:
                    pass  # the board still shows; only the history is lost

    def stats(self, now: float) -> dict:
        midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        today = [r["secs"] for r in self.done if r["end"] >= midnight]
        return {"answered": len(today), "median": round(statistics.median(today)) if today else None,
                "longest": round(max(today)) if today else None, "waiting": len(self.open)}


class AlertLog:
    """Every warning spell, one JSON line each, so the day's stats show which warnings are worth having.

    A spell is one session holding one kind (a ``warn`` entry, or ``wait on job``) from first seen to gone. Spells
    open when the board restarts start again from then.
    """

    def __init__(self, path: Path | None):
        self.path, self.open, self.done = path, {}, []
        try:
            self.done = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError, AttributeError):
            pass

    @staticmethod
    def kinds(row: dict) -> dict[str, list[str]]:
        jobs = row.get("jobs", [])
        out = {}
        for w in row.get("warn", []):
            out[w] = ([j.get("type") or "agent" for j in jobs if j.get("stalled")] if w.startswith("helper")
                      else [j["label"] for j in jobs if j.get("late") and not j.get("stalled")] if w.startswith("job") else [])
        if row["state"] == "WAITING ON JOB":
            out["wait on job"] = [j["label"] for j in jobs]
        return out

    def update(self, rows: list[dict], now: float) -> None:
        seen = set()
        visible = {r["sid"] for r in rows}
        for r in rows:
            for kind, detail in self.kinds(r).items():
                key = (r["sid"], kind)
                seen.add(key)
                rec = self.open.setdefault(key, {"sid": r["sid"], "name": r["name"], "kind": kind, "start": now, "detail": []})
                rec["detail"] += [d for d in detail if d not in rec["detail"]]
        for key in [k for k in self.open if k not in seen]:
            rec = self.open.pop(key)
            rec.update(end=now, secs=round(now - rec["start"], 1),
                       end_reason="condition_cleared" if rec["sid"] in visible else "session_not_visible")
            self.done.append(rec)
            if self.path:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(rec) + "\n")
                except OSError:
                    pass  # the board still shows; only the history is lost

    def stats(self, now: float, waits: list[dict], rows: list[dict]) -> dict:
        """Today: per kind count / median / % followed by NEEDS YOU within 10 min, and where the time goes."""
        midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        done = [r for r in self.done if r["end"] >= midnight]
        hour = lambda t: str(datetime.fromtimestamp(t).hour)  # noqa: E731

        def to_you(r: dict) -> bool:
            return any(w["sid"] == r["sid"] and r["start"] <= w["start"] <= r["end"] + 600 for w in waits)
        by_kind = {}
        for kind in sorted({r["kind"] for r in done} | {r["kind"] for r in self.open.values()}):
            ended = [r for r in done if r["kind"] == kind]
            by_kind[kind] = {"count": len(ended) + sum(r["kind"] == kind for r in self.open.values()),
                             "median": round(statistics.median(r["secs"] for r in ended)) if ended else None,
                             "to_you": round(100 * sum(map(to_you, ended)) / len(ended)) if ended else None}

        def tally(kind: str) -> dict[str, int]:
            out: dict[str, int] = {}
            for r in done:
                if r["kind"] == kind:
                    for d in r["detail"]:
                        out[d] = out.get(d, 0) + 1
            return dict(sorted(out.items(), key=lambda kv: -kv[1])[:5])
        job_wait: dict[str, float] = {}
        for r in done:
            if r["kind"] == "wait on job":
                job_wait[hour(r["start"])] = job_wait.get(hour(r["start"]), 0) + r["secs"] / 60
        asks: dict[str, list[float]] = {}
        asks_by_kind: dict[str, list[float]] = {}
        for w in waits:
            if w["end"] >= midnight:
                asks.setdefault(hour(w["start"]), []).append(w["secs"])
                asks_by_kind.setdefault(w.get("ask_kind") or "unknown", []).append(w["secs"])
        endings: dict[str, dict[str, int]] = {}
        for rec in done:
            reason = rec.get("end_reason") or "unknown"
            counts = endings.setdefault(rec["kind"], {})
            counts[reason] = counts.get(reason, 0) + 1
        return {"by_kind": by_kind, "behind_by_job": tally("job behind schedule"),
                "silent_by_type": tally("helper silent 20m+"),
                "job_wait_by_hour": {h: round(m) for h, m in job_wait.items()},
                "asks_by_hour": {h: {"count": len(v), "median": round(statistics.median(v))} for h, v in asks.items()},
                "asks_by_kind": {kind: {"count": len(v), "median": round(statistics.median(v))}
                                  for kind, v in sorted(asks_by_kind.items())},
                "end_reasons": endings,
                "compactions": {r["name"]: r["context"]["compactions"] for r in rows if r.get("context", {}).get("compactions")}}


API_VERSION = 1  # /api is a contract: removing or renaming a key bumps it
LOOPBACK_HOST_RE = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", re.I)


def make_server(board: Board, port: int, log: Path | None = None, alerts_log: Path | None = None,
                interval: float = 2.0):
    """A loopback-only HTTP server: / is the page, /api the snapshot. WSL forwards it to Windows."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    page, lock, waits = Path(__file__).with_name("session-board.html"), threading.Lock(), WaitLog(log)
    alerts = AlertLog(alerts_log)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's name
            if not LOOPBACK_HOST_RE.match(self.headers.get("Host", "")):
                self.send_error(403)  # DNS rebinding: a foreign page whose name points at 127.0.0.1
                return
            if self.path.split("?")[0] == "/api":
                with lock:  # transcripts are read incrementally; one reader at a time
                    now = time.time()
                    rows = finish_order([row_json(r, now) for r in board.snapshot(now)] + board.codex_rows(now))
                    waits.update(rows, now)
                    stats = waits.stats(now)
                    alerts.update(rows, now)
                    day = alerts.stats(now, waits.done, rows)
                load = loadavg()
                level, why = defcon(rows)
                bot_stats = board.bots(rows, now)
                body = json.dumps({"api": API_VERSION, "now": now, "load": load, "cores": os.cpu_count(), "defcon": {"level": level, "why": why},
                                   "waits": stats, "alerts": day, "untested": untested_versions(board.versions),
                                   "bots": bot_stats, "codex_count": bot_stats["codex"]["sessions"], "sessions": rows}).encode()
                ctype = "application/json"
            elif self.path.split("?")[0] in ("/", "/index.html"):
                body = page.read_bytes().replace(b"const POLL_INTERVAL_MS = 2000;",
                                                 f"const POLL_INTERVAL_MS = {round(interval * 1000)};".encode())
                ctype = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            if not isinstance(sys.exc_info()[1], ConnectionError):  # the page gave up on a slow answer: no one's error
                super().handle_error(request, client_address)

    return Server(("127.0.0.1", port), Handler)


def untested_versions(versions) -> list[str]:
    """Claude Code versions newer than TESTED: its files may have changed under the board."""
    def mm(v: str) -> tuple[int, int]:
        m = re.match(r"(\d+)\.(\d+)", v)
        return (int(m[1]), int(m[2])) if m else (0, 0)
    return sorted(v for v in versions if mm(v) > TESTED)


def demo_box(root: Path) -> Path:
    """A made-up Claude Code dir under ``root``: one session in each state, a late job, a nearly full context.

    Every entry carries this process's pid, so the board reads them as live while it runs.
    """
    now, claude, pid = time.time(), root / "claude", os.getpid()
    try:
        start = (PROC / "self" / "stat").read_text().rsplit(")", 1)[-1].split()[19]
    except OSError:
        start = None

    def iso(t: float) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))

    def say(t: float, text: str, used: int = 40_000) -> dict:
        return {"type": "assistant", "timestamp": iso(t), "message": {"id": f"m{t}", "content": [
            {"type": "text", "text": text}], "usage": {"input_tokens": 900, "cache_read_input_tokens": used,
                                                         "output_tokens": 700}}}

    def job(n: int, t: float, desc: str, cmd: str, end: float | None = None, tool: str = "Bash") -> list[dict]:
        recs = [{"type": "assistant", "timestamp": iso(t), "message": {"content": [
                    {"type": "tool_use", "id": f"u{n}", "name": tool,
                     "input": {"command": cmd, "description": desc, "run_in_background": True}}]}},
                {"type": "user", "timestamp": iso(t + 1), "toolUseResult": {"backgroundTaskId": f"j{n}"},
                 "message": {"content": [{"type": "tool_result", "tool_use_id": f"u{n}"}]}}]
        if end:
            recs.append({"type": "queue-operation", "operation": "enqueue", "timestamp": iso(end),
                         "content": f"<task-notification><task-id>j{n}</task-id><status>completed</status>"})
        return recs

    def title(text: str, summary: str = "") -> list[dict]:
        return [{"type": "ai-title", "aiTitle": text}] + (
            [{"type": "system", "subtype": "away_summary", "content": summary, "timestamp": iso(now - 120)}]
            if summary else [])

    history = [r for i in range(4) for r in job(i, now - 3600 * (5 - i), f"test run {i + 1}", "pytest tests",
                                                 now - 3600 * (5 - i) + 300)]
    sessions = [
        ("api-gateway", "waiting", 2, {"waitingFor": "approve: run the database migration"},
         title("Add rate limits to the API gateway", "Adding per-key rate limits to the gateway; the migration runs next.")
         + [say(now - 90, "The migration is ready. Approve the prompt to run it.")]),
        ("test-suite", "shell", 6, {}, title("Speed up the test suite", "Cutting the suite time in half; comparing against the last runs.")
         + history + job(9, now - 1500, "full test run", "pytest tests")
         + [say(now - 1490, "Running the full suite to compare with the last four runs.")]),
        ("docs-site", "busy", 3, {}, title("Rewrite the getting-started guide")
         + [say(now - 20, "Rewriting the install section with the new commands.", used=930_000)]),
        ("ci-watch", "shell", 1, {}, title("Fix the flaky upload step")
         + job(10, now - 400, "watch the CI run", "until ci-status --done; do sleep 30; done", tool="Monitor")
         + [say(now - 395, "Pushed the fix; watching the CI run.")]),
        ("release-notes", "idle", 4, {}, title("Draft the release notes")
         + [say(now - 900, "Done: release notes drafted in CHANGELOG.md. Nothing for you to do.")]),
    ]
    (claude / "sessions").mkdir(parents=True)
    proj = claude / "projects" / "demo"
    proj.mkdir(parents=True)
    for i, (name, status, hours, extra, recs) in enumerate(sessions):
        sid = f"{i + 1:08x}-0000-4000-8000-00000000demo"
        reg = {"pid": pid, "sessionId": sid, "name": name, "status": status, "cwd": str(root),
               "startedAt": int((now - 3600 * hours) * 1000), **({"procStart": start} if start else {}), **extra}
        (claude / "sessions" / f"demo-{i}.json").write_text(json.dumps(reg))
        (proj / f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    codex, cid = root / "codex", "0c0dec00-0000-7000-8000-00000000demo"
    day = codex / "sessions" / time.strftime("%Y/%m/%d", time.localtime(now))
    day.mkdir(parents=True)
    week = {"used_percent": 37.0, "window_minutes": 10080, "resets_at": int(now) + 3 * 86400}
    (day / f"rollout-demo-{cid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        {"timestamp": iso(now - 2400), "type": "session_meta", "payload": {"id": cid, "cwd": str(root / "parser")}},
        {"timestamp": iso(now - 2390), "type": "event_msg", "payload": {"type": "user_message", "message": "Split the parser into two passes"}},
        {"timestamp": iso(now - 2380), "type": "event_msg", "payload": {"type": "task_started"}},
        {"timestamp": iso(now - 45), "type": "event_msg", "payload": {"type": "agent_message", "message": "Moving the tokenizer into its own pass."}},
        {"timestamp": iso(now - 40), "type": "event_msg", "payload": {"type": "token_count", "info": {
            "last_token_usage": {"input_tokens": 88_000}, "model_context_window": 258_400},
            "rate_limits": {"primary": week, "secondary": None, "plan_type": "demo"}}}]))
    (codex / "session_index.jsonl").write_text(json.dumps({"id": cid, "thread_name": "Split the parser"}) + "\n")
    (root / "state").mkdir()
    (root / "state" / "claude-limits.json").write_text(json.dumps({
        "five_hour": {"used_percentage": 64, "resets_at": int(now) + 5400},
        "seven_day": {"used_percentage": 41, "resets_at": int(now) + 4 * 86400}}))
    return claude


DOCTOR_KEYS = ("pid", "sessionId", "procStart", "status", "startedAt", "name", "cwd", "version")


def doctor(claude_dir: Path, codex_dir: Path | None = None, limits_file: Path | None = None) -> list[str]:
    """What the board finds to read: counts and key names only, never a value, path or line of text."""
    regs = sorted((claude_dir / "sessions").glob("*.json"))
    board = Board(claude_dir, None, codex_dir, limits_file)
    live = board.live_sessions()
    logs = [p for s in live for p in claude_dir.glob(f"projects/*/{s.get('sessionId', '')}.jsonl")]
    notices = 0
    for log in logs:
        try:
            notices += sum(b"<task-id>" in line for line in log.read_bytes().splitlines())
        except OSError:
            pass
    out = [f"claude dir: {'found' if claude_dir.is_dir() else 'missing'}", f"registry files: {len(regs)}",
           f"live sessions: {len(live)}", f"transcripts: {len(logs)}",
           f"sub-agent records: {len(list(claude_dir.glob('projects/*/*/subagents/*.meta.json')))}",
           f"task notifications: {notices}", f"process table: {'/proc' if (PROC / 'self').exists() else 'signal 0'}",
           "registry keys in live sessions:"]
    for key in DOCTOR_KEYS:
        have = sum(key in s for s in live)
        out.append(f"  {key}: {have} of {len(live)}" if have else f"  {key}: missing")
    versions, newer = sorted(board.versions), untested_versions(board.versions)
    out.append(f"Claude Code versions: {', '.join(versions) or 'none recorded'} (checked against {TESTED[0]}.{TESTED[1]}.x)")
    now = time.time()
    codex_rows = board.codex_rows(now)
    bot_stats = board.bots(codex_rows, now)
    out.extend((f"codex sessions dir: {'found' if codex_dir and (codex_dir / 'sessions').is_dir() else 'missing'}",
                f"codex live sessions: {len(codex_rows)}",
                f"codex limits windows: {len(bot_stats['codex']['limits'])}",
                f"Claude limits file: {'found' if limits_file and limits_file.is_file() else 'missing'}",
                f"Claude limits windows: {len(bot_stats['claude']['limits'])}"))
    if newer:
        out.append(f"newer than checked: {', '.join(newer)}; if a number looks wrong, attach this report")
    return out


def default_claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def poll_interval(value: str) -> float:
    """A safe polling interval for both the web page and terminal view."""
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("interval must be 0.25 to 3600 seconds") from exc
    if not 0.25 <= seconds <= 3600:
        raise argparse.ArgumentTypeError("interval must be 0.25 to 3600 seconds")
    return seconds


def configure(path: Path | None) -> dict:
    """Load a TOML config (none: the neutral defaults) and set the patterns it names.

    Relative paths resolve against the main checkout holding the config, so a worktree copy finds the same files.
    """
    global CMD_RE, PLAN_RE
    cfg = tomllib.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else {}
    asks = [str(a) for a in cfg.get("ask_commands", [])]
    CMD_RE = re.compile(r"^\s*(" + "|".join(f"(?:{a})" for a in asks) + r")\s*$", re.M) if asks else NEVER
    WATCH_COMMANDS[:] = [str(w) for w in cfg.get("watch_commands", [])]
    PLAN_DIRS[:] = [str(d).strip("/") for d in cfg.get("plan_dirs", [])]
    PLAN_RE = (re.compile(rb"(?:" + b"|".join(re.escape(d.encode()) for d in PLAN_DIRS) + rb")/([\w.-]+\.md)")
               if PLAN_DIRS else re.compile(rb"(?!)"))
    base = main_root(path.resolve().parent) if path and path.is_file() else Path.cwd()
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "bot-battle-station"
    return {"roster": base / cfg["roster"] if cfg.get("roster") else None,
            "state_dir": base / Path(cfg["state_dir"]).expanduser() if cfg.get("state_dir") else state,
            "claude_limits": base / Path(cfg["claude_limits"]).expanduser() if cfg.get("claude_limits") else None,
            "plan_dirs": list(PLAN_DIRS)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--once", action="store_true", help="print one plain frame and exit")
    ap.add_argument("--tui", action="store_true", help="full-screen terminal board instead of the web page")
    ap.add_argument("--port", type=int, default=8765, help="web page port on 127.0.0.1 (default 8765)")
    ap.add_argument("--interval", type=poll_interval, default=2.0, help="web and terminal refresh seconds, 0.25–3600 (default 2)")
    ap.add_argument("--claude-dir", type=Path, default=default_claude_dir(),
                    help="Claude Code's config dir (default: $CLAUDE_CONFIG_DIR, else $HOME/.claude)")
    ap.add_argument("--codex-dir", type=Path, default=Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"),
                    help="Codex's home dir, read for its sessions and limits (default: $CODEX_HOME, else $HOME/.codex)")
    ap.add_argument("--config", type=Path, default=Path(__file__).resolve().with_name("session-board.toml"),
                    help="TOML config: this box's asks, watchers, plans, roster, state dir (default: beside this file)")
    ap.add_argument("--roster", type=Path, default=None)
    ap.add_argument("--waits-log", type=Path, default=None,
                    help="where each wait on you is logged (default: waits.jsonl in the config's state_dir)")
    ap.add_argument("--alerts-log", type=Path, default=None,
                    help="where each warning spell is logged (default: alerts.jsonl next to the waits log)")
    ap.add_argument("--demo", action="store_true", help="show made-up sessions in every state, not this box's")
    ap.add_argument("--doctor", action="store_true", help="print what the board finds to read (no values) and exit")
    args = ap.parse_args(argv)
    if args.doctor:
        try:
            cfg = configure(args.config)
        except tomllib.TOMLDecodeError as exc:
            print(f"session board: invalid config {args.config}: {exc}", file=sys.stderr)
            return 2
        print("\n".join(doctor(args.claude_dir, args.codex_dir,
                               cfg["claude_limits"] or cfg["state_dir"] / "claude-limits.json")))
        return 0
    if args.demo:
        import tempfile
        global proc_children, loadavg, codex_open_files
        with tempfile.TemporaryDirectory(prefix="board-demo-") as demo_dir:
            root = Path(demo_dir)
            cfg, args.claude_dir, args.codex_dir = configure(None), demo_box(root), root / "codex"
            cfg["state_dir"], cfg["roster"] = root / "state", None
            codex_open_files = lambda: None  # noqa: E731 - no process holds the demo's rollout; its age says live
            proc_children = lambda: None  # noqa: E731 - the demo's jobs have no processes; trust the transcript
            loadavg = lambda: DEMO_LOAD  # noqa: E731 - a screenshot shows a made-up load, not this box's
            return run_board(args, cfg)
    else:
        try:
            cfg = configure(args.config)
        except tomllib.TOMLDecodeError as exc:
            print(f"session board: invalid config {args.config}: {exc}", file=sys.stderr)
            return 2
    return run_board(args, cfg)


def run_board(args: argparse.Namespace, cfg: dict) -> int:
    """Run the selected board view while any demo fixture directory is still open."""
    board = Board(args.claude_dir, args.roster or cfg["roster"], args.codex_dir,
                  cfg["claude_limits"] or cfg["state_dir"] / "claude-limits.json")
    args.waits_log = args.waits_log or cfg["state_dir"] / "waits.jsonl"
    args.alerts_log = args.alerts_log or cfg["state_dir"] / "alerts.jsonl"
    if args.once or (args.tui and not sys.stdout.isatty()):
        width = shutil.get_terminal_size((120, 40)).columns
        print("\n".join(frame(board.snapshot(time.time()), width, time.time(), False)))
        return 0
    if not args.tui:
        server = make_server(board, args.port, args.waits_log, args.alerts_log, args.interval)
        print(f"session board: http://localhost:{server.server_address[1]}/  (Ctrl+C stops)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    import termios
    import tty
    fd, saved = sys.stdin.fileno(), None
    try:
        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    except termios.error:
        pass
    sys.stdout.write("\033[?1049h\033[?25l")
    rows, fetched = [], 0.0
    try:
        while True:
            now = time.time()
            if now - fetched >= args.interval:
                rows, fetched = board.snapshot(now), now
            size = shutil.get_terminal_size((120, 40))
            lines = frame(rows, size.columns, now, True)[: size.lines]
            sys.stdout.write("\033[H" + "\n".join(f"{ln}\033[K" for ln in lines) + "\033[J")
            sys.stdout.flush()
            if select.select([sys.stdin], [], [], 0.08)[0] and sys.stdin.read(1).lower() == "q":
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()
        if saved is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)


if __name__ == "__main__":
    sys.exit(main())

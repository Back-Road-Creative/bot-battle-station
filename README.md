# bot-battle-station

A live wall screen for every Claude Code session on your machine: which one needs you,
which are working, which are waiting on a test run or CI, and when each should be done.
It spends no tokens. It reads only the files Claude Code already writes.

![The board in --demo mode: five made-up sessions, one asking for approval](https://github.com/Back-Road-Creative/bot-battle-station/releases/latest/download/demo.png)

*The screenshot is `--demo`: invented sessions, no real data.*

> Not affiliated with or endorsed by Anthropic. "Claude" and "Claude Code" are Anthropic's
> names; this is an independent tool that reads the local files Claude Code writes.

## Try it

Python 3.11 or newer, standard library only. Linux and WSL are what it is built and tested on;
macOS works without the `/proc` checks (below). Windows-native Claude Code is not covered.

```bash
git clone https://github.com/Back-Road-Creative/bot-battle-station.git
cd bot-battle-station
python3 bin/session-board.py --demo     # made-up sessions in every state: a first look
python3 bin/session-board.py            # your sessions: open http://localhost:8765
```

```text
python3 bin/session-board.py            # web page on 127.0.0.1:8765 (--port to change)
python3 bin/session-board.py --tui      # full-screen terminal board, q quits
python3 bin/session-board.py --once     # one plain text frame (pipes, tests)
python3 bin/session-board.py --demo     # five invented sessions in a temp dir
python3 bin/session-board.py --doctor   # what it found to read: counts and key names, no values
```

Other options: `--interval` sets the refresh time in seconds; `--claude-dir` and
`--codex-dir` select the session directories; `--config` selects the TOML file;
`--roster` selects the session-name list; and `--waits-log` and `--alerts-log`
select the local JSONL logs. Run `python3 bin/session-board.py --help` for defaults.

Under WSL, a Windows browser opens it at `http://localhost:8765`.

## What it shows

One tile per live session, NEEDS YOU first, then in the order they should finish. A tile has
its topic (Claude Code's own away summary, else its plan's heading, else its title), a ring with
% done and minutes left, what it asks or is doing now, its next plan steps, its running jobs with
bars against their usual length, four dials (share of the last hour spent working, PRs it
linked, pace against its own usual, tokens today), an hour strip and a context gauge. Click a
tile or press 1-9 for the full view; `?` opens a legend that says how every number is computed.
Tiles reflow on narrow screens, and the board respects the system's reduced-motion setting.

| State | When | What you do |
|---|---|---|
| NEEDS YOU | Claude Code says the session is `waiting` (a permission prompt, a question, a plan to approve), or a line of its last reply matches one of your `ask_commands` | Open the session and answer its pending request. |
| RUNNING | Claude Code says `busy` | Let it work. |
| WAITING ON JOB | a background command, watcher or helper agent is still running | Check the job if its usual time has passed. |
| IDLE | anything else | Open it if you expected work to continue. |

The header carries a DEFCON ladder:

| DEFCON | When | The page does |
|---|---|---|
| 1 | a session NEEDS YOU | full-screen red takeover, a siren once armed, a desktop notification |
| 2 | a job is behind its usual run (past the 80th percentile of 4+ finished runs), a helper agent silent 20 min, a context 90 % full, or the board itself offline | amber |
| 3 | sessions wait on jobs | calm |
| 4 | sessions are working | calm |
| 5 | all quiet | calm |

ACKNOWLEDGE (or Esc) clears the takeover until another session starts asking; `/?ack` opens
with the asks already up acknowledged. Browsers play no sound before a click, so the siren is off
until you press ARM SIREN once.

ETAs are medians of what already finished on your machine: a helper agent against finished
agents of its type, a test run against finished runs of its kind, a plan against the session's
own pace per step. With fewer than 4 finished runs a job is never called behind.

A plan is a Markdown file in one of your `plan_dirs` that the session's replies name at least 3
times; its steps are the rows of a table with a Status column, or `- [ ]` / `- [x]` items.

## Configure it

Without a config the board is neutral: only Claude Code's own prompt state is red, only the
Monitor tool counts as a watcher, no plans are read. To fit your own workflow, put a
`session-board.toml` beside the script (or pass `--config`):

Relative `roster` and `state_dir` paths in that file resolve from the main checkout
holding the config, even when the board runs from a worktree. Without a
`state_dir` setting, local logs use `$XDG_STATE_HOME/bot-battle-station`, or
`~/.local/state/bot-battle-station` when that variable is unset.

```toml
# a reply line matching one of these means the session waits on you to run something
ask_commands = ['sudo\s.+', '.*\bdeploy-now\b.*']
# commands that only wait for something else (CI, a deploy): no ETA, never "behind"
watch_commands = ["waitfor"]
# folders, under each session's working directory, where your sessions keep plan files
plan_dirs = ["plans"]
# optional, relative to the git checkout holding this file: session names
# (tab-separated: session id or its first characters, name, role) and where the waits and alerts logs go
# roster = "session-roster.tsv"
# state_dir = "state"
# claude_limits = "state/claude-limits.json"
```

| Key | Default |
|---|---|
| `ask_commands` | none |
| `watch_commands` | none (the Monitor tool always counts) |
| `plan_dirs` | none |
| `roster` | names come from Claude Code's registry |
| `state_dir` | `$XDG_STATE_HOME/bot-battle-station`, else `~/.local/state/bot-battle-station` |
| `claude_limits` | `claude-limits.json` in `state_dir` |

Claude Code's files are read from `--claude-dir`, default `$CLAUDE_CONFIG_DIR`, else
`~/.claude`.

## Codex sessions and usage limits

Codex sessions get tiles too, marked CODEX (Claude Code's are marked CLAUDE). A Codex session is
live while a `codex` process holds its rollout file open (`--codex-dir`, default `$CODEX_HOME`,
else `~/.codex`); without `/proc`, while its rollout was written in the last 10 minutes. Its
helper threads (reviews, sub-agents) are not tiles. A Codex tile is RUNNING between Codex's own
turn-start and turn-end records, else IDLE; it shows the thread name, its last message and its
context use. The terminal views (`--tui`, `--once`) show Claude Code sessions only.

A strip under the header shows, per bot, its live sessions and each usage window (5-hour,
weekly) with % used and time to reset. Codex writes its limits into its own session files, so
they appear on their own. Claude Code writes its limits nowhere on disk; it hands them only to
your status line command, as `rate_limits` on its stdin. To show them, add one line to your
status line script, which already has that JSON in a variable (`$input` here). It writes only when
Claude Code sent limits, and swaps the file in whole, so the board never reads a half-written one:

```bash
d=~/.local/state/bot-battle-station; l=$(printf '%s' "$input" | jq -c '.rate_limits // empty' 2>/dev/null); [ -n "$l" ] && mkdir -p "$d" && printf '%s\n' "$l" > "$d/claude-limits.json.tmp" && mv "$d/claude-limits.json.tmp" "$d/claude-limits.json"
```

Without that file the strip says so. It shows "read … ago" when a file is over an hour old. A
window is left out when its percentage is not a number or its reset time has passed (the file is
stale); a percentage outside 0-100 is shown clamped.

## A wall screen on Windows

`bin/session-board-kiosk.ps1` starts the board in WSL if it is not answering, then opens it as
a full-screen app window on your second monitor (`-Monitor N` picks another), in its own
Edge/Chrome profile with sound allowed. Install it once from WSL, in the checkout; it adds a
Startup shortcut so it runs at every login:

```bash
powershell.exe -ExecutionPolicy Bypass -File "$(wslpath -w bin/session-board-kiosk.ps1)" -Install -Repo "$PWD"
```

`-User` (default: the distro's default user) and `-Command` (default
`python3 bin/session-board.py`) are there if yours differ.

## Privacy and safety

- **No model tokens, outbound requests, or subprocesses.** It serves the page
  over a loopback HTTP connection and reads `~/.claude/sessions/*.json`, the session
  transcripts and the helper-agent metadata, and only the bytes appended since the last poll;
  for Codex, the end of each live rollout file (re-read only when it changes) and its thread
  names, never its `auth.json`.
  `tests/test_session_board.py::test_spends_no_tokens` holds that.
- **Served on 127.0.0.1 only**, and only to a request whose `Host` is `localhost`, `127.0.0.1`
  or `[::1]`, so a web page whose DNS name flips to 127.0.0.1 cannot read your transcripts.
- **Transcript text is untrusted.** The page writes every value with `textContent` and makes no
  outside requests.
- **Logs stay local**: one JSON line per NEEDS YOU spell and per warning, in `state_dir`.
- **`--doctor`** prints counts, registry key names and Claude Code versions, never a path, name,
  id or text, so its output can go in a bug report.

## Compatibility

It reads Claude Code's internal files, which have no published format and can change. The board
was checked against Claude Code 2.1.x; a newer version is named in `--doctor`, in `/api`
(`untested`) and in an amber line on the page. If a number looks wrong after an update, open an
issue with the `--doctor` output. Without `/proc` (macOS) a session counts as live while its pid
answers signal 0, and the "ended without notice" check for killed background jobs is off.

`/api` returns JSON with `"api": 1`; a removed or renamed key bumps it.

## Development

```bash
python3 -m pip install pytest
python3 -m pytest -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md). MIT licensed, see [LICENSE](LICENSE).

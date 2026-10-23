# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Changed

- Clarify command options, local state paths, board actions, and the loopback
  server's security scope in the documentation.
- Show the number of finished runs behind each job's ETA in its tooltip; say
  when a job has no run history and no ETA yet.
- Move daily history onto the board and group answered asks by source and
  warnings by observed ending reason, preserving older records as unknown.
- Explain mixed Claude Code and Codex counts in the header and legend, show
  total context in use, and count duplicate session records only once.
- Apply `--interval` to web polling as well as the terminal view, reject unsafe
  interval values, and include Codex and limits source counts in `--doctor`.
- Add a bounded `--write-limits` status-line helper that atomically saves only
  Claude's supported usage-window fields in a private file.
- Keep board tiles readable on narrow screens and under reduced motion, with
  clearer muted text and stale-state styling.
- Let keyboard users open tiles and requests and keep focus during refreshes;
  keep long requests inside the phone-width alert banner.
- Clarify benign exception handling and tuple pairing in the board internals.

### Fixed

- Bound ask text saved in local wait history to a 240-character preview.
- Show unknown start times honestly, expand configured home paths, and report
  malformed TOML without a traceback. Ignore unrelated empty `.git` directories
  when locating a config checkout. Add a `--version` CLI option.
- Remove temporary demo session files when the board exits.
- Validate kiosk launcher arguments, identify the board before reusing a local
  server, and provide an uninstall option without disabling execution policy.

### Tests

- Keep public test fixtures self-contained and clarify the optional Node render check.

## 0.2.0

### Added

- Codex sessions: a tile per live Codex session, marked CODEX (Claude Code's are marked
  CLAUDE), read from Codex's own session files (`--codex-dir`).
- A per-bot strip under the header: live sessions per bot and each usage window with % used
  and time to reset. Codex's come from its session files; Claude's from a file your status line
  writes (`claude_limits`; README). `/api` gains `bots` and a `bot` key on every session.

### Fixed

- Usage limits the board can't trust no longer break `/api` or show on the page: a window
  whose percentage is not a number, or whose reset time has passed, is left out; a percentage
  outside 0-100 is clamped. The README's status-line line writes only when Claude Code sent
  limits, and swaps the file in whole.
- `SHA256SUMS` on a release names the files as they download (`session-board.py`,
  not `bin/session-board.py`), so `sha256sum -c SHA256SUMS` works. The release
  folder is built by `scripts/release-dist.sh`, which CI also runs on every pull
  request, so a broken checksum file fails CI instead of a release.

## 0.1.0

### Added

- First public release: the web board (`/api` version 1), `--tui`, `--once`,
  `--demo` and `--doctor`, the TOML config (`ask_commands`, `watch_commands`,
  `plan_dirs`, `roster`, `state_dir`) and the Windows kiosk script.

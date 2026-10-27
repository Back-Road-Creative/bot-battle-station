# Security Policy

## Supported versions

Only the latest released tag receives fixes.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub private
vulnerability reporting: the **Security** tab of this repository, then
**Report a vulnerability**
(<https://github.com/Back-Road-Creative/bot-battle-station/security/advisories/new>).
Do **not** open a public issue for a security report.

Please include the affected version or commit, a description of the issue and
its impact, and reproduction steps.

## What to expect

- Coordinated disclosure: we agree a timeline with you before any public
  write-up, and credit reporters who want it.

## Scope and threat model

In scope: `bin/session-board.py`, `bin/session-board.html`,
`bin/session-board-kiosk.ps1` and the workflows under `.github/workflows/`.

The board reads your Claude Code transcripts, which can hold anything a session
saw: code, command output, secrets pasted into a prompt. So it:

- listens on 127.0.0.1 only, and answers only a request whose `Host` header is
  `localhost`, `127.0.0.1` or `[::1]` (a DNS-rebinding guard);
- treats transcript text as untrusted, writing it into the page with
  `textContent` only;
- serves a loopback HTTP server and makes no outbound network request. The
  board itself (`bin/session-board.py`) starts no subprocess, on any platform;
  a test (`test_spends_no_tokens`) checks its source for that.

It reads your Claude Code and Codex session files and writes only its own files to `state_dir`: logs (one JSON line per NEEDS YOU
spell and per warning, created owner-only) and, if your status line feeds it,
`claude-limits.json`.

The optional Windows kiosk launcher, `bin/session-board-kiosk.ps1`, is separate
and does start processes. It only runs when you run it (or, if you used
`-Install`, at each Windows login through a Startup shortcut it creates). It:

- asks `http://127.0.0.1:<port>/api` whether the board is already answering
  (a loopback request, nothing leaves the machine);
- if not, starts one hidden `wsl.exe` process that runs the board command you
  gave (default `python3 bin/session-board.py`) in the WSL folder you gave;
  the command and folder are checked against a short allow-list of characters,
  so shell syntax is not accepted;
- starts one Edge or Chrome window (`--app`, full screen) at the board's
  loopback address, using its own profile folder under
  `%LOCALAPPDATA%\SessionBoard\browser`;
- with `-Install` or `-Uninstall`, copies or removes its own script and the
  Startup shortcut in `%LOCALAPPDATA%\SessionBoard` and your Startup folder.

It makes no other network request and does not run on Linux or macOS.

A way for another web page, another local user, or text inside a transcript to
read the board's data, run script in the page, or make the board act is in
scope. Anything already able to read your Claude Code directory directly is
out of scope, as are bugs in Claude Code itself (report those to its
maintainers).

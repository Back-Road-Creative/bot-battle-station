# Security Policy

## Supported versions

Only the latest released tag receives fixes.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub's
**Security → Report a vulnerability** tab on this repository. Do **not** open a
public issue for a security report.

Please include the affected version or commit, a description of the issue and
its impact, and reproduction steps.

## What to expect

- Acknowledgement within 5 business days.
- An initial assessment within 10 business days.
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
- makes no network request and starts no process.

A way for another web page, another local user, or text inside a transcript to
read the board's data, run script in the page, or make the board act is in
scope. Anything already able to read your Claude Code directory directly is
out of scope, as are bugs in Claude Code itself (report those to its
maintainers).

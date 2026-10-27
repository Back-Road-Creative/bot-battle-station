# Contributing to bot-battle-station

Thanks for your interest.

## Reporting

- **Bugs:** open an issue with the output of `python3 bin/session-board.py --doctor`
  (counts and key names only, safe to paste), your Claude Code version, what you
  expected and what you saw. Never paste transcript text or a screenshot of your
  real sessions; reproduce with `--demo` if you can.
- **Security vulnerabilities:** do **not** open a public issue; follow
  [SECURITY.md](SECURITY.md).

## Development

Python 3.11 or newer, standard library only; `pytest` is the only dev dependency.

```bash
python3 -m pip install pytest
python3 -m pytest -q
```

The tests build made-up Claude Code directories in a temp dir; they need no
Claude Code install and no network. CI runs them on Python 3.11, 3.12 and 3.13.
Node, when present, runs the page's own rendering code against a local stub DOM;
the suite skips that check when Node is absent.

## What a change should look like

- **Tests first for a bug fix.** Add a test that fails before your change and
  passes after. Never delete or weaken a test to make the suite pass.
- **No tokens, no network, no subprocess in the board.** `bin/session-board.py`
  only reads files (the Windows kiosk script is the one place that starts
  processes).
  `test_spends_no_tokens` holds that; a change that needs one of them needs a
  strong argument in the PR.
- **Invented data only.** Test fixtures, docs and screenshots use made-up
  sessions (`--demo`), never a real transcript.
- **Docs land in the same change.** A new flag, config key or `/api` field
  updates the README in the same commit. Removing or renaming an `/api` key
  bumps `API_VERSION`.
- **Pin GitHub Actions to a commit SHA**, never a moving reference.

## Commits and pull requests

Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`),
one logical change per PR, with the test output in the description. Add an
entry under `## Unreleased` in [CHANGELOG.md](CHANGELOG.md).

## Conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

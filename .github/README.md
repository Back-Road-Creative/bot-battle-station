# Workflow checks

CI tests Python 3.11 through 3.13 on Ubuntu and macOS. A Windows hosted job
installs and uninstalls the kiosk launcher, checking its startup shortcut and
installed copy against a local, invented board API. It does not cover WSL
startup, physical monitor placement, or sound; those still need a device check.
Every job has a time
limit, and a newer run for the same branch cancels an older CI run. Release
jobs do not cancel one another and retain write permission only on the release
job. The reusable leak scan is pinned to an immutable commit; Dependabot
checks GitHub Actions references weekly.

Release archives already include `SHA256SUMS`, checked by
`tests/test_release_dist.py` on systems with GNU coreutils.

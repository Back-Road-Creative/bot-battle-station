#!/usr/bin/env bash
# Build the folder a GitHub release ships: the board files flat (release
# downloads have no bin/), any extra files given (the release adds demo.png),
# and SHA256SUMS naming each file as a downloader sees it, checked here with
# sha256sum -c. release.yml uploads this folder; tests/test_release_dist.py
# runs it on every PR, so a wrong checksum file fails CI, not a release.
#
#   bash scripts/release-dist.sh <out-dir> [extra-file ...]
set -euo pipefail
out=${1:?usage: release-dist.sh <out-dir> [extra-file ...]}
shift
mkdir -p "$out"
cp bin/session-board.py bin/session-board.html bin/session-board-kiosk.ps1 "$@" "$out"/
names=(session-board.py session-board.html session-board-kiosk.ps1)
for f in "$@"; do names+=("$(basename "$f")"); done
cd "$out"
sha256sum "${names[@]}" > SHA256SUMS
sha256sum -c SHA256SUMS

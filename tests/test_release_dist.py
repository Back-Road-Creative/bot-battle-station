"""scripts/release-dist.sh: the release folder a downloader gets, checked on every PR."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOARD = ["session-board.py", "session-board.html", "session-board-kiosk.ps1"]

pytestmark = pytest.mark.skipif(not shutil.which("sha256sum"), reason="sha256sum (GNU coreutils) not installed")


def dist(tmp_path: Path, *extra: Path) -> Path:
    out = tmp_path / "dist"
    subprocess.run(["bash", str(ROOT / "scripts/release-dist.sh"), str(out), *map(str, extra)], check=True, cwd=ROOT)
    return out


def test_sums_name_the_files_as_they_download(tmp_path):
    shot = tmp_path / "demo.png"
    shot.write_bytes(b"not really a png")
    out = dist(tmp_path, shot)
    names = [line.split()[1] for line in (out / "SHA256SUMS").read_text().splitlines()]
    assert names == [*BOARD, "demo.png"]
    assert sorted(p.name for p in out.iterdir()) == sorted([*names, "SHA256SUMS"])


def test_a_downloader_can_check_them_in_a_fresh_folder(tmp_path):
    out = dist(tmp_path)
    fresh = tmp_path / "downloads"
    shutil.copytree(out, fresh)
    check = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=fresh, capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
    assert check.stdout.count(": OK") == len(BOARD)

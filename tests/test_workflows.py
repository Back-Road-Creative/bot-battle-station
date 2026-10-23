"""CI and release workflow contracts for the supported platforms."""

from pathlib import Path
import re


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def test_ci_exercises_linux_and_macos_with_bounded_jobs():
    ci = (WORKFLOWS / "ci.yml").read_text()
    assert "ubuntu-latest" in ci and "macos-latest" in ci
    assert "timeout-minutes:" in ci
    assert "concurrency:" in ci


def test_release_and_leak_scan_have_bounded_immutable_workflows():
    release = (WORKFLOWS / "release.yml").read_text()
    leak_scan = (WORKFLOWS / "leak-scan.yml").read_text()
    assert "timeout-minutes:" in release
    assert "concurrency:" in release
    assert "@main" not in leak_scan
    assert re.search(r"leak-scan\.yml@[0-9a-f]{40}\b", leak_scan)


def test_dependabot_checks_action_references():
    config = Path(__file__).resolve().parents[1] / ".github/dependabot.yml"
    assert "github-actions" in config.read_text()

"""Keep operator-facing instructions aligned with the shipped board."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_lists_every_cli_option():
    tree = ast.parse((ROOT / "bin/session-board.py").read_text())
    flags = {
        arg.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("--")
    }
    readme = (ROOT / "README.md").read_text()
    assert not {flag for flag in flags if flag not in readme}


def test_changelog_has_unreleased_section_for_next_changes():
    assert "## Unreleased" in (ROOT / "CHANGELOG.md").read_text()


def test_security_scope_describes_local_http_server():
    security = (ROOT / "SECURITY.md").read_text()
    assert "loopback HTTP server" in security
    assert "makes no network request" not in security


def test_readme_explains_config_and_state_paths():
    readme = (ROOT / "README.md").read_text()
    assert "main checkout" in readme
    assert "state_dir" in readme

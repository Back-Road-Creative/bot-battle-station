"""Static safety contracts for the Windows kiosk launcher."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "bin/session-board-kiosk.ps1").read_text()


def test_startup_does_not_disable_execution_policy():
    assert "ExecutionPolicy Bypass" not in SCRIPT
    assert "ExecutionPolicy Bypass" not in (ROOT / "README.md").read_text()


def test_launcher_validates_user_supplied_paths_and_has_uninstall():
    assert "[ValidatePattern(" in SCRIPT
    assert "[switch]$Uninstall" in SCRIPT
    assert "Remove-Item -LiteralPath $lnk" in SCRIPT


def test_existing_http_service_must_be_this_board():
    assert '$url = "http://127.0.0.1:$Port/"' in SCRIPT
    assert "ConvertFrom-Json" in SCRIPT
    assert "$payload.api -eq 1" in SCRIPT
    assert "$null -ne $payload.sessions" in SCRIPT


def test_board_command_becomes_validated_argument_array():
    assert "[regex]::Matches" in SCRIPT
    assert "$boardArgs" in SCRIPT
    assert "-split" not in SCRIPT


def test_install_runs_from_a_windows_folder_not_the_wsl_share():
    # RemoteSigned refuses an unsigned script on \\wsl.localhost (an Internet-zone path),
    # so the documented install copies the launcher to Windows first and runs that copy.
    for doc in (SCRIPT, (ROOT / "README.md").read_text()):
        assert '-File "$(wslpath -w bin/session-board-kiosk.ps1)"' not in doc
        assert "$env:LOCALAPPDATA" in doc
        assert 'cp bin/session-board-kiosk.ps1 "$board/"' in doc
        assert '-File "$(wslpath -w "$board/session-board-kiosk.ps1")" -Install' in doc


def test_install_from_the_installed_copy_skips_copying_onto_itself():
    assert "if ([IO.Path]::GetFullPath($PSCommandPath) -ne [IO.Path]::GetFullPath($copy))" in SCRIPT

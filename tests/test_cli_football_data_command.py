import subprocess
import sys


def test_cli_help_lists_football_data_history_command():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "sync-football-data-history" in result.stdout


def test_football_data_history_help_has_required_options():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "sync-football-data-history", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--season" in result.stdout
    assert "--league" in result.stdout
    assert "--file" in result.stdout

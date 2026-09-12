import subprocess
import sys


def test_cli_has_odds_history_commands():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "capture-odds-snapshots" in result.stdout
    assert "list-odds-history" in result.stdout
    assert "odds-movement" in result.stdout

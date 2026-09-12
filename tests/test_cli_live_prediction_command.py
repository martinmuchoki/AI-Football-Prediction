import subprocess
import sys


def test_cli_lists_phase7_commands():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "run-live-predictions" in result.stdout
    assert "list-live-predictions" in result.stdout


def test_run_live_predictions_help():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "run-live-predictions", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--competition" in result.stdout
    assert "--season" in result.stdout
    assert "--threshold" in result.stdout

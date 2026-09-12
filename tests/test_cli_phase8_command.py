import subprocess
import sys


def test_cli_has_phase8_commands():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "grade-live-predictions" in result.stdout
    assert "live-performance" in result.stdout

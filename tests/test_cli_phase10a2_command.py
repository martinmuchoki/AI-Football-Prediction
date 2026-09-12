import subprocess
import sys


def test_cli_exposes_phase10a2_commands():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "check-source-health" in result.stdout
    assert "refresh-live-market" in result.stdout

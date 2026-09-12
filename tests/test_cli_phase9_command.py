import subprocess
import sys


def test_cli_has_phase9_command():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "generate-social-content" in result.stdout

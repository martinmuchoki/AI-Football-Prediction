import subprocess
import sys


def test_cli_exposes_phase10a3_commands():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "add-web-target" in result.stdout
    assert "list-web-targets" in result.stdout
    assert "collect-web-targets" in result.stdout

import subprocess
import sys


def test_cli_has_reconciliation_commands():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "reconcile-openfootball-epl" in result.stdout
    assert "reconciliation-summary" in result.stdout
    assert "reconciliation-issues" in result.stdout

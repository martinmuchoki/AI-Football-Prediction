import subprocess
import sys


def test_cli_lists_market_residual_command():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "evaluate-market-residual" in result.stdout


def test_market_residual_help_lists_fold_arguments():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "evaluate-market-residual", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--fold1-train" in result.stdout
    assert "--fold1-validation" in result.stdout
    assert "--fold2-train" in result.stdout
    assert "--fold2-validation" in result.stdout
    assert "--output" in result.stdout

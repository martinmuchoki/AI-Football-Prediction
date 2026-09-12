import subprocess
import sys


def test_cli_lists_evaluate_baselines_command():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "evaluate-baselines" in result.stdout


def test_evaluate_baselines_help_lists_required_arguments():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "evaluate-baselines", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--train" in result.stdout
    assert "--validation" in result.stdout
    assert "--output" in result.stdout

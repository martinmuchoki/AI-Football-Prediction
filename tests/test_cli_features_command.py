import subprocess
import sys


def test_cli_lists_build_features():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "build-features" in result.stdout


def test_build_features_help():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "build-features", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--from-season" in result.stdout
    assert "--to-season" in result.stdout
    assert "--output" in result.stdout

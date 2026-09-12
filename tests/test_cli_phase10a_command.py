import subprocess
import sys


def test_cli_has_phase10a_commands():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "-h"],
        capture_output=True,
        text=True,
        check=True,
    )
    for command in (
        "seed-data-sources",
        "list-data-sources",
        "add-web-source",
        "collect-public-url",
        "resolve-source-field",
    ):
        assert command in result.stdout

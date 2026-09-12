import subprocess, sys

def test_cli_has_selector():
    r = subprocess.run([sys.executable, "-m", "app.cli", "-h"], capture_output=True, text=True, check=True)
    assert "select-high-confidence" in r.stdout

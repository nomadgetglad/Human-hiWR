"""Every demo still runs and prints exactly what its saved *_output.txt says."""
import subprocess
import sys

from conftest import ROOT


def test_all_demos_match_saved_output():
    r = subprocess.run([sys.executable, "run_all_demos.py"], cwd=ROOT / "hiwr", capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all demos passed" in r.stdout

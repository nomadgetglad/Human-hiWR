"""Every demo still runs and prints exactly what its saved *_output.txt says."""
import subprocess
import sys

from conftest import ROOT


def test_all_demos_match_saved_output():
    r = subprocess.run([sys.executable, "run_all_demos.py"], cwd=ROOT / "hiwr", capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all demos passed" in r.stdout


def test_margin_sim_adaptive_beats_fixed_on_average_and_good_nights():
    import importlib, sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hiwr"))
    sim = importlib.import_module("margin_sim")
    for s in ("average", "good"):
        fixed, human = sim.run(s, "fixed"), sim.run(s, "human")
        assert human["profit"] > fixed["profit"] and human["manip_cents"] < fixed["manip_cents"]

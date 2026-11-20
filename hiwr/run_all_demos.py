"""Run: python run_all_demos.py
Runs every *_demo.py in this folder and compares its output with the saved *_output.txt run.
Exit code 0 means every demo ran and matched. Pass --update to overwrite the saved outputs."""
import subprocess, sys
from pathlib import Path

here = Path(__file__).parent
update = "--update" in sys.argv
failed = 0
for demo in sorted(here.glob("*demo.py")):
    run = subprocess.run([sys.executable, demo.name], cwd=here, capture_output=True, text=True)
    saved = here / demo.name.replace("_demo.py", "_output.txt")
    if run.returncode != 0:
        status, failed = "ERROR", failed + 1
        print(run.stderr.strip().splitlines()[-1] if run.stderr.strip() else "", file=sys.stderr)
    elif demo.name == "demo.py" or not saved.exists():
        status = "ran (no saved output)"
    elif update:
        saved.write_text(run.stdout); status = "ran, output saved"
    elif saved.read_text() == run.stdout:
        status = "ran, matches saved output"
    else:
        status, failed = "ran, OUTPUT DIFFERS", failed + 1
    print(f"{demo.name:22} {status}")
print("all demos passed" if not failed else f"{failed} demo(s) failed")
sys.exit(1 if failed else 0)

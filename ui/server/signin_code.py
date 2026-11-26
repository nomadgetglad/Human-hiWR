"""Print a sign-in setup code from the server computer. This is how the account holder gets back in after
losing their phone (someone at the office computer is trusted as the account holder), and works for anyone.

Run from the ui/ folder, with the server stopped or running:
    python -m server.signin_code owner_alex
"""
import os
import sys
from pathlib import Path

from . import seed  # noqa: F401  (puts hiwr/ on the path)
import hiwr_auth as auth
from hiwr_contacts import connect


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    # same file as server/app.py (not imported: starting the app would print its own code)
    db = connect(os.environ.get("HUMAN_DB", str(Path(__file__).resolve().parents[1] / "human.db")))
    try:
        code = auth.issue_setup_code(db, "console", argv[1])
    except LookupError as e:
        print(e)
        return 1
    db.commit()
    print(f"Setup code for {argv[1]}: {code[:4]}-{code[4:]}  (good for {auth.SETUP_CODE_HOURS} hours, works once)")
    print("Using it replaces their authenticator and signs out their open sessions." if auth.factor(db, argv[1]) else "")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

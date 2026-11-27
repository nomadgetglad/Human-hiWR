"""Open ticket exposure for the weekly outlook and accounting view: count, dollars by type, aging.

Owner-approved script: the assistant can run it at Finance level and up.
Usage: python ticket_exposure.py <path to hiwr.db> [as-of date YYYY-MM-DD]
"""
import json
import sqlite3
import sys
from datetime import date

db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
as_of = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
out = {}
for tid, ttype, opened, fin in db.execute("SELECT id, type, opened_at, financial FROM tickets WHERE status != 'closed'"):
    row = out.setdefault(ttype, {"open": 0, "dollars_pending": 0.0, "aging": {"0-2 days": 0, "3-7 days": 0, "8+ days": 0}})
    row["open"] += 1
    cents = db.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM money_actions WHERE ticket_id=?", (tid,)).fetchone()[0]
    if not cents and fin:
        cents = round(float(json.loads(fin).get("variance_amount") or 0) * 100)
    row["dollars_pending"] += cents / 100
    age = (as_of - date.fromisoformat(opened[:10])).days
    row["aging"]["0-2 days" if age <= 2 else "3-7 days" if age <= 7 else "8+ days"] += 1
print(json.dumps({"as_of": as_of.isoformat(), "currency": "USD", "by_type": out}, indent=2))

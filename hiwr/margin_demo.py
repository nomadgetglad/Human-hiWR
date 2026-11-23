"""Run: python margin_demo.py
Sample-to-sample numbers with options for holding margin. HiWR gives the quantities, Human gives the options
and the quantities each one needs. Sample data: 5 weeks of a restaurant, seafood prices creep up in November."""
import random
from datetime import date, timedelta
from hiwr_contacts import *
from hiwr_access import *
from hiwr_accounting import post, confirm_all_proposed, set_target
from hiwr_margins import snapshot, sample_table, choose, label
from human_margins import options, render, needs_text
import hiwr_contract

random.seed(11)
db = connect()
add_plan_owner(db, "owner_alex", "setup")
set_level(db, "accounting_sue", 3, "owner_alex"); set_level(db, "chef_marco", 2, "owner_alex")
weeks = [(date(2026, 10, 19) + timedelta(days=7 * i), date(2026, 10, 25) + timedelta(days=7 * i)) for i in range(5)]
WEEKS = [(a.isoformat(), b.isoformat()) for a, b in weeks]
counts = {}
for wk, (a, b) in enumerate(weeks):
    seafood_rate = 0.30 * (1 + 0.15 * max(wk - 1, 0))        # Harbor Seafood up ~15% a week from week 3
    hours = drinks = plates = 0
    d = a
    while d <= b:
        busy = d.weekday() in (4, 5)
        f = int((360000 if busy else 240000) * random.uniform(0.92, 1.08))
        bv = int(f * random.uniform(0.20, 0.23))
        ds = d.isoformat()
        post(db, ds, "revenue", f, "POS food", "pos", f"{ds}-f", "pos", department="food")
        post(db, ds, "revenue", bv, "POS bar", "pos", f"{ds}-b", "pos", department="beverage")
        plates += round(f / 1650); drinks += round(bv / 900)
        post(db, ds, "expense", int(f * 0.30 * 0.7), "Sysco invoice", "invoice", f"S-{ds}", "rcv", counterparty="Sysco", department="food")
        post(db, ds, "expense", int(f * seafood_rate * 0.3), "Harbor invoice", "invoice", f"H-{ds}", "rcv",
             counterparty="Harbor Seafood", department="food")
        post(db, ds, "expense", int(bv * 0.21), "Coastal Beverage", "invoice", f"B-{ds}", "rcv", counterparty="Coastal Beverage Co", department="beverage")
        extra = 1.12 if wk >= 3 else 1.0                       # two new hires overlapping in training in weeks 4-5
        for dept, amt, h in (("food", 72000 if busy else 52000, 45 if busy else 33), ("beverage", 17000 if busy else 12000, 11 if busy else 8), ("shared", 29000, 14)):
            w = int(amt * extra * random.uniform(0.97, 1.03))
            hours += h * extra
            post(db, ds, "expense", w, f"hours {dept}", "timeclock", f"T-{ds}-{dept}", "timeclock", department=dept)
            post(db, ds, "expense", int(w * 0.0765), "employer tax", "timeclock_tax", f"X-{ds}-{dept}", "timeclock",
                 counterparty="payroll tax accrual", department="shared")
        post(db, ds, "expense", int((f + bv) * 0.027), "card processing", "bank", f"Q-{ds}", "bank", counterparty="Square")
        if d.day == 1:
            for who, amt in (("Front St Properties", 850000), ("Duke Energy", 141000), ("Piedmont Natural Gas", 52000)):
                post(db, ds, "expense", amt, "monthly bill", "bank", f"{who}-{ds}", "bank", counterparty=who)
        d += timedelta(days=1)
    counts[WEEKS[wk]] = {"labor_hours": round(hours), "drinks_sold": drinks, "plates_sold": plates,
                         "waste": [18000, 21000, 26000, 41000, 45000][wk]}
for who, amt in (("Front St Properties", 850000), ("Duke Energy", 139000), ("Piedmont Natural Gas", 47000)):  # October's bills
    post(db, "2026-10-01", "expense", amt, "monthly bill", "bank", f"{who}-oct", "bank", counterparty=who)
confirm_all_proposed(db, "accounting_sue", "2026-10-01", "2026-11-30")

print("1. HiWR: QUANTITIES, SAMPLE TO SAMPLE (one week each; rent and utilities spread by day)")
snaps = [snapshot(db, "accounting_sue", w, counts[w]) for w in WEEKS]
print(sample_table(snaps))
print("\n   Same table for chef_marco (Manager):")
print(sample_table(snaps, finance=False))
print("   chef_marco asks for a snapshot with dollars:", end=" ")
try:
    snapshot(db, "chef_marco", WEEKS[-1], counts[WEEKS[-1]])
except PermissionError as e:
    print("BLOCKED:", e)
last = snaps[-1]
print(f"\n   Latest sample quantities HiWR hands to Human: {last['labor_hours']} labor hours at ${last['avg_wage'] / 100:.2f}/h avg, "
      f"{last['plates_sold']} plates at ${last['avg_plate_price'] / 100:.2f}, {last['drinks_sold']} drinks at "
      f"${last['avg_drink_price'] / 100:.2f}, waste ${last['waste'] / 100:,.0f}, vendor shares {last['food_vendor_share_pct']}")

print("\n2. Human: OPTIONS AND NEEDED QUANTITIES to hold the margin of the steady week of Oct 26")
res2 = options(last, snaps[1])
print(render(last, snaps[1], res2))

print("\n   Owner-set floor instead (25% margin): ")
res = options(last, snaps[1], 25.0)
print(render(last, snaps[1], res))

print("\n3. KEEPING THE BUILDS IN STEP")
old = dict(last, contract="0.9")
try:
    options(old, snaps[-2])
except ValueError as e:
    print("   Human given an old snapshot: REFUSED:", e)
broken = {k: v for k, v in last.items() if k != "drinks_sold"}
try:
    options(broken, snaps[-2])
except ValueError as e:
    print("   Snapshot missing a field: REFUSED:", e)

print("\n4. OWNER PICKS, LOGGED WITH THE NEEDED QUANTITIES")
for o in res2["options"]:
    if o["name"] in ("food_cost", "labor", "beverage_attach"):
        choose(db, "owner_alex", WEEKS[-1], o, needs_text(o))
for r in db.execute("SELECT sample, option, expected_cents, note FROM margin_choices"):
    print(f"   {r['sample']} {r['option']:16} expects ${r['expected_cents'] / 100:,.2f}  needs {r['note']}")
try:
    choose(db, "accounting_sue", WEEKS[-1], res2["options"][1], "")
except PermissionError as e:
    print("   accounting_sue picks the waste option:", "BLOCKED:", e)

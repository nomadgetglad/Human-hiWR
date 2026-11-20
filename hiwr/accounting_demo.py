"""Run: python accounting_demo.py
Accounting view: classify expenses, cross them with revenue, compare months, reconcile with QuickBooks. Sample data."""
import random
from datetime import date, timedelta
from hiwr_contacts import *
from hiwr_access import *
from hiwr_accounting import *

random.seed(7)
db = connect()
add_plan_owner(db, "owner_alex", "setup")
set_level(db, "accounting_sue", 3, "owner_alex")
set_level(db, "chef_marco", 2, "owner_alex")
set_level(db, "cook_luis", 0, "owner_alex")
certify(db, "accounting_sue", "refund", "owner_alex")
certify(db, "owner_alex", "refund", "owner_alex")
OCT, NOV = ("2026-10-01", "2026-10-31"), ("2026-11-01", "2026-11-19")


def days(p):
    d = date.fromisoformat(p[0])
    while d <= date.fromisoformat(p[1]):
        yield d
        d += timedelta(days=1)


def feed(p, food_cost_rate, label):
    food = bev = 0
    for d in days(p):
        busy = d.weekday() in (4, 5)
        f = int((360000 if busy else 240000) * random.uniform(0.9, 1.1))
        b = int(f * random.uniform(0.20, 0.24))
        food, bev = food + f, bev + b
        post(db, d.isoformat(), "revenue", f, "POS food sales", "pos", f"{d}-food", "pos", department="food")
        post(db, d.isoformat(), "revenue", b, "POS bar sales", "pos", f"{d}-bar", "pos", department="beverage")
        # deliveries: produce/dry daily except Sunday, seafood Tue/Fri, beverage Mon/Thu
        if d.weekday() != 6:
            post(db, d.isoformat(), "expense", int(f * 7 / 6 * food_cost_rate * 0.7 * random.uniform(0.85, 1.15)),
                 "Sysco invoice", "invoice", f"SYS-{d}", "receiving", counterparty="Sysco", department="food")
        if d.weekday() in (1, 4):
            post(db, d.isoformat(), "expense", int(f * 3.5 * food_cost_rate * 0.3), "Harbor Seafood invoice",
                 "invoice", f"HAR-{d}", "receiving", counterparty="Harbor Seafood", department="food")
        if d.weekday() in (0, 3):
            post(db, d.isoformat(), "expense", int(b * 3.5 * 0.21), "Coastal Beverage invoice", "invoice",
                 f"BEV-{d}", "receiving", counterparty="Coastal Beverage Co", department="beverage")
        # labor accrues daily from clocked hours (the day it was worked, not the day it's paid)
        for dept, amt in (("food", 72000 if busy else 52000), ("beverage", 17000 if busy else 12000), ("shared", 29000)):
            w = int(amt * random.uniform(0.95, 1.05))
            post(db, d.isoformat(), "expense", w, f"hours worked {dept}", "timeclock", f"TC-{d}-{dept}", "timeclock",
                 department=dept)
            post(db, d.isoformat(), "expense", int(w * 0.0765), f"employer payroll tax {dept}",
                 "timeclock_tax", f"TCX-{d}-{dept}", "timeclock", counterparty="payroll tax accrual", department="shared")
        # card fees come out of each day's deposit
        post(db, d.isoformat(), "expense", int((f + b) * 0.027), "card processing", "bank", f"SQ-{d}", "bank",
             counterparty="Square")
    first = p[0]
    for who, what, amt in (("Front St Properties", "rent", 850000), ("Duke Energy", "electric", 141000),
                           ("Piedmont Natural Gas", "gas", 52000), ("Spectrum", "internet", 18900),
                           ("Ecolab", "cleaning chemicals", 31000)):
        post(db, first, "expense", amt, f"{label} {what}", "bank", f"{who}-{first}", "bank", counterparty=who)
    return food, bev


feed(OCT, 0.30, "October")
feed(NOV, 0.335, "November")    # tomato price spike and more waste in November
db.execute("INSERT INTO acct_memos(memo_date, kind, amount_cents, note) VALUES ('2026-10-31','waste',31000,'October waste log')")
db.execute("INSERT INTO acct_memos(memo_date, kind, amount_cents, note) VALUES ('2026-11-19','waste',72000,'November waste log, mostly roma tomato')")

# odd ones in November
post(db, "2026-11-12", "expense", 340000, "replace walk-in compressor", "bank", "CR-1112", "bank",
     counterparty="Carolina Refrigeration")
amz = post(db, "2026-11-07", "expense", 8640, "order 113-4471", "bank", "AMZ-1107", "bank", counterparty="Amazon")
sysco_bank = post(db, "2026-11-03", "expense",
                  db.execute("SELECT amount_cents FROM acct_txns WHERE source_ref='SYS-2026-11-02'").fetchone()[0],
                  "ACH payment", "bank", "ACH-1104", "bank", counterparty="Sysco")
lowes = post(db, "2026-11-15", "expense", 21218, "misc", "bank", "LOW-1115", "bank", counterparty="Lowes")
print("Posting the same Sysco invoice again:", end=" ")
try:
    post(db, "2026-11-02", "expense", 1, "Sysco invoice", "invoice", "SYS-2026-11-02", "receiving", counterparty="Sysco")
except ValueError as e:
    print("BLOCKED:", e)

# approved refund flows in; an unapproved one doesn't
r = request_money_action(db, "refund", 4800, "Refund table 12, wrong order", "server_kim", ticket_id="DSP-0011")
print("Posting refund before approval:", end=" ")
try:
    post_money_action(db, r, "2026-11-16", "accounting_sue")
except PermissionError as e:
    print("BLOCKED:", e)
approve(db, r, "accounting_sue"); approve(db, r, "owner_alex")
post_money_action(db, r, "2026-11-16", "accounting_sue", department="food")
print("Refund posted after two approvals.\n")

print("1. REVIEW QUEUE (HiWR proposes, a Finance person confirms)")
confirm_all_proposed(db, "accounting_sue", *OCT)
for q in review_queue(db, *NOV):
    if q["status"] == "needs_person":
        print(f"   #{q['id']} {q['date']} {q['payee']:22} {q['amount']:>10}  HiWR: {q['hiwr_says']:18} {q['why']}")
print("   chef_marco tries to confirm:", end=" ")
try:
    confirm(db, amz, "chef_marco", "supplies")
except PermissionError as e:
    print("BLOCKED:", e)
cr = db.execute("SELECT id FROM acct_txns WHERE source_ref='CR-1112'").fetchone()[0]
confirm(db, cr, "accounting_sue", reason="accountant: new compressor is capital, depreciate")
confirm(db, amz, "accounting_sue", "supplies")
exclude(db, sysco_bank, "accounting_sue", "payment of SYS-2026-11-02, already counted as the invoice")
amz2 = post(db, "2026-11-18", "expense", 4299, "order 113-9902", "bank", "AMZ-1118", "bank", counterparty="Amazon")
e = db.execute("SELECT class, reason FROM acct_class_events WHERE txn_id=? ORDER BY id DESC", (amz2,)).fetchone()
print(f"   Next Amazon charge now proposed as {e['class']} ({e['reason']})")
print(f"   Sue confirmed the compressor as capital, Amazon as supplies, excluded the Sysco ACH; Lowes left for later")
confirm_all_proposed(db, "accounting_sue", *NOV)
for day, food_v, bev_v in (("2026-09-30", 640000, 410000), ("2026-10-19", 655000, 402000),
                           ("2026-10-31", 650000, 405000), ("2026-11-19", 712000, 398000)):
    record_inventory_count(db, "chef_marco", day, "food", food_v)
    record_inventory_count(db, "chef_marco", day, "beverage", bev_v)
set_target(db, "owner_alex", "food_cost_pct", 28, 32)

print("\n2. NOVEMBER TO DATE, FINANCE VIEW (accounting_sue)")
print(report(db, "accounting_sue", *NOV))

print("\n3. SAME PERIOD, MANAGER VIEW (chef_marco)")
print(report(db, "chef_marco", *NOV))
print("   cook_luis opens it:", end=" ")
try:
    report(db, "cook_luis", *NOV)
except PermissionError as e:
    print("BLOCKED:", e)

print("\n4. NOVEMBER TO DATE vs THE SAME 19 DAYS OF OCTOBER")
print(compare(db, "accounting_sue", ("2026-10-01", "2026-10-19"), NOV))

print("\n5. QUICKBOOKS CHECK (QuickBooks is the source of truth)")
nov = numbers(db, *NOV)["by_class"]
qb_lines = [(f"QB {c}", c, v) for c, v in nov.items()] + [("QB Health insurance", "benefits", 0)]
qb_lines = [(a, c, v + 41250 if c == "benefits" else v + 21218 if c == "supplies" else v) for a, c, v in qb_lines]
import_qb(db, "accounting_sue", "2026-11", qb_lines)
for line in reconcile_qb(db, "accounting_sue", "2026-11", *NOV):
    print("   FLAG:", line)

print("\n6. AUTHORITY CHECK-IN")
print("  ", check_in(db, "acct_report:2026-11", "owner_alex", works=True, intended_consequence=True,
                     note="Food cost over target traced to tomato and waste; chef running tomato special"))
print("   Access log rows for the accounting view:",
      db.execute("SELECT COUNT(*) FROM access_log WHERE action LIKE '%financial_block'").fetchone()[0])
try:
    db.execute("DELETE FROM acct_txns")
except Exception as e:
    print("   Deleting ledger rows:", e)

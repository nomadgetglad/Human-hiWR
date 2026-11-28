"""Run: python vectors_demo.py
Income and expenses as signed vectors: gross income, total expenses and net from the HiWR databases, checked
against the ledger to the cent. Sample data. Runs on plain Python here so the saved output matches anywhere;
set HIWR_VECTORS=torch or numpy in your own code to use those (same cents)."""
from datetime import date, timedelta
from hiwr_contacts import *
from hiwr_access import *
from hiwr_processes import open_ticket, ticket_priority
from hiwr_lab import log_time
import hiwr_accounting as acct
import hiwr_vectors as vec

HOW = "python"
db = connect()
add_plan_owner(db, "owner_alex", "setup")
for m in ("accounting_sue", "manager_jo"):
    set_level(db, m, 3, "owner_alex")
    certify(db, m, "refund", "owner_alex")
set_level(db, "chef_marco", 2, "owner_alex")
set_level(db, "cook_luis", 0, "owner_alex")


def month(ym, food, last_day=None):
    first = date.fromisoformat(f"{ym}-01")
    end = date.fromisoformat(last_day or vec._month_bounds(ym)[1])
    d, i = first, 0
    while d <= end:
        busy = d.weekday() in (4, 5)
        f = (food * 3 // 2 if busy else food) + 41 * i
        s = d.isoformat()
        acct.post(db, s, "revenue", f, "POS food sales", "pos", f"{s}-food", "pos", department="food")
        acct.post(db, s, "revenue", f // 5, "POS bar sales", "pos", f"{s}-bar", "pos", department="beverage")
        acct.post(db, s, "expense", f * 32 // 100, "Sysco invoice", "invoice", f"SYS-{s}", "receiving",
                  counterparty="Sysco", department="food")
        acct.post(db, s, "expense", 70000 if busy else 52000, "hours worked", "timeclock", f"TC-{s}", "timeclock",
                  department="food")
        acct.post(db, s, "expense", (f + f // 5) * 27 // 1000, "card processing", "bank", f"SQ-{s}", "bank",
                  counterparty="Square")
        d, i = d + timedelta(days=1), i + 1
    acct.post(db, f"{ym}-01", "expense", 850000, "rent", "bank", f"rent-{ym}", "bank", counterparty="Front St Properties")
    acct.post(db, f"{ym}-01", "expense", 141000, "electric", "bank", f"duke-{ym}", "bank", counterparty="Duke Energy")
    acct.confirm_all_proposed(db, "accounting_sue", f"{ym}-01", end.isoformat())


month("2026-08", 236000)
month("2026-09", 241000)
month("2026-10", 239000)
month("2026-11", 228000, last_day="2026-11-27")

# one refund that went through double approval, one compressor (capital), one charge nobody has looked at
r = request_money_action(db, "refund", 4800, "Refund table 12, wrong order", "chef_marco")
approve(db, r, "accounting_sue"); approve(db, r, "manager_jo")
acct.post_money_action(db, r, "2026-11-16", "accounting_sue", department="food")
cr = acct.post(db, "2026-11-12", "expense", 340000, "replace walk-in compressor", "bank", "CR-1112", "bank",
               counterparty="Carolina Refrigeration")
acct.confirm_all_proposed(db, "accounting_sue", "2026-11-16", "2026-11-16")
acct.confirm(db, cr, "accounting_sue", reason="accountant: capital")
acct.post(db, "2026-11-15", "expense", 21218, "misc", "bank", "LOW-1115", "bank", counterparty="Lowes")

# open money that is NOT in the books yet
open_ticket(db, "SHIPMENT", "Harbor Seafood short 2 cases", ticket_priority(db, "SHIPMENT", 186, perishable=True),
            "2026-11-26T08:10", "receiving",
            financial={"invoice": "HAR-1126", "expected": 6, "received": 4, "variance_cents": -18600,
                       "proposed_resolution": "credit 2 cases", "lands_in": "anticipated costs"})
request_money_action(db, "payroll", 412575, "Payroll week of Nov 23", "manager_jo")

print("1. ONE SIGNED VECTOR, SUMMED (one day: + money in, - money out)")
one = vec.books(db, "2026-11-02", "2026-11-02", how=HOW)["vectors"]
for e, s in zip(one.entries, one.signed):
    print(f"   {e.cls:14} {vec.usd(s):>12}")
t = one.total()
print(f"   sum = {vec.usd(sum(one.signed))}  ->  gross income {vec.usd(t['gross_income'])}, "
      f"expenses {vec.usd(t['expenses'])}, net {vec.usd(t['net'])}")
print("   (adding the expenses as negatives gives the NET; gross is the top line before expenses)\n")

print("2. BY MONTH, FINANCE VIEW (accounting_sue)")
print(vec.report(db, "accounting_sue", "2026-08-01", "2026-11-27", axis="month", how=HOW))

print("\n3. NOVEMBER BY WEEK AND BY CLASS")
v = vec.books(db, "2026-11-01", "2026-11-27", how=HOW)["vectors"]
for k, x in v.by("week").items():
    print(f"   week of {k}  gross {vec.usd(x['gross_income']):>12}  expenses {vec.usd(x['expenses']):>12}  net {vec.usd(x['net']):>12}")
for k, x in v.by("class").items():
    print(f"   {k:16} {vec.usd(x['net']):>12}")

print("\n4. CHECK AGAINST THE LEDGER (hiwr_accounting.numbers)")
n = acct.numbers(db, "2026-11-01", "2026-11-27")["metrics"]
t = v.total()
for label, a, b in (("gross income", t["gross_income"], n["gross_sales"]), ("net sales", t["net_sales"], n["net_sales"]),
                    ("expenses", t["expenses"], n["cogs"] + n["labor"] + n["occupancy"] + n["operating"]),
                    ("net", t["net"], n["operating_profit"])):
    print(f"   {label:13} vectors {vec.usd(a):>12}   ledger {vec.usd(b):>12}   {'match' if a == b else 'DIFFERENT'}")

print("\n5. STEW: DISH SALES x MENU PRICE AND PLATE COST, BESIDE THE BOOKS (no winner picked)")
for m, unit, cents in (("tortilla", "ea", 12), ("mahi", "oz", 55), ("roma tomato", "lb", 189)):
    db.execute("INSERT INTO materials VALUES (?,?,100,0)", (m, unit))
    db.execute("INSERT INTO material_sources VALUES (?, 'Sysco', 2, ?, 1, 1)", (m, cents))
db.executemany("INSERT INTO recipes VALUES (?,?,?,?,?)", [("fish tacos", "dish", 1, "plate", 1400),
                                                          ("salsa", "sub", 32, "oz", None)])
db.executemany("INSERT INTO recipe_lines VALUES (?,?,?,?)", [("fish tacos", "tortilla", 3, "ea"), ("fish tacos", "mahi", 4.5, "oz"),
                                                            ("fish tacos", "salsa", 2, "oz"), ("salsa", "roma tomato", 2, "lb")])
for hour, qty in ((12, 61), (13, 44), (18, 70), (19, 77)):
    db.execute("INSERT INTO dish_sales VALUES ('2026-11-02', ?, 'fish tacos', ?)", (hour, qty))
ops = vec.stew(db, "2026-11-02", "2026-11-02", how=HOW)
for r in vec.side_by_side(one, ops, "Stew dish sales x price / plate cost"):
    print(f"   {r['class']:10} books {vec.usd(r['books']):>11}  Stew {vec.usd(r['operations']):>11}  gap {vec.usd(r['gap']):>10}  {r['decision']}")
print("   (Stew counts only fish tacos here; the POS total has the whole menu)")

print("\n6. LAB JOB: CONTRACT VS CREW TIME BY ROLE")
db.execute("INSERT INTO staff(member, role, hourly_cents) VALUES ('ana','framer',2850), ('bo','laborer',1733)")
pid = db.execute("INSERT INTO lab_projects(name, contract_cents, starts_on, target_end) VALUES "
                 "('Oak St house', 41000000, '2026-11-02', '2027-06-01')").lastrowid
db.execute("INSERT INTO lab_phases(project_id, seq, name, labor_budget_cents) VALUES (?,1,'framing',800000)", (pid,))
for d in range(2, 7):
    log_time(db, pid, 1, "ana", f"2026-11-0{d}", 7.5, 2850)
    log_time(db, pid, 1, "bo", f"2026-11-0{d}", 8.25, 1733)
job = vec.lab(db, pid, how=HOW)
for k, x in job["vectors"].by("class").items():
    print(f"   {k:14} {vec.usd(x['net']):>14}")
print(f"   left after crew time {vec.usd(job['vectors'].total()['net']):>14}  (8.25 h x $17.33 rounds to the cent once)")

print("\n7. BUSINESS PULSE: NOVEMBER TO DATE VS THE 3 MONTHS BEFORE, BY WHO IS LOOKING")
for who in ("accounting_sue", "chef_marco", "cook_luis"):
    print(f"   {who:15} {vec.pulse(db, who, '2026-11', through='2026-11-27', how=HOW)}")
print("   (month to date is compared with days 1-27 of Aug, Sep and Oct, not with whole months)")

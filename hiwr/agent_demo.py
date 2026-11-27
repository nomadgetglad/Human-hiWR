"""Run: python agent_demo.py
Pioneering phase -> Human as bookkeeper -> accounting agent -> task list agent, plus inventory order updates
and vendor suggestions from approved price sources. Sample data for a restaurant."""
import random
from datetime import date, timedelta
from hiwr_contacts import *
from hiwr_access import *
from hiwr_accounting import post, confirm_all_proposed, confirm, review_queue
import hiwr_pioneer as pioneer
import human_agent as human

random.seed(5)
db = connect()
add_plan_owner(db, "owner_alex", "setup")
set_level(db, "accounting_sue", 3, "owner_alex"); set_level(db, "chef_marco", 2, "owner_alex")
set_level(db, "cook_luis", 0, "owner_alex")
MENU = [("PLU-101", "Fish tacos", 1400, "Food"), ("PLU-102", "Burger", 1500, "Food"), ("PLU-103", "Tomato salad", 900, "Food"),
        ("PLU-104", "Fries", 500, "Food"), ("PLU-201", "Draft beer", 700, "Drinks"), ("PLU-202", "Margarita", 1100, "Drinks")]


def square_rows(d):
    busy = d.weekday() in (4, 5)
    rows = []
    for upc, item, price, cat in MENU:
        q = int((60 if busy else 38) * random.uniform(0.8, 1.2) * (0.6 if cat == "Drinks" else 1))
        rows.append({"Date": d.isoformat(), "Item": item, "Category": cat, "Qty": str(q),
                     "Net Sales": f"${q * price / 100:,.2f}", "Tax": f"${q * price * 0.0725 / 100:,.2f}"})
    return rows


def days(a, b):
    d = date.fromisoformat(a)
    while d <= date.fromisoformat(b):
        yield d
        d += timedelta(days=1)


print("1. PIONEERING PHASE")
pioneer.start(db, "owner_alex", pioneer.PATHS)
print("   Turn Human on before setup is done:", end=" ")
try:
    human.set_stage(db, "owner_alex", 1)
except PermissionError as e:
    print("BLOCKED:", e)
for upc, item, price, cat in MENU:
    pioneer.add_item(db, "chef_marco", upc, item, price, "food" if cat == "Food" else "beverage")
pioneer.first_order(db, "chef_marco", "POS-0001")
for d in days("2026-09-01", "2026-09-03"):   # routine sales by hand from the Z-report
    pioneer.record_day(db, "chef_marco", d.isoformat(), {"food": 210000, "beverage": 60000}, sales_tax_cents=19575)
print("   after items + 3 hand-entered days:", pioneer.status(db)["next"])
mapping = {"columns": {"date": "Date", "item": "Item", "category": "Category", "qty": "Qty", "net": "Net Sales", "tax": "Tax"},
           "departments": {"Food": "food"}}
cid = pioneer.connect(db, "owner_alex", "Square", "csv_export", mapping)
rows = [r for d in days("2026-09-01", "2026-09-21") for r in square_rows(d)]
pv = pioneer.preview(db, cid, rows)
print(f"   Square export preview: {pv['days']} days, {pv['lines']} lines, {len(pv['problems'])} problems, e.g. {pv['problems'][0]}")
mapping["departments"]["Drinks"] = "beverage"
cid = pioneer.connect(db, "owner_alex", "Square", "csv_export", mapping)
print("   import after fixing the mapping:", pioneer.run_import(db, "accounting_sue", cid, rows))
st = pioneer.status(db)
print(f"   status: {st['sale_days']} sale days, {st['full_weeks']} full weeks, done={st['done']}")
print("  ", human.set_stage(db, "owner_alex", 1, "pioneering done"))


def books(a, b):
    """The rest of the business's money for a stretch of days: imports, timeclock, bills."""
    pioneer.run_import(db, "accounting_sue", cid, [r for d in days(a, b) for r in square_rows(d)])
    for d in days(a, b):
        ds = d.isoformat()
        for dept, amt in (("food", 60000), ("beverage", 14000), ("shared", 29000)):
            w = int(amt * random.uniform(0.95, 1.05))
            post(db, ds, "expense", w, f"hours {dept}", "timeclock", f"T-{ds}-{dept}", "timeclock", department=dept)
            post(db, ds, "expense", int(w * 0.0765), "employer tax", "timeclock_tax", f"X-{ds}-{dept}", "timeclock",
                 counterparty="payroll tax accrual", department="shared")
        if d.weekday() != 6:
            post(db, ds, "expense", int(random.uniform(55000, 75000)), "invoice", "invoice", f"S-{ds}", "rcv",
                 counterparty="Sysco", department="food")
        if d.weekday() == 2:
            post(db, ds, "expense", int(random.uniform(26000, 34000)), "cleaning chemicals", "bank", f"E-{ds}", "bank",
                 counterparty="Ecolab")
        if d.weekday() == 4:
            post(db, ds, "expense", 55000, "EFTPS deposit", "bank", f"F-{ds}", "bank", counterparty="IRS EFTPS")
        if d.day == 20:
            post(db, ds, "expense", 400000, "sales tax payment", "bank", f"N-{ds}", "bank", counterparty="NCDOR")


print("\n2. STAGE 1 BOOKKEEPER: Human proposes, Sue confirms everything")
books("2026-09-22", "2026-10-31")
print(f"   Sue confirmed {confirm_all_proposed(db, 'accounting_sue', '2026-09-01', '2026-10-31')} entries")
print("   Skip to stage 3:", end=" ")
try:
    human.set_stage(db, "owner_alex", 3)
except PermissionError as e:
    print("BLOCKED:", e)
print("  ", human.set_stage(db, "owner_alex", 2, "two months, 471 confirmed by people, under 5% corrected"))

print("\n3. STAGE 2 ACCOUNTING AGENT: routine entries confirmed on its own")
books("2026-11-01", "2026-11-22")
post(db, "2026-11-18", "expense", 23412, "smallwares", "bank", "RD-1118", "bank", counterparty="Restaurant Depot")
r = human.auto_classify(db, "2026-11-01", "2026-11-22")
print(f"   {r}")
for q in review_queue(db, "2026-11-01", "2026-11-22"):
    print(f"   left for Sue: #{q['id']} {q['payee']} {q['amount']} ({q['why']})")
eco = db.execute("SELECT a.id FROM auto_audit a JOIN acct_txns t ON t.id=a.txn_id WHERE t.counterparty='Ecolab' LIMIT 1").fetchone()[0]
first = db.execute("SELECT a.id FROM auto_audit a JOIN acct_txns t ON t.id=a.txn_id WHERE t.counterparty='Sysco' LIMIT 1").fetchone()[0]
print("   audit:", human.audit(db, "accounting_sue", first, True))
print("   audit:", human.audit(db, "accounting_sue", eco, False, "supplies"))
print("   Ecolab's next charge:", end=" ")
post(db, "2026-11-22", "expense", 30500, "cleaning chemicals", "bank", "E-1122b", "bank", counterparty="Ecolab")
r = human.auto_classify(db, "2026-11-22", "2026-11-22")
print("left for a person" if r["left_for_people"] else "auto-confirmed")
print(human.tax_summary(db, "accounting_sue", "2026-11-01", "2026-11-22"))

print("\n4. INVENTORY ORDER UPDATES")
for m, unit, on_hand in (("white fish", "oz", 300), ("roma tomato", "oz", 400), ("shrimp", "oz", 200), ("cilantro", "oz", 30)):
    db.execute("INSERT INTO materials(material, unit, on_hand, perishable) VALUES (?,?,?,1)", (m, unit, on_hand))
for m, v, cost in (("white fish", "Harbor Seafood", 55), ("roma tomato", "Sysco", 9), ("shrimp", "Sysco", 70), ("cilantro", "Sysco", 25)):
    db.execute("INSERT INTO material_sources(material, vendor, lead_days, unit_cost_cents, preferred) VALUES (?,?,?,?,1)", (m, v, 2, cost))
po = db.execute("INSERT INTO po_drafts(vendor, order_by, delivery_on, status, made_by, sent_by) VALUES "
                "('Harbor Seafood','2026-11-20','2026-11-22','sent','hiwr','chef_marco')").lastrowid
db.execute("INSERT INTO po_lines VALUES (?,?,?,?,?,?,?)", (po, "white fish", "0085012300045", 6, 960, 52800, "forecast"))
human.po_event(db, po, "confirmed", "2026-11-20T15:10", "vendor", detail="delivery Sat 7-9 AM")
human.po_event(db, po, "shipped", "2026-11-22T06:05", "vendor", detail="ETA 7:40 AM")
issues, credit = human.receive(db, po, {"white fish": 800}, {"white fish": 58.5}, "2026-11-22T07:52", "cook_luis")
for line in human.order_updates(db, po):
    print("  ", line)

print("\n5. VENDOR PRICES FROM APPROVED SOURCES ONLY")
human.approve_source(db, "owner_alex", "sysco.com", "Sysco", "account_export", "order guide download from our own Sysco Shop account")
human.approve_source(db, "owner_alex", "usfoods.com", "US Foods", "account_export", "MOXe order guide export")
human.approve_source(db, "owner_alex", "harborseafood.example", "Harbor Seafood", "catalog_file", "weekly price sheet")
for url in ("https://cheap-wholesale.biz/prices", "http://shop.sysco.com/order-guide"):
    try:
        human.ingest_quotes(db, url, [], "2026-11-22", "accounting_sue")
    except PermissionError as e:
        print("   BLOCKED:", e)
print("  ", human.ingest_quotes(db, "https://shop.sysco.com/app/order-guide/export", [
    {"material": "roma tomato", "upc": "0004068200071", "pack_size": 400, "pack_label": "25 lb case", "pack_cost_cents": 3800, "lead_days": 2},
    {"material": "white fish", "upc": "0007400012345", "pack_size": 160, "pack_label": "10 lb case", "pack_cost_cents": 7900, "lead_days": 2},
    {"material": "shrimp", "upc": "0007400099881", "pack_size": 80, "pack_label": "5 lb bag", "pack_cost_cents": 5800, "lead_days": 2},
    {"material": "cilantro", "upc": "0004088900030", "pack_size": 32, "pack_label": "2 lb case", "pack_cost_cents": 800, "lead_days": 2}],
    "2026-11-22", "accounting_sue"))
print("  ", human.ingest_quotes(db, "https://www.usfoods.com/order-guide.csv", [
    {"material": "roma tomato", "upc": "0007218400025", "pack_size": 400, "pack_label": "25 lb case", "pack_cost_cents": 3200, "lead_days": 3},
    {"material": "cilantro", "upc": "0007218400310", "pack_size": 32, "pack_label": "2 lb case", "pack_cost_cents": 780, "lead_days": 3}],
    "2026-11-22", "accounting_sue"))
print("  ", human.ingest_quotes(db, "https://prices.harborseafood.example/week47.csv", [
    {"material": "white fish", "pack_size": 160, "pack_label": "10 lb case", "pack_cost_cents": 9100, "lead_days": 1},
    {"material": "shrimp", "pack_size": 80, "pack_label": "5 lb bag", "pack_cost_cents": 4900, "lead_days": 1}],
    "2026-11-22", "accounting_sue"))
sugg = human.suggest_vendors(db, "2026-11-22", {"roma tomato": 490, "white fish": 1300, "shrimp": 300, "cilantro": 40})
for sid, detail in sugg:
    print(f"   suggestion #{sid}: {detail}")
print("   (shrimp is cheaper at Harbor Seafood, but Harbor has an open SHIPMENT ticket, so it isn't suggested yet)")
print("   cook_luis accepts one:", end=" ")
try:
    human.decide_vendor(db, "cook_luis", sugg[0][0], True)
except PermissionError as e:
    print("BLOCKED:", e)
print("  ", human.decide_vendor(db, "chef_marco", sugg[0][0], True))

print("\n6. STAGE 3 TASK LIST AGENT")
try:
    human.build_tasks(db, "owner_alex", "2026-11-23", {})
except PermissionError as e:
    print("   BLOCKED:", e)
print("  ", human.set_stage(db, "owner_alex", 3, "agent ran clean for a month"))
db.execute("INSERT INTO margin_choices(sample, option, expected_cents, chosen_by, note) VALUES "
           "('11-16..11-22','labor',42536,'owner_alex','recover $425.36, hours to move 25.1')")
human.build_tasks(db, "owner_alex", "2026-11-23", {"finance": "accounting_sue", "manager": "chef_marco", "chef": "chef_marco"})
for who in ("accounting_sue", "chef_marco"):
    print(f"   {who}'s day:")
    for t in my_day(db, who, "2026-11-23"):
        print(f"      [{t['kind']}] {t['title']}" + (f"  (goal: {t['goal']})" if t["goal"] else ""))
print("   Human tries to send a PO or move money: not in its tools at any stage.")

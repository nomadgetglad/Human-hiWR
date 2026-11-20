"""Run: python human_demo.py
Human builds next week's staffing from reservations (restaurant) and production quota (boot shop),
plans materials with order-by dates, and writes a supply chain brief. Sample data."""
from hiwr_contacts import *
from hiwr_human import *

def usd(c): return f"${c / 100:,.2f}"
TODAY, WEEK = "2026-11-20", "2026-11-30"
db = connect()
add_plan_owner(db, "owner_alex", "setup")

db.executemany("INSERT INTO staffing_rules VALUES (?,?,?,?,?,?)", [
    ("cook", "covers", 40, 2, 16, 22), ("server", "covers", 25, 1, 16, 22), ("maker", "units", 2, 0, 8, 16)])
db.executemany("INSERT INTO staff VALUES (?,?,?,?)", [
    ("cook_luis", "cook", 1800, 3), ("cook_rosa", "cook", 2000, 3), ("cook_dan", "cook", 1700, 3), ("cook_ana", "cook", 1800, 3),
    ("server_kim", "server", 1200, 3), ("server_ben", "server", 1100, 3), ("server_mo", "server", 1150, 3),
    ("server_liz", "server", 1200, 3), ("maker_dee", "maker", 2600, 0), ("maker_sol", "maker", 2400, 0)])
days = [WEEK, "2026-12-01", "2026-12-02", "2026-12-03", "2026-12-04", "2026-12-05"]
cooks = {"2026-11-30": ["cook_luis", "cook_ana"], "2026-12-01": ["cook_luis", "cook_rosa", "cook_dan"],
         "2026-12-02": ["cook_rosa", "cook_ana"], "2026-12-03": ["cook_luis", "cook_rosa", "cook_dan"],
         "2026-12-04": ["cook_luis", "cook_rosa", "cook_dan", "cook_ana"], "2026-12-05": ["cook_luis", "cook_rosa", "cook_dan"]}
servers = {d: ["server_kim", "server_ben", "server_mo", "server_liz"] for d in days}
servers["2026-12-05"] = ["server_kim", "server_ben", "server_mo"]
for d in days:
    for m in cooks[d] + servers[d]:
        db.execute("INSERT INTO staff_availability VALUES (?,?,15,23)", (m, d))
    if d < "2026-12-05":
        for m in ("maker_dee", "maker_sol"):
            db.execute("INSERT INTO staff_availability VALUES (?,?,8,17)", (m, d))
db.executemany("INSERT INTO staff_booked_hours VALUES (?,?,?)", [("cook_luis", WEEK, 20), ("cook_rosa", WEEK, 16)])

guests = add_contact(db, "Reservations (sample)", "host_ana")["id"]
covers = dict(zip(days, [60, 70, 80, 90, 150, 180]))
for d, n in covers.items():
    for _ in range(n // 6):
        db.execute("INSERT INTO appointments(contact_id, starts_at, ends_at, party_size, kind) VALUES (?,?,?,6,'reservation')",
                   (guests, d + "T19:00", d + "T21:00"))
for d, u in zip(days[:5], [1, 2, 2, 1, 0]):
    if u:
        db.execute("INSERT INTO production_quota VALUES (?, 'boot_pair', ?)", (d, u))

db.executemany("INSERT INTO bill_of_materials VALUES (?,?,?)", [
    ("dinner_cover", "beef patties", 0.3), ("dinner_cover", "fries", 0.25), ("dinner_cover", "roma tomatoes", 0.1),
    ("boot_pair", "calf leather", 4.5), ("boot_pair", "leather soles", 1)])
db.executemany("INSERT INTO materials VALUES (?,?,?,?)", [
    ("beef patties", "each", 120, 1), ("fries", "lb", 50, 0), ("roma tomatoes", "lb", 20, 1),
    ("calf leather", "sq ft", 10, 0), ("leather soles", "pair", 10, 0)])
db.executemany("INSERT INTO material_sources VALUES (?,?,?,?,?,?)", [
    ("beef patties", "Sysco", 2, 110, 40, 1), ("fries", "Sysco", 2, 90, 30, 1),
    ("roma tomatoes", "Sysco", 3, 180, 10, 1), ("roma tomatoes", "Local farm", 1, 240, 5, 0),
    ("calf leather", "Tannery A", 14, 900, 10, 1), ("calf leather", "Leather depot", 5, 1150, 5, 0)])

sched = build_schedule(db, WEEK, 6, TODAY + "T14:00")
print(f"1. STAFFING for week of {WEEK} ({sched['status']})")
for p in sched["plan"]:
    print(f"   {p['day']} {p['role']:<6} need {p['need']}  ({p['because']})  -> {', '.join(p['assigned']) or 'none'}  {usd(p['cost_cents'])}")
print(f"   Payroll estimate {usd(sched['payroll_estimate_cents'])}, overtime {sched['overtime_hours']:g}h")
for g in sched["gaps"]:
    print(f"   GAP {g['day']} short {g['short']} {g['role']}")
print("   ->", sched["staffing_ticket"], "sent to the hiring matcher")

mats = material_plan(db, WEEK, 6, TODAY)
print("\n2. MATERIALS")
for m in mats:
    print(f"   {m['material']:<14} need {m['need']:g} {m['unit']}, on hand {m['on_hand']:g}, short {m['short']:g}"
          f"  -> {m.get('advice', 'in stock')}")
print("   Purchase drafts:", ", ".join(f"#{r['id']} {r['qty']:g} {r['material']} from {r['vendor']} {usd(r['cost_cents'])}"
                                     for r in db.execute("SELECT * FROM purchase_drafts")), "(manager sends)")

brief = supply_chain_brief(db, sched, mats, f"week of {WEEK}", TODAY)
open("supply_chain_brief.md", "w").write(brief)
print("\n3. DOCUMENT -> supply_chain_brief.md written")
print("   Check-in:", check_in(db, f"schedule:{WEEK}", "owner_alex", True, True, note="Reviewed draft; publish after Fri gap filled"))

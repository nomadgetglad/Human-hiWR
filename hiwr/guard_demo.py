"""Run: python guard_demo.py
Security patrol over sign-ins and reads, the data keeper keeping the databases in sync, and Human's
inventory plans under a stable, lean (risky) or growth mindset. Sample data."""
from hiwr_contacts import *
from hiwr_access import *
from hiwr_accounting import post
import hiwr_security as sec
import hiwr_datakeeper as keeper
import human_strategy as strat

db = connect()
add_plan_owner(db, "owner_alex", "setup")
for m, lv in (("accounting_sue", 3), ("chef_marco", 2), ("host_ana", 0), ("cook_luis", 0)):   # set at opening, weeks ago
    db.execute("INSERT INTO member_levels(member, level, set_by, at) VALUES (?,?,'owner_alex','2026-11-01 09:00:00')", (m, lv))
for m, a, b in (("accounting_sue", 8, 18), ("chef_marco", 10, 24), ("host_ana", 15, 23), ("cook_luis", 14, 23)):
    db.execute("INSERT INTO security_profiles VALUES (?,?,?)", (m, a, b))

print("1. SECURITY PATROL (Sunday night, 11-22)")
# normal history so devices are known
for m, dev in (("accounting_sue", "sue-laptop"), ("chef_marco", "kitchen-tablet"), ("host_ana", "host-ipad"), ("cook_luis", "kitchen-tablet")):
    sec.sign_in(db, m, "2026-11-10 16:00:00", True, dev, "73.12.40.15")
# someone guessing Luis's PIN from outside, then trying other accounts
for i in range(6):
    sec.sign_in(db, "cook_luis", f"2026-11-22 23:4{i}:10", False, "unknown-android", "185.220.101.7")
for m in ("host_ana", "chef_marco"):
    sec.sign_in(db, m, "2026-11-22 23:47:30", False, "unknown-android", "185.220.101.7")
print("   Luis types the right PIN at 23:50 from the kitchen:", "let in" if sec.sign_in(db, "cook_luis", "2026-11-22 23:50:00", True, "kitchen-tablet", "73.12.40.15") else "refused (locked 15 min)")
print("   ...and again at 00:05:", "let in" if sec.sign_in(db, "cook_luis", "2026-11-23 00:05:00", True, "kitchen-tablet", "73.12.40.15") else "refused")
# Sue signs in from a new laptop at 2 AM
sec.sign_in(db, "accounting_sue", "2026-11-23 02:05:00", True, "new-laptop", "98.21.7.33")
# Ana (Staff) pokes at things she can't see, then a manager account pulls many client records
for what in ("payroll", "order_costs", "client_records"):
    db.execute("INSERT INTO access_log(at, actor, role, contact_id, action) VALUES ('2026-11-22 23:20:00','host_ana','Staff',NULL,?)", (f"denied:{what}",))
ids = [db.execute("INSERT INTO contacts(kind, display_name, created_by) VALUES ('client', ?, 'pos')", (f"Guest {i}",)).lastrowid for i in range(26)]
for i, cid in enumerate(ids):
    db.execute("INSERT INTO access_log(at, actor, role, contact_id, action) VALUES (?, 'chef_marco', 'Manager', ?, 'view')",
               (f"2026-11-22 23:{30 + i // 4:02d}:{(i % 4) * 15:02d}", cid))
db.execute("INSERT INTO member_levels(member, level, set_by, at) VALUES ('host_ana', 1, 'owner_alex', '2026-11-22 23:58:00')")
al = sec.patrol(db, "2026-11-23 02:10:00", since_min=180)
print(sec.brief(db, "2026-11-23 02:10:00"))
first_new = next(a for a in al if a["kind"] == "new_device")
sec.decide(db, "owner_alex", first_new["id"], "was_them", "2026-11-23 07:30:00", "Sue bought a new laptop")
print(f"   Owner closes #{first_new['id']}: it was Sue's new laptop")
print("   Marco tries to close an alert:", end=" ")
try:
    sec.decide(db, "chef_marco", al[0]["id"], "was_them", "2026-11-23 07:31:00")
except PermissionError as e:
    print("BLOCKED:", e)
print("   Erasing sign-in history:", end=" ")
try:
    db.execute("DELETE FROM signin_events")
except Exception as e:
    print(e)

print("\n2. DATA KEEPER (synchronized and organized)")
a = add_contact(db, "Maria Lopez", "host_ana", phone="910-555-0101")["id"]
b = db.execute("INSERT INTO contacts(kind, display_name, created_by) VALUES ('client','M. Lopez','web')").lastrowid
db.execute("INSERT INTO contact_phones(contact_id, e164, is_primary) VALUES (?, '+19105550101', 1)", (b,))
for d in ("2026-11-18", "2026-11-19", "2026-11-21"):
    post(db, d, "revenue", 250000, "POS food", "pos", f"{d}-food", "pos", department="food")
db.execute("INSERT INTO dish_sales(day, hour, dish, qty) VALUES ('2026-11-22', 19, 'Fish tacos', 40)")
post(db, "2026-11-05", "expense", 21218, "misc", "bank", "LOW-1105", "bank", counterparty="Lowes")
post(db, "2026-11-12", "expense", 64000, "invoice", "invoice", "SYS-1112", "rcv", counterparty="Sysco")
post(db, "2026-11-14", "expense", 58000, "ACH", "bank", "ACH-1114", "bank", counterparty="SYSCO INC")
db.execute("INSERT INTO materials(material, unit, on_hand, perishable) VALUES ('corn tortilla','each',-120,1)")
po = db.execute("INSERT INTO po_drafts(vendor, order_by, delivery_on, status, made_by, sent_by) VALUES "
                "('Pine St Bakery','2026-11-19','2026-11-20','sent','hiwr','chef_marco')").lastrowid
db.execute("INSERT INTO po_events(po_id, event, at, by) VALUES (?, 'received', '2026-11-20T07:10', 'cook_luis')", (po,))
db.execute("INSERT INTO tickets(id, type, status, priority, subject, financial, opened_at) VALUES "
           "('SHP-0001','SHIPMENT','new',3,'PO #1 Harbor Seafood short', '{\"expected_credit_cents\": 8800}', '2026-11-02T07:52')")
for r in keeper.run(db, "2026-11-23", "2026-11-23 03:00"):
    print(f"   [{r['severity']}] {r['detail']}\n         fix: {r['proposed_fix']}")
dup = db.execute("SELECT id FROM datakeeper_findings WHERE check_name='duplicate_clients'").fetchone()[0]
keeper.decide(db, "accounting_sue", dup, True)
merge_contacts(db, a, b, "accounting_sue")
print("   Sue approves the merge; both histories are kept. Re-run:",
      sum(1 for r in keeper.run(db, "2026-11-23", "2026-11-23 03:05") if r["check_name"] == "duplicate_clients"), "duplicate findings open")

print("\n3. INVENTORY FOR THIS WEEKEND: PICK A MINDSET (Fri-Sat, party of 12 and the yacht club catering included)")
items = [  # per dish for the weekend; mu/sd from past samples
    {"dish": "Fish tacos", "material": "white fish", "unit": "oz", "per_dish": 5, "mu": 240, "sd": 40, "price": 1400, "unit_cost": 55, "spoil": 0.9},
    {"dish": "Burger", "material": "beef patty", "unit": "each", "per_dish": 1, "mu": 190, "sd": 30, "price": 1500, "unit_cost": 135, "spoil": 0.1},
    {"dish": "Tomato salad", "material": "roma tomato", "unit": "oz", "per_dish": 6, "mu": 80, "sd": 25, "price": 900, "unit_cost": 9, "spoil": 0.8},
    {"dish": "Margarita", "material": "lime", "unit": "each", "per_dish": 1.5, "mu": 150, "sd": 35, "price": 1100, "unit_cost": 20, "spoil": 0.5},
]
on_hand = {"white fish": 300, "beef patty": 400, "roma tomato": 1200, "lime": 80}
plans = strat.compare(items, on_hand)
print(strat.render(plans))
pick = next(p for p in plans if p["mindset"] == "stable")
print(f"\n   Owner picks {pick['label']}. Human's tasks go out in the owner's name:")
who = {"manager": "chef_marco", "chef": "chef_marco"}
for role_, t in pick["tasks"]:
    assign(db, "owner_alex", who[role_], "task", f"[Human, stable plan] {t}", due_on="2026-11-27")
for t in my_day(db, "chef_marco", "2026-11-27"):
    print(f"      {t['title']}")
print("  ", check_in(db, "strategy:2026-11-27", "owner_alex", works=True, intended_consequence=True,
                     note="Stable plan chosen; check runs-outs and waste Monday against the sample"))

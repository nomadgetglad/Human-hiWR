"""Run: python reschedule_demo.py
A reminder call where the client cancels a catering booking and reschedules it
against staff availability, with payroll, equipment and late-fee estimates.
All sample data. Fee policy and call-in pay rules are examples for the owner to set."""
from hiwr_contacts import *
from hiwr_scheduling import *

def usd(c): return f"${c / 100:,.2f}"

db = connect()
add_plan_owner(db, "owner_alex", "setup")
for m in ("manager_jo", "owner_alex"):
    for scope in ("fee", "payroll"):
        certify(db, m, scope, "owner_alex")

# --- sample business data ---
db.executemany("INSERT INTO staff VALUES (?,?,?,?)", [
    ("cook_luis", "cook", 1800, 3), ("cook_rosa", "cook", 2000, 3), ("cook_dan", "cook", 1700, 3),
    ("server_kim", "server", 1200, 3), ("server_ben", "server", 1100, 3)])
db.executemany("INSERT INTO equipment VALUES (?,?,?,?)", [("chafing set", 12000, 6000, 72)])
db.execute("INSERT INTO equipment_blackout VALUES ('chafing set', '2026-11-25')")   # rented out Wednesday
db.execute("INSERT INTO fee_policy VALUES ('catering', 48, 0.20, 1)")   # <48h notice = 20% fee, waived if rebooked
avail = {  # next week; Thursday is Thanksgiving, nobody scheduled
    "2026-11-23": [("cook_luis", 10, 22), ("server_kim", 15, 23)],
    "2026-11-24": [("cook_luis", 14, 22), ("cook_rosa", 15, 22), ("server_kim", 16, 22)],
    "2026-11-25": [("cook_rosa", 12, 22), ("cook_dan", 16, 21), ("server_ben", 16, 23)],
    "2026-11-27": [("cook_rosa", 15, 23), ("cook_dan", 15, 22), ("server_ben", 15, 23), ("server_kim", 15, 23)],
    "2026-11-28": [("cook_luis", 12, 23), ("cook_rosa", 12, 23), ("server_kim", 12, 23)],
}
for day, rows in avail.items():
    db.executemany("INSERT INTO staff_availability VALUES (?,?,?,?)", [(m, day, s, e) for m, s, e in rows])
db.executemany("INSERT INTO staff_booked_hours VALUES (?,?,?)", [
    ("cook_luis", "2026-11-23", 38), ("cook_rosa", "2026-11-23", 30), ("cook_dan", "2026-11-23", 20),
    ("server_kim", "2026-11-23", 25), ("server_ben", "2026-11-23", 32)])

client = add_contact(db, "Dana Whitfield", "host_ana", phone="910-555-0123")["id"]
record_consent(db, "910-555-0123", "transactional", "granted", "catering contract", "host_ana")
appt = db.execute("INSERT INTO appointments(contact_id, starts_at, ends_at, party_size, quote_cents, kind, details) "
                  "VALUES (?, '2026-11-21T17:00', '2026-11-21T21:00', 40, 180000, 'catering', 'Birthday, 40 guests')",
                  (client,)).lastrowid
db.executemany("INSERT INTO appointment_requirements VALUES (?,?,?,?,?)", [
    (appt, "role", "cook", 1, "cook_luis"), (appt, "role", "cook", 1, "cook_rosa"),
    (appt, "role", "server", 1, "server_kim"), (appt, "equipment", "chafing set", 1, "chafing set")])
db.execute("INSERT INTO appointment_events(appointment_id, stage, actor) VALUES (?, 'booked', 'host_ana')", (appt,))

NOW = "2026-11-20T10:00"
print("1. REMINDER CALL  Fri Nov 20, 10:00 AM - host_ana calls Dana about Sat Nov 21, 5-9 PM, 40 guests, quote $1,800")
print("   Dana: \"We have to cancel tomorrow. Can we do next weekend instead?\"")
log_call(db, appt, "host_ana", "cancel_and_reschedule", note="Family emergency; wants next weekend, evening")

impact = cancel(db, appt, "host_ana", NOW, reason="Client cancelled on reminder call")
print(f"\n2. CANCELLATION IMPACT  notice {impact['notice_hours']}h (policy: 48h)  late={impact['late']}")
print(f"   Late fee (20% of quote): {usd(impact['late_fee_cents'])}  waivable if rebooked within 14 days")
for c in impact["call_in_pay"]:
    print(f"   Call-in pay: {c['member']} {c['hours']}h = {usd(c['cents'])}")
for e in impact["equipment_cancel_fees"]:
    print(f"   Equipment cancel fee: {e['equipment']} {usd(e['cents'])}  -> anticipated costs")
print(f"   Sunk cost if nothing is waived: {usd(impact['sunk_cost_cents'])}")

print("\n3. OPEN SLOTS next 7 days, 4h, 2 cooks + 1 server + chafing set, same 5 PM start, cheapest first")
slots = find_slots(db, appt, "2026-11-22", 7, earliest=17, latest=17)
for s in slots:
    crew = ", ".join(c["member"] + (f" (OT {c['overtime_hours']}h)" if c["overtime_hours"] else "") for c in s["crew"])
    print(f"   {s['starts_at']}  payroll {usd(s['payroll_cents'])} + equipment {usd(s['equipment_cents'])}"
          f" = {usd(s['total_cost_cents'])}   [{crew}]")
print("   (Mon: one cook only. Wed: chafing set rented out. Thu: Thanksgiving, nobody scheduled.)")

choice = next(s for s in slots if s["starts_at"] == "2026-11-28T17:00")
original_cost = 4 * (1800 + 2000 + 1200) + 12000
new_id = rebook(db, appt, choice, "host_ana")
print(f"\n4. DANA PICKS Sat Nov 28, 5 PM -> appointment #{new_id}, linked to cancelled #{appt}")
print(f"   Cost: {usd(choice['total_cost_cents'])} vs original {usd(original_cost)}"
      f"  (+{usd(choice['total_cost_cents'] - original_cost)}, {choice['overtime_hours']}h overtime)")
print(f"   Margin on $1,800 quote: before {usd(180000 - original_cost)}, after {usd(180000 - choice['total_cost_cents'])}")
print("   Confirmation text allowed?", can_text(db, "910-555-0123"))

print("\n5. MONEY ACTIONS - nothing is charged or paid yet")
ids = propose_money_actions(db, impact, "host_ana", None, rebooked_within_14_days=True)
for i in ids:
    a = db.execute("SELECT * FROM money_actions WHERE id=?", (i,)).fetchone()
    print(f"   #{i} [{a['scope']}] {usd(a['amount_cents'])}  {a['description']}  -> {money_action_status(db, i)}")

print("\n6. CERTIFIED APPROVAL")
try:
    approve(db, ids[0], "host_ana")
except PermissionError as e:
    print("   Blocked:", e)
for i in ids:
    approve(db, i, "manager_jo")
    print(f"   #{i} -> {approve(db, i, 'owner_alex')}")

print("\n7. AUTHORITY CHECK-IN")
print("  ", check_in(db, f"appointment:{new_id}", "owner_alex", works=True, intended_consequence=True,
                     note="Rebooked, crew confirmed, fee waived, call-in pay approved; Luis OT accepted"))
print("\nClient history:")
for r in db.execute("SELECT event, detail FROM contact_events WHERE contact_id=? ORDER BY id", (client,)):
    print("  ", r["event"], r["detail"] or "")

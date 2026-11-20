"""Run: python processes_demo.py
1. Doctor's office booked out for weeks: earliest opening, waitlist, filling a cancellation.
2. Kitchen: move tables to seat a party of 10 at prime time, with upsell math.
3. Hot process (food cooling) and calculated process (made-to-order boots: ship-by + production test).
All sample data. Thresholds marked 'sample' are placeholders for the owner to set."""
from datetime import datetime, timedelta
from hiwr_contacts import *
from hiwr_processes import *

def usd(c): return f"${c / 100:,.2f}"
def nice(s): return datetime.fromisoformat(s).strftime("%a %b %d, %I:%M %p").replace(" 0", " ")

db = connect()
add_plan_owner(db, "owner_alex", "setup")
for m in ("manager_jo", "owner_alex"):
    certify(db, m, "fee", "owner_alex")

# ---------------------------------------------------------------- 1. clinic
print("=" * 70, "\n1. DOCTOR'S OFFICE - Dr. Patel is booked out for weeks\n")
db.execute("INSERT INTO staff VALUES ('dr_patel', 'doctor', 0, 0)")
d = datetime(2026, 11, 23)
while d < datetime(2027, 1, 15):
    if d.weekday() < 5:
        db.execute("INSERT INTO staff_availability VALUES ('dr_patel', ?, 9, 12)", (d.date().isoformat(),))
    d += timedelta(days=1)
filler = add_contact(db, "Existing patients (sample)", "front_desk_pat")["id"]
t = datetime(2026, 11, 23, 9)
fill = {}
while t < datetime(2026, 12, 14):
    if t.weekday() < 5 and 9 <= t.hour < 12:
        fill[t.isoformat(timespec="minutes")] = book_with_provider(db, filler, "dr_patel", t.isoformat(timespec="minutes"), "front_desk_pat")
    t += timedelta(minutes=30)
    if t.hour >= 12:
        t = t.replace(hour=9, minute=0) + timedelta(days=1)

jordan = add_contact(db, "Jordan Reyes", "front_desk_pat", phone="910-555-0177")["id"]
casey = add_contact(db, "Casey Ford", "front_desk_pat", phone="910-555-0188")["id"]
record_consent(db, "910-555-0177", "transactional", "granted", "patient portal", "front_desk_pat")
NOW = "2026-11-20T11:00"
opens = earliest_openings(db, "dr_patel", NOW)
print("Earliest openings:", ", ".join(nice(s) for s in opens), f"  ({len(fill)} slots booked before that)")
j_appt = book_with_provider(db, jordan, "dr_patel", opens[0], "front_desk_pat")
c_appt = book_with_provider(db, casey, "dr_patel", opens[1], "front_desk_pat")
join_waitlist(db, jordan, "dr_patel", j_appt, wants_before=opens[0])
join_waitlist(db, casey, "dr_patel", c_appt, wants_before=opens[1])
print(f"Jordan booked {nice(opens[0])}, Casey {nice(opens[1])}; both on the waitlist for anything earlier")

slot = "2026-12-02T10:00"
db.execute("INSERT INTO appointment_events(appointment_id, stage, actor, note) VALUES (?, 'cancelled', 'front_desk_pat', 'patient called')",
           (fill[slot],))
print(f"\nMon Nov 23, 10:00 AM - a patient cancels {nice(slot)}")
offer = offer_opening(db, "dr_patel", slot, "2026-11-23T10:00")
print(f"  Offer -> Jordan by {offer['channel']}, held until {offer['hold_until'][11:]}: \"{offer['message']}\"")
new_j, old_j = accept_offer(db, offer["waitlist_id"], "2026-11-23T10:40", "front_desk_pat")
print(f"  Jordan replies YES at 10:40 -> booked {nice(slot)}; old {nice(opens[0])} released")
offer2 = offer_opening(db, "dr_patel", opens[0], "2026-11-23T10:41")
print(f"  Freed slot {nice(opens[0])} -> offered to Casey by {offer2['channel']} (no SMS consent on file, so staff call)")
print("  No visit reasons or clinical details are stored; HIPAA notes are in the plan.")

# ---------------------------------------------------------------- 2. kitchen
print("\n" + "=" * 70, "\n2. KITCHEN - party of 10 wants Sat 7 PM (prime time)\n")
db.executemany("INSERT INTO dining_tables VALUES (?,?,?,?)", [
    ("T1", 4, "A", 1), ("T2", 4, "A", 2), ("T3", 4, "A", 3), ("T4", 4, "A", 4),
    ("T5", 6, "B", 1), ("T6", 6, "B", 2), ("T7", 2, None, None), ("T8", 4, None, None)])
guests = add_contact(db, "Walk-in reservations (sample)", "host_ana")["id"]
for tbl, size in [("T1", 2), ("T4", 4), ("T5", 5), ("T6", 4)]:
    a = db.execute("INSERT INTO appointments(contact_id, starts_at, ends_at, party_size, kind) VALUES (?, '2026-11-28T19:00', '2026-11-28T21:00', ?, 'reservation')",
                   (guests, size)).lastrowid
    db.execute("INSERT INTO appointment_events(appointment_id, stage, actor) VALUES (?, 'booked', 'host_ana')", (a,))
    db.execute("INSERT INTO table_assignments(appointment_id, tables, actor) VALUES (?,?, 'host_ana')", (a, tbl))
print("7 PM now: T1 party of 2, T4 party of 4, T5 party of 5, T6 party of 4; T2, T3, T7, T8 free")
morgan = add_contact(db, "Morgan Lee", "host_ana", phone="910-555-0155")["id"]
party = db.execute("INSERT INTO appointments(contact_id, starts_at, ends_at, party_size, kind, details) VALUES (?, '2026-11-28T19:00', '2026-11-28T21:30', 10, 'reservation', 'Birthday, prime-time package')",
                   (morgan,)).lastrowid
db.execute("INSERT INTO appointment_events(appointment_id, stage, actor) VALUES (?, 'booked', 'host_ana')", (party,))
opts = seating_options(db, 10, "2026-11-28T19:00", "2026-11-28T21:30")
for i, o in enumerate(opts, 1):
    mv = "; ".join(f"party of {m['party_size']} {m['from']} -> {m['to']}" for m in o["moves"]) or "no moves"
    print(f"  Option {i}: {o['tables']} ({o['seats']} seats)  moves: {mv}")
apply_seating(db, party, opts[0], "host_ana")
print(f"  Chosen: option 1 (fewest guests moved). Moves logged; nothing overwritten.")
u = upsell_estimate(10, package_cents_pp=5500, usual_check_cents_pp=3800, extra_staff_cents=3 * 1200)
print(f"  Upsell (sample prices): package {usd(u['package_revenue_cents'])} vs usual {usd(u['usual_revenue_cents'])}"
      f", extra server 3h {usd(u['extra_staff_cents'])} -> net uplift {usd(u['net_uplift_cents'])}")
dep = request_money_action(db, "fee", u["deposit_cents"], "25% deposit, Morgan Lee party of 10, Sat 7 PM", "host_ana")
print(f"  Deposit {usd(u['deposit_cents'])} -> {money_action_status(db, dep)}")
approve(db, dep, "manager_jo"); print(f"  After manager_jo + owner_alex -> {approve(db, dep, 'owner_alex')}")

# ---------------------------------------------------------------- 3. processes
print("\n" + "=" * 70, "\n3. PROCESSES\n")
print("HOT - cooling a batch of chili (Food Code: 135F->70F in 2h, ->41F in 6h total)")
cool = start_cooling(db, "chili-1120", "2026-11-20T14:00", 160)
print("  15:50 reading 68F ->", record(db, cool, 1, "2026-11-20T15:50", "cook_luis", {"temp_f": 68}))
for item in sweep(db, "2026-11-20T20:15"):
    if item["class"] == "hot":
        print(f"  20:15 sweep: '{item['checkpoint']}' was due {item['was_due'][11:]} -> {item['action']}")
print("  20:25 reading 45F ->", record(db, cool, 2, "2026-11-20T20:25", "cook_luis", {"temp_f": 45}))
ca = log_corrective_action(db, "2026-11-20T20:30", cool, "Not at 41F within 6h", "45F at 6h25m", "Discarded batch", "manager_jo")
print(f"  Corrective action #{ca}: discarded batch, waiting for manager verification")

print("\nCALCULATED - made-to-order boots promised Fri Dec 18, 3 days shipping")
sam = add_contact(db, "Sam Carter", "owner_alex", phone="+1 302 555 0142")["id"]
order = create_order(db, "boots_made_to_order", "ship", [{"item": "Western boot, roper toe", "specs": {"size": "10.5D"}}],
                     "owner_alex", contact_id=sam, due_at="2026-12-18")
stages = {"pattern": 1, "cut": 1, "stitched": 3, "lasted": 2, "soled": 1, "finished": 1, "qc_passed": 1}
qc_rule = {"min": {"sole_bond_lbs": 40, "stitches_per_inch": 8}}   # sample test thresholds
ship = start_order_shipment(db, order, "2026-12-18T17:00", 3, stages, qc_rule)
print("  Worked back from delivery:")
for cp in db.execute("SELECT seq, name, due_at FROM process_checkpoints WHERE process_id=? ORDER BY seq", (ship,)):
    print(f"    {cp['name']:<10} due {nice(cp['due_at'])}")
print("  pattern   ->", record(db, ship, 1, "2026-12-06T12:00", "maker_dee"))
print("  cut       ->", record(db, ship, 2, "2026-12-07T15:00", "maker_dee"))
for item in (i for i in sweep(db, "2026-12-11T08:00") if i["class"] == "calculated"):
    print(f"  Dec 11 sweep: '{item['checkpoint']}' missed -> {item['action']}")
print("  stitched  ->", record(db, ship, 3, "2026-12-11T10:00", "maker_dee"))
for s, at in [(4, "2026-12-12T16:00"), (5, "2026-12-13T15:00"), (6, "2026-12-14T15:00")]:
    record(db, ship, s, at, "maker_dee")
print("  lasted, soled, finished recorded on time (buffer recovered)")
qc = record(db, ship, 7, "2026-12-15T10:00", "qc_ray", {"sole_bond_lbs": 36, "stitches_per_inch": 9})
print("  PRODUCTION TEST sole bond 36 lbs (min 40), 9 stitches/in ->", qc)
log_corrective_action(db, "2026-12-15T10:15", ship, "QC sole bond below 40 lbs", "36 lbs", "Re-sole and retest", "qc_ray")
for item in sweep(db, "2026-12-15T18:00"):
    if item["class"] == "calculated":
        print(f"  Dec 15 sweep: '{item['checkpoint']}' -> {item['action']}")
t = db.execute("SELECT * FROM tickets").fetchone()
print(f"  Ticket {t['id']} priority {t['priority']}: {t['subject']}")

print("\nCHECK-INS")
print("  waitlist fill:", check_in(db, f"appointment:{new_j}", "owner_alex", True, True))
print("  party seating:", check_in(db, f"appointment:{party}", "owner_alex", True, True))
print("  boots order:  ", check_in(db, f"order:{order}", "owner_alex", True, False,
                                  note="Works, but ship date at risk after QC fail; client to be called"))

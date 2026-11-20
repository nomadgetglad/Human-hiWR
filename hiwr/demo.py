"""Run: python demo.py  -- walks a restaurant, a boot shop and a clinic through the same records."""
import json
from hiwr_contacts import *

db = connect()  # pass a file path, e.g. connect("hiwr.db"), to keep the data

# --- client records, dedupe, consent ---
maria = add_contact(db, "Maria Lopez", "host_ana", phone="(910) 555-0101", language="es")["id"]
print("Second Maria with same number ->", add_contact(db, "maria lopez", "host_ben", phone="910-555-0101"))
record_consent(db, "9105550101", "transactional", "granted", "booking form", "host_ana")
print("Can text Maria updates?", can_text(db, "9105550101"))
print("Can text Maria promos?", can_text(db, "9105550101", "marketing"))

# --- restaurant: reservation + live order to the line ---
add_contact(db, "Sysco Wilmington", "manager_jo", phone="910-555-0199", kind="vendor")
book_appointment(db, maria, "2026-11-21T19:30", "host_ana", party_size=4)
o = create_order(db, "restaurant_live", "dine_in", [
    {"item": "Burger", "specs": {"temp": "medium", "no": ["tomato"], "allergy": "nightshade"}},
    {"item": "Fries", "qty": 2}], "server_kim", contact_id=maria, source_ref="POS-1042")
for s in ("fired", "cooking", "ready"):
    advance_order(db, o, s, "cook_luis")

# --- boot shop: made-to-order job with measurements, shipped ---
sam = add_contact(db, "Sam Carter", "owner", phone="+1 302 555 0142")["id"]
b = create_order(db, "boots_made_to_order", "ship", [
    {"item": "Western boot, roper toe", "specs": {"size": "10.5D", "left_ball_in": 9.4, "right_ball_in": 9.5,
                                                  "instep_in": 9.8, "leather": "chestnut calf"}}],
    "owner", contact_id=sam, due_at="2027-01-15")
for s in ("pattern", "cut", "stitched"):
    advance_order(db, b, s, "maker_dee")
try:
    advance_order(db, b, "pattern", "maker_dee")
except ValueError as e:
    print("Blocked:", e)

# --- doctor's office: check-in board, no clinical data, every read logged ---
pat = add_contact(db, "Jordan Reyes", "front_desk_pat", phone="910-555-0177")["id"]
try:
    book_appointment(db, pat, "2026-11-21T09:00", "front_desk_pat", kind="clinic_visit",
                     workflow="clinic_checkin", details="follow-up for diabetes")
except ValueError as e:
    print("Blocked:", e)
a = book_appointment(db, pat, "2026-11-21T09:00", "front_desk_pat", kind="clinic_visit", workflow="clinic_checkin")
for s in ("confirmed", "arrived", "checked_in"):
    advance_appointment(db, a, s, "front_desk_pat")

print("\nProduction board:")
for r in db.execute("SELECT workflow, client, item, current_stage, station FROM v_production_board"):
    print("  ", dict(r))
print("Check-in board:")
for r in db.execute("SELECT * FROM v_checkin_board WHERE current_stage IS NOT NULL"):
    print("  ", dict(r))

print("\nCook sees:", get_contact(db, maria, "cook_luis", "cook"))
print("Manager sees phones:", get_contact(db, maria, "manager_jo", "manager")["phones"])
print("STOP from Maria ->", handle_inbound_sms(db, "9105550101", "stop"), "| can text now?", can_text(db, "9105550101"))
print("Access log rows:", db.execute("SELECT COUNT(*) FROM access_log").fetchone()[0])
try:
    db.execute("DELETE FROM contact_events")
except Exception as e:
    print("Blocked:", e)

# --- authority: certified approval for refunds and payroll ---
print("\n--- Authority ---")
add_plan_owner(db, "owner_alex", "setup")
certify(db, "manager_jo", "refund", "owner_alex")
certify(db, "owner_alex", "refund", "owner_alex")
try:
    certify(db, "cook_luis", "payroll", "server_kim")
except PermissionError as e:
    print("Blocked:", e)
r = request_money_action(db, "refund", 2450, "Refund Maria, burger sent with tomato", "server_kim", ticket_id="DSP-0007")
try:
    approve(db, r, "cook_luis")
except PermissionError as e:
    print("Blocked:", e)
print("After first approval:", approve(db, r, "manager_jo"))
print("After second approval:", approve(db, r, "owner_alex"))
print("Check-in on refund:", check_in(db, f"money_action:{r}", "owner_alex", works=True, intended_consequence=True,
                                       note="Refund shows on card statement, ticket closed"))

# --- owner assigns the day ---
d = "2026-11-21"
assign(db, "owner_alex", "host_ana", "call", "Confirm Maria's 7:30 reservation", due_on=d, contact_id=maria)
assign(db, "owner_alex", "host_ana", "analysis", "Count no-shows this month", due_on=d, goal="Cut no-shows below 5%")
t = assign(db, "owner_alex", "host_ana", "task", "Restock host stand menus", due_on=d)
update_assignment(db, t, "done", "host_ana")
try:
    assign(db, "host_ana", "cook_luis", "task", "Clean fryer")
except PermissionError as e:
    print("Blocked:", e)
print("Ana's day:")
for item in my_day(db, "host_ana", d):
    print("  ", item)

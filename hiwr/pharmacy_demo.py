"""Run: python pharmacy_demo.py
Prescription order status at a pharmacy. Sample data; no drug names are stored anywhere."""
from hiwr_contacts import *
from hiwr_access import *
from hiwr_pharmacy import *

D = "2026-11-20"
db = connect()
add_plan_owner(db, "owner_alex", "setup")
set_level(db, "rph_nguyen", 2, "owner_alex"); set_level(db, "tech_lily", 0, "owner_alex")
grant_task(db, "rph_nguyen", "pharmacist_verify", "owner_alex", D)
print("   Pharmacist tries to certify a tech to verify:", end=" ")
try:
    grant_task(db, "tech_lily", "pharmacist_verify", "rph_nguyen", D)
except PermissionError as e:
    print("BLOCKED:", e)

p1 = add_contact(db, "Jordan Reyes", "tech_lily", phone="910-555-0177")["id"]
p2 = add_contact(db, "Casey Ford", "tech_lily", phone="910-555-0188")["id"]
p3 = add_contact(db, "Morgan Lee", "tech_lily", phone="910-555-0155")["id"]
record_consent(db, "910-555-0177", "transactional", "granted", "pharmacy counter", "tech_lily")
record_consent(db, "910-555-0155", "transactional", "granted", "pharmacy app", "tech_lily")

def step(rx, stage, at, who, **kw):
    try:
        n = advance(db, rx, stage, D + at, who, **kw)
        print(f"   {at[1:]} {who:<10} {stage:<20}" + (f" -> {n['channel']}: \"{n['text']}\"" if n else ""))
    except PermissionError as e:
        print(f"   {at[1:]} {who:<10} {stage:<20} BLOCKED: {e}")

print("\n1. JORDAN, Rx 4471203, promised in 2 hours")
a = new_rx(db, p1, "RX-4471203", D + "T09:00", 2, "tech_lily")
step(a, "insurance_check", "T09:05", "tech_lily")
step(a, "filling", "T09:10", "tech_lily")
step(a, "pharmacist_verified", "T09:30", "tech_lily")
step(a, "pharmacist_verified", "T09:35", "rph_nguyen")
step(a, "ready", "T09:40", "tech_lily")
step(a, "picked_up", "T15:00", "tech_lily")
step(a, "picked_up", "T15:01", "tech_lily", id_checked=True)

print("\n2. CASEY, Rx 4471288, insurance hold then out of stock")
b = new_rx(db, p2, "RX-4471288", D + "T10:00", 24, "tech_lily")
step(b, "insurance_check", "T10:05", "tech_lily")
step(b, "insurance_hold", "T10:10", "tech_lily", note="prior authorization needed")
step(b, "filling", "T10:20", "tech_lily")
step(b, "resume", "T13:00", "tech_lily", note="insurer approved")
step(b, "filling", "T13:05", "tech_lily")
step(b, "out_of_stock", "T13:10", "tech_lily", note="Mon Nov 23")

print("\n3. MORGAN, ready since Nov 11, not picked up")
c = new_rx(db, p3, "RX-4469001", "2026-11-11T08:00", 4, "tech_lily")
for st, who in [("insurance_check", "tech_lily"), ("filling", "tech_lily"), ("pharmacist_verified", "rph_nguyen"), ("ready", "tech_lily")]:
    advance(db, c, st, "2026-11-11T09:00", who)

print("   Controlled substance:", end=" ")
try:
    new_rx(db, p3, "RX-4471300", D + "T11:00", 4, "tech_lily", controlled=True)
except ValueError as e:
    print("NOT TRACKED:", e)

print("\n4. PICKUP BOARD, Fri Nov 20, 4 PM")
for r in board(db, D + "T16:00"):
    print(f"   {r['rx']:<11} {r['patient']:<10} {r['stage']:<32} promised {r['promised'][5:].replace('T', ' ')}"
          + ("  LATE" if r["late"] else "") + (f"  {r['flag']}" if r["flag"] else ""))
print("   Morgan's reminder:", NOTICE["return_warning"].format(store="Main St Pharmacy", ref="9001", date="2026-11-21"))
print("\n   Stored per order: Rx number, stage, times, staff ID. No drug, dose or diagnosis.")

"""Run: python staff_demo.py
A. Chef opens the Staff section, finds a same-night dishwasher, and the fill is documented and accounted for.
B. The dishwasher takes a 4-week prime-time temp agreement; both sides' conditions are tracked.
C. A production foreman searches for a certified welder (AWS + OSHA) for third shift.
All people and numbers are sample data."""
import json
from hiwr_contacts import *
from hiwr_hiring import *
from hiwr_staff import *

db = connect()
add_plan_owner(db, "owner_alex", "setup")
for m in ("manager_jo", "accounting_sue"):
    certify(db, m, "payroll", "owner_alex")
db.executemany("INSERT INTO staffing_rules VALUES (?,?,?,?,?,?)", [
    ("cook", "covers", 40, 2, 16, 22), ("dishwasher", "covers", 75, 1, 17, 23)])
db.executemany("INSERT INTO staff VALUES (?,?,?,?)", [
    ("cook_luis", "cook", 1800, 3), ("cook_rosa", "cook", 2000, 3), ("cook_dan", "cook", 1700, 3), ("cook_ana", "cook", 1800, 3),
    ("dish_ray", "dishwasher", 1500, 3), ("dish_eve", "dishwasher", 1500, 3)])
TODAY = "2026-12-04"
for m in ("cook_luis", "cook_rosa", "cook_dan", "cook_ana", "dish_ray", "dish_eve"):
    db.execute("INSERT INTO staff_availability VALUES (?,?,15,23)", (m, TODAY))
guests = add_contact(db, "Reservations (sample)", "host_ana")["id"]
for _ in range(25):
    db.execute("INSERT INTO appointments(contact_id, starts_at, ends_at, party_size, kind) VALUES (?,?,?,6,'reservation')",
               (guests, TODAY + "T19:00", TODAY + "T21:00"))
hr_request(db, "cook_dan", "time_off", "2026-12-01T09:00", "2026-12-24", "2026-12-26", "family travel")
hr_request(db, "cook_ana", "pay_question", "2026-12-03T16:00", detail="overtime on last check")
hr_request(db, "dish_eve", "swap", "2026-12-02T11:00", "2026-12-12", "2026-12-12", "swap Sat with Ray")

# Ray calls out at 2 PM; time off for him today is approved, which removes his availability
rid = hr_request(db, "dish_ray", "time_off", "2026-12-04T14:00", TODAY, TODAY, "sick")
decide_hr(db, rid, "approved", "chef_marco", "2026-12-04T14:02", "feel better")

print("A. STAFF SECTION - Fri Dec 4, 2:05 PM, chef_marco")
v = staff_section(db, TODAY)
print(f"   Tonight: {v['demand']['covers']} covers")
for r in v["roles"]:
    print(f"   {r['role']:<10} need {r['need']} ({r['because']}), on shift: {', '.join(r['on_shift'])}  short {r['short']}")
print("   Pressing:")
for p in v["pressing"]:
    print("    -", p)

pos = db.execute("INSERT INTO positions(role, title, hourly_cents, opened_by) VALUES ('dishwasher', 'Dishwasher, prime time', 1600, 'owner_alex')").lastrowid
def cand(name, months, days, start, end, phone, lang="en"):
    cid = db.execute("INSERT INTO candidates(name, phone, language) VALUES (?,?,?)", (name, phone, lang)).lastrowid
    db.execute("INSERT INTO candidate_experience VALUES (?, 'dishwasher', ?, 'sample')", (cid, months))
    for wd in days:
        db.execute("INSERT INTO candidate_availability VALUES (?,?,?,?)", (cid, wd, start, end))
    return cid
marcus = cand("Marcus Bell", 18, [4, 5], 16, 24, "910-555-0161")
tia = cand("Tia Owens", 30, [4, 5, 6], 17, 23, "910-555-0162", "es")
gus = cand("Gus Hall", 6, [0, 1, 2], 16, 23, "910-555-0163")
nina = cand("Nina Park", 4, [4], 17, 23, "910-555-0164")

print("\n   Find same-night dishwasher, 5-11 PM:")
cands = find_same_night(db, "dishwasher", TODAY + "T17:00", TODAY + "T23:00", "2026-12-04T14:05")
for c in cands:
    print(f"    {c['name']:<11} {c['months']} months, reliability {c['reliability']}, language {c['language']}")
offers, hold = send_offers(db, cands, "dishwasher", TODAY + "T17:00", TODAY + "T23:00", 1600, "2026-12-04T14:06")
print(f"   Texts sent to the top {len(offers)}, held until {hold[11:]}. Answers:")
names = {c["candidate_id"]: c["name"] for c in cands}
for oid, ans, at in [(offers[1], "yes", "2026-12-04T14:12"), (offers[0], "yes", "2026-12-04T14:15"), (offers[2], "no", "2026-12-04T14:20")]:
    o = db.execute("SELECT candidate_id FROM shift_offers WHERE id=?", (oid,)).fetchone()
    print(f"    {names[o['candidate_id']]:<11} {ans} at {at[11:]} -> {answer_offer(db, oid, ans, at)}")
winner = db.execute("SELECT candidate_id FROM shift_offers WHERE answer='yes'").fetchone()["candidate_id"]
print(f"   Filled by {names[winner]}.")

# B. the fill becomes a prime-time temp agreement: tonight plus Fri/Sat for 3 more weeks
shift_days = ["2026-12-04", "2026-12-05", "2026-12-11", "2026-12-12", "2026-12-18", "2026-12-19", "2026-12-26"]
agr = create_agreement(db, pos, winner, "temporary", "2026-12-04", "2026-12-26", 1600, 5,
                       [(d + "T17:00", d + "T23:00") for d in shift_days], "owner_alex", "2026-12-04T14:30")
open("contract_dishwasher_temp.md", "w").write(contract_text(db, agr))
print(f"\nB. TEMP AGREEMENT #{agr}: {len(shift_days)} prime-time shifts, $16/h, 5h minimum -> contract_dishwasher_temp.md")
member = db.execute("SELECT member FROM work_agreements WHERE id=?", (agr,)).fetchone()["member"]
sh = {r["starts_at"][:10]: r["id"] for r in db.execute("SELECT * FROM shifts WHERE agreement_id=?", (agr,))}
script = {
    "2026-12-04": [("clock_in", "T16:55"), ("clock_out", "T23:05")],
    "2026-12-05": [("clock_in", "T17:25"), ("clock_out", "T23:00")],
    "2026-12-11": [("cancelled_by_employer", "T14:00", "slow night, private event cancelled")],
    "2026-12-12": [("clock_in", "T17:00"), ("clock_out", "T20:30", "manager OK, slow night")],
    "2026-12-18": [],
    "2026-12-19": [("clock_in", "T16:58"), ("clock_out", "T23:00")],
    "2026-12-26": [("clock_in", "T17:02"), ("clock_out", "T22:10")],
}
actions = []
for d, evs in script.items():
    for e in evs:
        shift_event(db, sh[d], e[0], d + e[1], member if e[0].startswith("clock") else "chef_marco", e[2] if len(e) > 2 else None)
    r = close_shift(db, sh[d], d + "T23:30", "chef_marco")
    if r["money_action"]:
        actions.append(r["money_action"])
    print(f"   {d}: {r['hours']:>4}h worked, pay {usd(r['pay_cents'])}" + (f", employer owes {usd(r['owed_cents'])}" if r["owed_cents"] else "")
          + ("".join(f"\n              {n}" for n in r["notes"])))
total = sum(db.execute("SELECT amount_cents FROM money_actions WHERE id=?", (i,)).fetchone()[0] for i in actions)
print(f"   Payroll entries: {len(actions)}, total {usd(total)} + employer tax est. {usd(round(total * EMPLOYER_TAX_EST))} (7.65%)")
print(f"   Status before approval: {money_action_status(db, actions[0])}")
for i in actions:
    approve(db, i, "manager_jo"); approve(db, i, "accounting_sue")
print(f"   After manager_jo + accounting_sue: {money_action_status(db, actions[0])} (all {len(actions)})")
print(f"   Reliability: {member} {reliability(db, 'worker', member)}, employer {reliability(db, 'employer', 'employer')}")
print("   Check-in:", check_in(db, f"agreement:{agr}", "owner_alex", True, True, note="All shifts accounted; one NCNS warning on file"))

# C. foreman
print("\nC. FOREMAN - third shift welder, Mon-Thu 10 PM-6 AM, start Jan 4")
def welder(name, months, certs, days):
    cid = db.execute("INSERT INTO candidates(name) VALUES (?)", (name,)).lastrowid
    db.execute("INSERT INTO candidate_experience VALUES (?, 'welder', ?, 'sample')", (cid, months))
    for cert, exp, ok in certs:
        db.execute("INSERT INTO candidate_certs(candidate_id, cert, expires_on, verified_by) VALUES (?,?,?,?)",
                   (cid, cert, exp, "foreman_bo" if ok else None))
    for wd in days:
        db.execute("INSERT INTO candidate_availability VALUES (?,?,22,30)", (cid, wd))
welder("Rico Vance", 72, [("aws_certified_welder", "2027-03-31", True), ("osha_30", None, True)], [0, 1, 2, 3])
welder("Joy Tran", 40, [("aws_certified_welder", "2027-01-31", True), ("osha_10", None, False)], [0, 1, 2, 3])
welder("Hal Burke", 120, [("aws_certified_welder", "2026-12-31", True), ("osha_10", None, True)], [0, 1, 2, 3])
welder("Kim Ott", 30, [("aws_certified_welder", "2027-06-30", True), ("osha_10", None, True)], [0, 1])
found, near = search_certified(db, "welder", ["aws_certified_welder"], ["osha_10", "osha_30"], [0, 1, 2, 3], 22, 30,
                               "2027-01-04", base_cents=2600, night_diff_cents=200)
for f in found:
    print(f"   READY  {f['name']:<10} {f['months']} months, {f['certs']}, {f['rate']}")
for n in near:
    print(f"   NOT YET {n['name']:<9} " + "; ".join(n["problems"]))

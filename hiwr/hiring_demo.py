"""Run: python hiring_demo.py
Staffing gap -> STAFFING ticket -> ranked candidates -> swipe match -> a person decides -> payroll waits for approval.
All candidates are sample data."""
import json
from hiwr_contacts import *
from hiwr_hiring import *

def usd(c): return f"${c / 100:,.2f}"

db = connect()
add_plan_owner(db, "owner_alex", "setup")
for m in ("manager_jo", "owner_alex"):
    certify(db, m, "payroll", "owner_alex")

db.executemany("INSERT INTO staff VALUES (?,?,?,?)", [
    ("cook_luis", "cook", 1800, 3), ("cook_rosa", "cook", 2000, 3), ("server_kim", "server", 1200, 3)])
avail = {"2026-11-30": [("cook_luis", 10, 22), ("server_kim", 15, 23)],
         "2026-12-01": [("cook_luis", 14, 22), ("cook_rosa", 15, 22), ("server_kim", 16, 22)],
         "2026-12-02": [("cook_rosa", 12, 22), ("server_kim", 15, 23)],
         "2026-12-03": [("cook_luis", 14, 22), ("cook_rosa", 15, 22), ("server_kim", 16, 22)],
         "2026-12-04": [("cook_luis", 14, 23), ("cook_rosa", 15, 23), ("server_kim", 16, 23)],
         "2026-12-05": [("cook_luis", 12, 23), ("cook_rosa", 12, 23), ("server_kim", 12, 23)]}
for day, rows in avail.items():
    db.executemany("INSERT INTO staff_availability VALUES (?,?,?,?)", [(m, day, s, e) for m, s, e in rows])

needs = {wd: [("cook", 2, 16, 22), ("server", 1, 16, 22)] for wd in range(6)}   # Mon-Sat dinner
gaps = find_gaps(db, needs, "2026-11-30", 6)
print("1. GAPS, week of Nov 30 (dinner needs 2 cooks 4-10 PM):")
for g in gaps:
    print(f"   {g['day']} short {g['short']} {g['role']}")
tid = open_staffing_ticket(db, gaps, "2026-11-20T14:00", "hiwr")
print("   ->", tid, db.execute("SELECT subject FROM tickets WHERE id=?", (tid,)).fetchone()[0])

pos = db.execute("INSERT INTO positions(role, title, required_certs, preferred_certs, min_experience_months, work_style, hourly_cents, opened_by) "
                 "VALUES ('cook', 'Line cook, dinner', ?, ?, 6, ?, 1800, 'owner_alex')",
                 (json.dumps(["food_handler"]), json.dumps(["servsafe_manager"]),
                  json.dumps({"fast_paced": "yes", "team_based": "yes", "late_nights": "yes"}))).lastrowid

def cand(name, certs, months, avail_days, style, phone=None, lang="en"):
    cid = db.execute("INSERT INTO candidates(name, phone, language) VALUES (?,?,?)", (name, phone, lang)).lastrowid
    for cert, exp, verified in certs:
        db.execute("INSERT INTO candidate_certs(candidate_id, cert, expires_on, verified_by, verified_at) VALUES (?,?,?,?,?)",
                   (cid, cert, exp, "manager_jo" if verified else None, "2026-11-19" if verified else None))
    if months:
        db.execute("INSERT INTO candidate_experience VALUES (?, 'cook', ?, 'previous kitchen')", (cid, months))
    for wd in avail_days:
        db.execute("INSERT INTO candidate_availability VALUES (?,?,15,23)", (cid, wd))
    for q, a in style.items():
        db.execute("INSERT INTO candidate_work_style VALUES (?,?,?)", (cid, q, a))
    return cid

ana = cand("Ana Ruiz", [("food_handler", "2028-03-01", True), ("servsafe_manager", "2029-06-30", True)], 36, [0, 2],
           {"fast_paced": "yes", "team_based": "yes", "late_nights": "yes"}, lang="es")
jamie = cand("Jamie Cole", [("food_handler", "2027-09-15", False)], 8, [0, 1, 2, 3, 4, 5],
             {"fast_paced": "yes", "team_based": "sometimes", "late_nights": "yes"})
chris = cand("Chris Ward", [("food_handler", "2026-08-31", True)], 60, [0, 2], {"fast_paced": "yes"})
pat = cand("Pat Kim", [], 24, [0], {"fast_paced": "yes", "team_based": "yes"})
lee = cand("Lee Grant", [("food_handler", "2027-12-01", True)], 3, [0, 2], {"fast_paced": "no"})

ranked, out = rank_candidates(db, pos, gaps, start_on="2026-11-30")
print("\n2. RANKED for Line cook, dinner (score = availability 50 + experience 30 + work style 20, +3 per preferred cert)")
for r in ranked:
    print(f"   {r['score']:>5}  {r['candidate']:<11} " + "; ".join(r["reasons"]))
    for f in r["flags"]:
        print(f"          FLAG: {f}")
print("   Not eligible:")
for n in out:
    print(f"          {n['candidate']:<11} " + "; ".join(n["why"]))

print("\n3. SWIPE")
print("   Ana swipes yes on the shift posting -> match?", swipe(db, pos, ana, "candidate", "yes", "ana"))
print("   owner_alex swipes yes on Ana       -> match?", swipe(db, pos, ana, "employer", "yes", "owner_alex"))

print("\n4. A PERSON DECIDES")
try:
    decide(db, pos, jamie, "hire", "owner_alex", "covers every gap", tid, 24, "2026-11-30")
except ValueError as e:
    print("   Blocked:", e)
try:
    decide(db, pos, ana, "hire", "server_kim", "good fit", tid, 12, "2026-11-30")
except PermissionError as e:
    print("   Blocked:", e)
decide(db, pos, jamie, "interview", "owner_alex", "Available all week; verify food handler card at interview", tid)
pay = decide(db, pos, ana, "hire", "owner_alex", "Covers both gap nights, 36 months line experience, ServSafe Manager",
             tid, weekly_hours=12, start_on="2026-11-30")
print(f"   Ana hired by owner_alex. Payroll change #{pay}: {usd(db.execute('SELECT amount_cents FROM money_actions WHERE id=?', (pay,)).fetchone()[0])}/week"
      f" -> {money_action_status(db, pay)}")
approve(db, pay, "manager_jo")
try:
    approve(db, pay, "owner_alex")
except PermissionError as e:
    print("   Blocked: owner_alex made the hire, so", e)
certify(db, "accounting_sue", "payroll", "owner_alex")
print(f"   After manager_jo + accounting_sue -> {approve(db, pay, 'accounting_sue')}")
print("   Check-in:", check_in(db, tid, "owner_alex", True, True, note="Mon and Wed covered from Nov 30"))

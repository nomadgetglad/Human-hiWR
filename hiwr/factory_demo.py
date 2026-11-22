"""Run: python factory_demo.py
A pipe plant: owner-written procedures, per-pipe check-throughs, staffing quotas, rotation, and who sees what."""
import json, random
from datetime import date, timedelta
from hiwr_contacts import *
from hiwr_access import *
from hiwr_factory import *

def usd(c): return f"${c / 100:,.2f}"

D = "2026-11-20"
random.seed(2)
db = connect()
add_plan_owner(db, "owner_alex", "setup")
set_level(db, "foreman_bo", 1, "owner_alex"); set_level(db, "hr_lena", 2, "owner_alex")
grant_task(db, "foreman_bo", "foreman", "owner_alex", D)
grant_task(db, "hr_lena", "hr_manager", "owner_alex", D)
grant_task(db, "hr_lena", "roll_call", "owner_alex", D)

print("1. OWNER WRITES THE PROCEDURES (this is the 'training')")
sops = [
    ("cut", 1, "Measure twice against the work order length. Cut square within 1/16 inch. Deburr both ends before moving the pipe.",
     [{"name": "length_in", "type": "number", "min": "length_in - 0.0625", "max": "length_in + 0.0625"},
      {"name": "deburred", "type": "yes_no", "must": "yes"}]),
    ("bevel", 2, "Bevel both ends to 37.5 degrees, plus or minus 2.5. Leave a land of 1/16 inch.",
     [{"name": "bevel_deg", "type": "number", "min": 35, "max": 40}]),
    ("weld", 3, "Preheat the joint to between 150 and 300 F before the root pass. Keep interpass temperature at or below 500 F. "
     "Visually check for cracks, porosity and undercut. Take a photo of every finished weld.",
     [{"name": "preheat_f", "type": "number", "min": 150, "max": 300}, {"name": "interpass_f", "type": "number", "max": 500},
      {"name": "visual_ok", "type": "yes_no", "must": "yes"}, {"name": "weld_photo", "type": "photo"}]),
    ("hydrotest", 4, "Fill and bleed all air. Pressurize to 1.5 times the rated pressure. Hold for 10 minutes with no drop and no leaks.",
     [{"name": "test_psi", "type": "number", "min": "1.5 * rated_psi"}, {"name": "hold_min", "type": "number", "min": 10},
      {"name": "no_leaks", "type": "yes_no", "must": "yes"}]),
    ("coat", 5, "Blast to near-white metal. Apply coating to 12 to 16 mils dry film thickness. Check three spots.",
     [{"name": "dft_mils", "type": "number", "min": 12, "max": 16}]),
]
for st, seq, text, checks in sops:
    v = publish_sop(db, st, seq, text, checks, "owner_alex")
    print(f"   {st:<9} v{v}: {len(checks)} checks")
print("   Owner updates weld preheat after a customer spec change ->",
      f"weld v{publish_sop(db, 'weld', 3, sops[2][2].replace('150 and 300', '200 and 350'), [dict(sops[2][3][0], min=200, max=350)] + sops[2][3][1:], 'owner_alex')}")

print("\n2. WORKER ASKS, AI ANSWERS FROM THE SOP ONLY")
for q in ["What preheat do I need before the root pass?", "How long do I hold the hydrotest?", "Can I use the forklift?"]:
    print(f"   Q: {q}\n   A: {teach(db, q)}")

workers = {"ana": 2400, "ben": 2300, "cruz": 2600, "dee": 2500, "eli": 2200, "fay": 2400, "gil": 2700, "hal": 2300}
certs = {"ana": ["cut", "bevel", "coat"], "ben": ["cut", "bevel", "hydrotest"], "cruz": ["weld", "bevel"],
         "dee": ["weld", "hydrotest"], "eli": ["cut", "coat", "hydrotest"], "fay": ["coat", "bevel", "cut"],
         "gil": ["weld", "cut"], "hal": ["hydrotest", "coat", "cut"]}
for m, sts in certs.items():
    for st in sts:
        grant_task(db, m, f"station_{st}", "owner_alex", D)

print("\n3. CHECK-THROUGH, pipe P-1001 (12in Sch40, 36 in, rated 285 psi)")
spec = {"length_in": 36.0, "rated_psi": 285}
db.execute("INSERT INTO units VALUES ('P-1001', ?, ?)", (json.dumps(spec), D + "T07:00"))
for st, m, at, vals in [
    ("bevel", "cruz", "T07:10", {"bevel_deg": 37.5}),
    ("cut", "ana", "T07:15", {"length_in": 36.03, "deburred": "yes"}),
    ("bevel", "cruz", "T07:30", {"bevel_deg": 37.0}),
    ("weld", "ana", "T08:00", {}),
    ("weld", "cruz", "T08:40", {"preheat_f": 210, "interpass_f": 460, "visual_ok": "yes", "weld_photo": "img_0412.jpg"}),
    ("hydrotest", "dee", "T09:20", {"test_psi": 420, "hold_min": 10, "no_leaks": "yes"}),
]:
    try:
        r = check_through(db, "P-1001", st, m, D + at, vals)
        print(f"   {st:<9} {m:<5} SOP {r['sop']}: {'PASS' if r['passed'] else 'FAIL - ' + '; '.join(r['failures'])}")
    except PermissionError as e:
        print(f"   {st:<9} {m:<5} BLOCKED: {e}")
print("   Coat try:", end=" ")
try:
    check_through(db, "P-1001", "coat", "ana", D + "T09:40", {"dft_mils": 14})
except PermissionError as e:
    print("BLOCKED:", e)
print("   Retest after re-pressurizing:", check_through(db, "P-1001", "hydrotest", "dee", D + "T10:10",
                                                       {"test_psi": 430, "hold_min": 10, "no_leaks": "yes"})["passed"])
print("   Coat:", check_through(db, "P-1001", "coat", "ana", D + "T11:00", {"dft_mils": 14})["passed"])

print("\n4. STAFFING QUOTA from last week's output (target 60 pipes/day, 8h shift)")
start = date(2026, 11, 9)
rates_per_hour = {"cut": 6.0, "bevel": 7.5, "weld": 2.4, "hydrotest": 4.0, "coat": 5.0}
staffing = {"cut": ["ana", "eli"], "bevel": ["fay"], "weld": ["cruz", "dee", "gil"], "hydrotest": ["ben", "hal"], "coat": []}
for i in range(5):
    d = (start + timedelta(days=i)).isoformat()
    for st, ms in staffing.items():
        for m in ms or ["fay"]:
            h = 8 if ms else 2
            db.execute("INSERT INTO station_output VALUES (?,?,?,?,?)", (d, st, m, h, round(h * rates_per_hour[st] * random.uniform(0.9, 1.05))))
q, tot = staffing_quota(db, 60, 8, "2026-11-09", "2026-11-13", avg_rate_cents=sum(workers.values()) / len(workers))
for r in q:
    print(f"   {r['station']:<9} {r['pipes_per_worker_hour']:>5} pipes/worker-hr -> {r['workers']} workers (capacity {r['capacity']}/day)")
cents, done, per = actual_labor_cost(db, "2026-11-09", "2026-11-13", workers)
print(f"   Need {tot['workers']} workers, {usd(tot['labor_cents_per_day'])}/day, {usd(tot['labor_cents_per_pipe'])} labor per pipe"
      f"\n   Last week: only {done} pipes finished at {usd(per)} labor per pipe, because coat was staffed 2h a day and starved the line")
print(f"   Bottleneck: weld. With {len(workers)} certified workers on hand, short {max(0, tot['workers'] - len(workers))} -> STAFFING ticket for the hiring matcher")

print("\n5. ROTATION, Mon-Fri (only certified stations, max 2 days running on one)")
small = [dict(r) for r in q]
for r in small:
    r["workers"] = min(r["workers"], {"cut": 1, "bevel": 1, "weld": 3, "hydrotest": 2, "coat": 1}[r["station"]])
days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
plan, count = rotate(db, list(workers), small, days, D)
for d, today in plan:
    print(f"   {d}: " + " | ".join(f"{st} {', '.join(ms)}" for st, ms in today.items()))
print("   Days per station:", {m: dict(c) for m, c in count.items()})
weld_certified = [m for m in workers if can_do(db, m, "station_weld", D)]
if len(weld_certified) <= max(r["workers"] for r in small if r["station"] == "weld"):
    print(f"   Weld can't rotate: only {len(weld_certified)} people are certified for {len(weld_certified)} weld slots."
          " Suggest cross-training 2 more (e.g. ben, hal) so welders get relief days.")

print("\n6. WHO SEES WHAT")
for m in ("ana", "foreman_bo", "hr_lena", "owner_alex"):
    seen = [s for s in SECTION_TASK if section_allowed(db, m, s, D)]
    print(f"   {m:<11} sees: {', '.join(seen)}")
try:
    open_section(db, "foreman_bo", "hr_roll_call", D)
except PermissionError as e:
    print("   Foreman opens HR / roll call: BLOCKED:", e)

"""Run: python lab_demo.py
Lab: a house build with phases, inspection gates, subcontractor draws, retainage and crew payroll. Sample data."""
from datetime import date, timedelta
from hiwr_contacts import *
from hiwr_access import *
from hiwr_lab import *

db = connect()
add_plan_owner(db, "owner_alex", "setup")
for m in ("pm_dana", "accounting_sue"):
    set_level(db, m, 3, "owner_alex")
    grant_task(db, m, "subcontract_approval", "owner_alex", "2026-11-20")
    grant_task(db, m, "payroll_approval", "owner_alex", "2026-11-20")
client = add_contact(db, "The Hendersons", "owner_alex", phone="910-555-0190")["id"]
proj = db.execute("INSERT INTO lab_projects(name, contact_id, contract_cents, starts_on, target_end, permit_number) "
                  "VALUES ('Henderson house, 3 bed', ?, 38500000, '2027-01-04', '2027-07-30', 'BP-27-0142')", (client,)).lastrowid
phases = [("Site prep and footings", 1800000, "footing inspection"), ("Foundation", 1500000, "foundation inspection"),
          ("Framing", 2100000, "framing inspection"), ("Rough-in: plumbing, electrical, HVAC", 600000, "rough-in inspection"),
          ("Insulation", 300000, "insulation inspection"), ("Drywall and finishes", 3500000, "final inspection")]
for i, (n, lb, insp) in enumerate(phases, 1):
    db.execute("INSERT INTO lab_phases(project_id, seq, name, labor_budget_cents, inspection) VALUES (?,?,?,?,?)", (proj, i, n, lb, insp))
subs = [(4, "Cape Fear Plumbing", "rough and finish plumbing", 1850000, "2027-12-31"),
        (4, "Port City Electric", "rough and finish electrical", 2200000, "2027-10-31"),
        (4, "Coastal HVAC", "ductwork and equipment", 1600000, "2027-03-15")]
sid = {}
for ph, name, scope, amt, ins in subs:
    sid[name] = db.execute("INSERT INTO lab_subcontracts(project_id, phase_id, sub, scope, amount_cents, license_no, insurance_expires) "
                           "VALUES (?,?,?,?,?,?,?)", (proj, phase(db, proj, ph)["id"], name, scope, amt, "NC-LIC", ins)).lastrowid

def blocked(f, *a, **k):
    try: f(*a, **k); return "started"
    except PermissionError as e: return f"BLOCKED: {e}"

print("Henderson house, $385,000 contract, permit BP-27-0142, Jan 4 to Jul 30 2027\n")
print("1. PHASES AND INSPECTION GATES")
start_phase(db, proj, 1, "2027-01-04", "pm_dana"); inspect(db, proj, 1, "2027-01-15", "county_inspector", "pass")
start_phase(db, proj, 2, "2027-01-18", "pm_dana"); inspect(db, proj, 2, "2027-02-05", "county_inspector", "pass")
start_phase(db, proj, 3, "2027-02-08", "pm_dana")
print("   Footings and foundation passed. Framing started Feb 8.")
d = date(2027, 2, 8)
while d < date(2027, 3, 13):
    if d.weekday() < 5:
        for m, rate in [("carp_lead", 3400), ("carp_1", 2600), ("carp_2", 2600), ("carp_3", 2400)]:
            log_time(db, proj, 3, m, d.isoformat(), 9 if d >= date(2027, 3, 1) else 8, rate)
    d += timedelta(days=1)
print("   Rough-in try before framing inspection:", blocked(start_phase, db, proj, 4, "2027-03-10", "pm_dana"))
inspect(db, proj, 3, "2027-03-12", "county_inspector", "pass")
start_phase(db, proj, 4, "2027-03-15", "pm_dana")
print("   Framing passed Mar 12; rough-in started Mar 15.")

print("\n2. FAILED INSPECTION")
t = inspect(db, proj, 4, "2027-04-02", "county_inspector", "fail",
            notes="missing nail plates at 6 studs; box fill over limit in kitchen", responsible_sub="Port City Electric")
print(f"   Rough-in failed Apr 2 -> {t}: {db.execute('SELECT subject FROM tickets WHERE id=?', (t,)).fetchone()[0]}")
print("   Insulation try:", blocked(start_phase, db, proj, 5, "2027-04-05", "pm_dana"))

print("\n3. SUBCONTRACTOR DRAWS (10% retainage held)")
for name, pct, at, waiver, bc, note in [
    ("Cape Fear Plumbing", 0.5, "2027-04-03", "conditional", 0, None),
    ("Coastal HVAC", 0.5, "2027-04-03", "conditional", 0, None),
    ("Port City Electric", 0.5, "2027-04-03", None, 0, None),
    ("Port City Electric", 0.5, "2027-04-04", "conditional", 7500, "reinspection fee from failed rough-in"),
]:
    r = request_draw(db, sid[name], pct, at, lien_waiver=waiver, backcharge_cents=bc, backcharge_note=note)
    line = f"   {name:<19} to {int(pct*100)}%: gross {usd(r['gross'])}, retainage {usd(r['retainage'])}"
    line += f", backcharge {usd(r['backcharge'])}" if r["backcharge"] else ""
    line += f", net {usd(r['net'])} -> " + ("BLOCKED: " + "; ".join(r["problems"]) if r["problems"] else money_action_status(db, r["money_action"]))
    print(line)
print("   Coastal HVAC renews insurance to 2028-03-15 and resubmits:")
db.execute("UPDATE lab_subcontracts SET insurance_expires='2028-03-15' WHERE id=?", (sid["Coastal HVAC"],))
r = request_draw(db, sid["Coastal HVAC"], 0.5, "2027-04-05", lien_waiver="conditional")
print(f"   Coastal HVAC        to 50%: net {usd(r['net'])} -> {money_action_status(db, r['money_action'])}")
for a in db.execute("SELECT id FROM money_actions WHERE scope='subcontract'").fetchall():
    approve(db, a["id"], "pm_dana"); approve(db, a["id"], "accounting_sue")
print("   All approved by pm_dana + accounting_sue.")

print("\n4. REINSPECTION AND THE REST OF THE JOB")
inspect(db, proj, 4, "2027-04-09", "county_inspector", "pass")
print(f"   Rough-in passed Apr 9 -> {t} now '{db.execute('SELECT status FROM tickets WHERE id=?', (t,)).fetchone()[0]}'")
start_phase(db, proj, 5, "2027-04-12", "pm_dana"); inspect(db, proj, 5, "2027-04-20", "county_inspector", "pass")
start_phase(db, proj, 6, "2027-04-22", "pm_dana")
print("   Retainage before final:", blocked(release_retainage, db, proj, "2027-07-01"))
inspect(db, proj, 6, "2027-07-23", "county_inspector", "pass", notes="certificate of occupancy issued")
for name in sid:
    waiver = "unconditional" if name != "Coastal HVAC" else "conditional"
    r = request_draw(db, sid[name], 1.0, "2027-07-26", lien_waiver=waiver)
    print(f"   {name:<19} final draw: net {usd(r['net'])} ({waiver} waiver) -> {r['status']}")
print("   Retainage release:")
for sub, held, status in release_retainage(db, proj, "2027-07-27"):
    print(f"     {sub:<19} {usd(held)}: {status}")

print("\n5. CREW PAYROLL AND JOB COST (sample logs crew time on framing only)")
for ph, cents, a in payroll_run(db, proj, "2027-02-08", "2027-03-12"):
    print(f"   {ph}: {usd(cents)} -> {money_action_status(db, a)}")
print(f"   {'phase':<38}{'crew labor':>12}{'budget':>12}{'subs':>12}  inspection")
for r in job_cost(db, proj):
    print(f"   {r['phase']:<38}{usd(r['labor']):>12}{usd(r['labor_budget']):>12}{usd(r['subs']):>12}  {r['inspection']}: {r['status']}"
          + ("   OVER BUDGET" if r["over"] else ""))
print("\n   Check-in:", check_in(db, f"lab:{proj}", "owner_alex", True, False,
                               note="House done, CO issued; framing labor over budget, HVAC final waiver outstanding"))

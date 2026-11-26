"""Builds one sample restaurant for the Human UI from the same sample data the demos use.

Everything here is sample data: people, prices, covers and sales. The day is Friday 2026-11-20.
The prep clock starts at 4 PM; the UI can move it forward to watch the fryer screen change.
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hiwr"))

from hiwr_contacts import connect, add_plan_owner, add_contact, certify, request_money_action  # noqa: E402
from hiwr_access import grant_task  # noqa: E402
from hiwr_roster import add_member  # noqa: E402
from hiwr_staff import hr_request, decide_hr  # noqa: E402
from hiwr_prep import make_batches  # noqa: E402
from hiwr_stew import sell, receive_short  # noqa: E402
from hiwr_processes import open_ticket, ticket_priority  # noqa: E402

TODAY = "2026-11-20"
OWNER = "owner_alex"

# The sample roster, added through hiwr_roster like anyone else. Level is set by the account holder only.
MEMBERS = [
    {"id": "owner_alex", "name": "Alex", "title": "Owner", "level": 4, "language": "en"},
    {"id": "chef_marco", "name": "Marco", "title": "Chef", "level": 2, "language": "en"},
    {"id": "manager_jo", "name": "Jo", "title": "General manager", "level": 3, "language": "en"},
    {"id": "accounting_sue", "name": "Sue", "title": "Bookkeeper", "level": 3, "language": "en"},
    {"id": "fry_rosa", "name": "Rosa", "title": "Cook, fryer", "level": 0, "language": "es"},
    {"id": "stocker_tim", "name": "Tim", "title": "Stocker", "level": 0, "language": "en"},
]

# Sales by hour for the dishes that use fried chips (same shape as prep_demo.py)
CHIP_SALES = {"Chips and salsa": {11: 4, 12: 8, 13: 6, 14: 3, 15: 2, 16: 3, 17: 8, 18: 14, 19: 16, 20: 12, 21: 7, 22: 3},
              "Nachos": {11: 1, 12: 3, 13: 2, 14: 1, 15: 1, 16: 1, 17: 3, 18: 6, 19: 7, 20: 5, 21: 3, 22: 1}}
# Main dishes by hour (sample), so revenue and forecast have something real to add up
MAIN_SALES = {"Fish tacos": {11: 3, 12: 6, 13: 5, 14: 2, 15: 1, 16: 2, 17: 6, 18: 10, 19: 12, 20: 9, 21: 5, 22: 2},
              "House burger": {11: 4, 12: 8, 13: 6, 14: 2, 15: 2, 16: 2, 17: 7, 18: 12, 19: 13, 20: 10, 21: 6, 22: 2},
              "Chicken salad": {11: 3, 12: 6, 13: 4, 14: 1, 15: 1, 16: 1, 17: 3, 18: 5, 19: 6, 20: 4, 21: 2, 22: 1}}
ALL_SALES = {**CHIP_SALES, **MAIN_SALES}
SIDE_PRICES = {"Chips and salsa": 600, "Nachos": 1200}     # cents, sample; mains are priced in recipes
RESERVATIONS, USUAL_RESERVATIONS = 150, 120
BUSY_BOOST = 1.45            # tonight runs busier than forecast after 6 PM (a big party)


def add_today_sales(db, upto_hour):
    """POS sales for today up to (not including) upto_hour. Safe to call again with a later hour."""
    for dish, hours in ALL_SALES.items():
        for h, q in hours.items():
            if h < upto_hour and not db.execute("SELECT 1 FROM dish_sales WHERE day=? AND hour=? AND dish=?",
                                                (TODAY, h, dish)).fetchone():
                db.execute("INSERT INTO dish_sales VALUES (?,?,?,?)", (TODAY, h, dish, round(q * (BUSY_BOOST if h >= 18 else 1.0))))


def build_blank(path=":memory:"):
    """Your own account, no test data. Kept in a file, so the team you add is still there after a restart.
    A new file starts with just the account holder: adding the team is the first step."""
    db = connect(path, check_same_thread=False)
    if not db.execute("SELECT 1 FROM plan_owners LIMIT 1").fetchone():
        add_plan_owner(db, OWNER, "setup")
        add_member(db, "Alex", "Owner", OWNER, member_id=OWNER, reason="account created")
        db.commit()
    return db


# Prior vendor invoices for the demo scene (sample). Paid amounts against what actually arrived are what "settle"
# the real unit cost the algorithms use; anything short or disputed stays anticipated until two people approve.
PRIOR_INVOICES = [
    {"number": "INV-8790", "vendor": "Sysco", "material": "white fish", "billed_qty": 160, "received_qty": 160,
     "billed_cents": 9600, "paid_cents": 9600, "status": "paid", "note": "Arrived complete; fish went up to 60c an oz"},
    {"number": "INV-8801", "vendor": "Sysco", "material": "white fish", "billed_qty": 160, "received_qty": 150,
     "billed_cents": 9600, "paid_cents": 9000, "status": "paid_short", "note": "10 oz short; paid only for what arrived"},
    {"number": "INV-8812", "vendor": "Sysco", "material": "white fish", "billed_qty": 160, "received_qty": 60,
     "billed_cents": 8800, "paid_cents": 0, "status": "disputed", "note": "100 oz short, perishable: ticket SHP-0001, credit asked"},
    {"number": "INV-8805", "vendor": "Sysco", "material": "beef patty", "billed_qty": 120, "received_qty": 120,
     "billed_cents": 16800, "paid_cents": 16800, "status": "paid", "note": "Price went up from $1.35 to $1.40 a patty"},
    {"number": "INV-8809", "vendor": "Sysco", "material": "corn tortilla", "billed_qty": 1500, "received_qty": 1500,
     "billed_cents": 18000, "paid_cents": 18000, "status": "paid", "note": "Arrived complete, paid in full"},
]

# ---------- Trial demos, one per business type, offered before a plan is bought ----------
# "stew" = restaurant, "stock" = stock house (warehouse / retail shelves), "lab" = long jobs (a house build).
DEMOS = {
    "stew": {"name": "Stew demo", "about": "Restaurant: staff, fryer station, recipes, approvals"},
    "stock": {"name": "Stock house demo", "about": "Warehouse and shelves: pull list, dates, stock moves"},
    "lab": {"name": "Lab demo", "about": "Long jobs: a house build with phases, inspections, subs"},
}
# Sample UPC/EAN numbers for the stock items (made up, valid check digits), so Scan can find them in the demo.
SAMPLE_UPCS = {"CHIP-01": "012345678905", "SALS-02": "036000291452", "BREW-04": "042100005264",
               "YOGT-03": "070847811169", "SOUP-06": "051000012616", "RICE-08": "074312000149"}
STOCK_MEMBERS = {"owner_alex", "manager_jo", "accounting_sue", "stocker_tim"}
LAB_MEMBERS = [
    {"id": "owner_alex", "name": "Alex", "title": "Owner", "level": 4, "language": "en"},
    {"id": "pm_dana", "name": "Dana", "title": "Project manager", "level": 3, "language": "en"},
    {"id": "accounting_sue", "name": "Sue", "title": "Bookkeeper", "level": 3, "language": "en"},
    {"id": "carp_luis", "name": "Luis", "title": "Lead carpenter", "level": 1, "language": "es"},
]


def build_demo(kind):
    """One business type's trial demo, in memory. Changes reset when you switch away."""
    if kind == "lab":
        return build_lab()
    if kind not in ("stew", "stock"):
        raise ValueError(f"No demo called {kind}")
    return build(parts={"restaurant"} if kind == "stew" else {"stock"})


def build_lab():
    """Lab trial: the Henderson house build (from lab_demo.py), stopped mid-job so there is work to do:
    framing over budget, rough-in failed with a ticket open, sub draws waiting for double approval."""
    from hiwr_access import set_level
    from hiwr_lab import start_phase, inspect, log_time, request_draw
    db = connect(check_same_thread=False)
    add_plan_owner(db, OWNER, "setup")
    for m in LAB_MEMBERS:
        add_member(db, m["name"], m["title"], OWNER, m["language"], 0 if m["level"] >= 4 else m["level"], member_id=m["id"],
                   reason="sample roster")
    for m in ("pm_dana", "accounting_sue"):
        set_level(db, m, 3, OWNER)
        for scope in ("subcontract", "payroll"):
            certify(db, m, scope, OWNER)
        grant_task(db, m, "subcontract_approval", OWNER, TODAY)
        grant_task(db, m, "payroll_approval", OWNER, TODAY)
    client = add_contact(db, "The Hendersons (sample)", OWNER)["id"]
    proj = db.execute("INSERT INTO lab_projects(name, contact_id, contract_cents, starts_on, target_end, permit_number) "
                      "VALUES ('Henderson house, 3 bed', ?, 38500000, '2027-01-04', '2027-07-30', 'BP-27-0142')", (client,)).lastrowid
    for i, (n, lb, insp) in enumerate([("Site prep and footings", 1800000, "footing inspection"),
                                       ("Foundation", 1500000, "foundation inspection"),
                                       ("Framing", 2100000, "framing inspection"),
                                       ("Rough-in: plumbing, electrical, HVAC", 600000, "rough-in inspection"),
                                       ("Insulation", 300000, "insulation inspection"),
                                       ("Drywall and finishes", 3500000, "final inspection")], 1):
        db.execute("INSERT INTO lab_phases(project_id, seq, name, labor_budget_cents, inspection) VALUES (?,?,?,?,?)",
                   (proj, i, n, lb, insp))
    from hiwr_lab import phase
    subs = {}
    for name, scope, amt, ins in [("Cape Fear Plumbing", "rough and finish plumbing", 1850000, "2027-12-31"),
                                  ("Port City Electric", "rough and finish electrical", 2200000, "2027-10-31"),
                                  ("Coastal HVAC", "ductwork and equipment", 1600000, "2028-03-15")]:
        subs[name] = db.execute("INSERT INTO lab_subcontracts(project_id, phase_id, sub, scope, amount_cents, license_no, "
                                "insurance_expires) VALUES (?,?,?,?,?,?,?)",
                                (proj, phase(db, proj, 4)["id"], name, scope, amt, "NC-LIC", ins)).lastrowid
    start_phase(db, proj, 1, "2027-01-04", "pm_dana"); inspect(db, proj, 1, "2027-01-15", "county_inspector", "pass")
    start_phase(db, proj, 2, "2027-01-18", "pm_dana"); inspect(db, proj, 2, "2027-02-05", "county_inspector", "pass")
    start_phase(db, proj, 3, "2027-02-08", "pm_dana")
    d = date(2027, 2, 8)
    while d < date(2027, 3, 13):
        if d.weekday() < 5:
            for m, rate in [("carp_luis", 3400), ("carp_1", 2600), ("carp_2", 2600), ("carp_3", 2400)]:
                log_time(db, proj, 3, m, d.isoformat(), 9 if d >= date(2027, 3, 1) else 8, rate)
        d += timedelta(days=1)
    inspect(db, proj, 3, "2027-03-12", "county_inspector", "pass")
    start_phase(db, proj, 4, "2027-03-15", "pm_dana")
    inspect(db, proj, 4, "2027-04-02", "county_inspector", "fail",
            notes="missing nail plates at 6 studs; box fill over limit in kitchen", responsible_sub="Port City Electric")
    request_draw(db, subs["Cape Fear Plumbing"], 0.5, "2027-04-03", lien_waiver="conditional")
    request_draw(db, subs["Coastal HVAC"], 0.5, "2027-04-03", lien_waiver="conditional")
    from hiwr_contacts import assign
    assign(db, OWNER, "carp_luis", "task", "Add nail plates at the 6 flagged studs before reinspection", due_on=TODAY)
    db.commit()
    return db


# Who cooks what, and how it goes (sample): minutes from fired to ready, and how often a plate is remade.
COOK_PROFILES = {
    "chef_marco": {"Fish tacos": (9, 0.02), "House burger": (11, 0.03), "Chicken salad": (7, 0.00)},
    "cook_luis": {"House burger": (8, 0.02), "Fish tacos": (12, 0.08), "Nachos": (6, 0.03)},
    "fry_rosa": {"Nachos": (5, 0.01), "Chips and salsa": (3, 0.00), "Fish tacos": (10, 0.04)},
    "cook_dan": {"House burger": (10, 0.10), "Chicken salad": (6, 0.02)},
    "cook_ana": {"Chicken salad": (6, 0.01), "Fish tacos": (9, 0.03), "Nachos": (7, 0.06)},
}


def add_order_history(db, days=14, per_day=24):
    """Past orders with who cooked each line (order_stage_events), so Mesh has history to read."""
    rng = random.Random(11)
    wid = db.execute("SELECT id FROM workflows WHERE name='restaurant_live'").fetchone()["id"]
    pairs = [(who, item, mins, remake) for who, items in COOK_PROFILES.items() for item, (mins, remake) in items.items()]
    for back in range(1, days + 1):
        day = (date.fromisoformat(TODAY) - timedelta(days=back)).isoformat()
        for n in range(per_day):
            who, item, mins, remake = rng.choice(pairs)
            h, m = rng.choice([12, 13, 18, 19, 20]), rng.randrange(0, 50)
            t0 = f"{day}T{h:02d}:{m:02d}"
            oid = db.execute("INSERT INTO orders(workflow_id, placed_at, fulfillment, source_ref) VALUES (?,?,?,?)",
                             (wid, t0, "dine_in", f"POS-{back:02d}{n:02d}")).lastrowid
            iid = db.execute("INSERT INTO order_items(order_id, item, qty) VALUES (?,?,1)", (oid, item)).lastrowid
            took = max(2, round(rng.gauss(mins, mins * 0.15)))
            ready_m = m + 1 + took
            redo = rng.random() < remake
            if redo:
                ready_m += mins
            events = [("received", m, "server_kim"), ("fired", m + 1, who), ("cooking", m + 1, who)]
            if redo:
                events.append(("remade", m + 1 + took, who))
            events += [("ready", ready_m, who), ("served_or_picked_up", ready_m + 2, "server_kim")]
            for stage, mm, actor in events:
                at = f"{day}T{h + mm // 60:02d}:{mm % 60:02d}"
                db.execute("INSERT INTO order_stage_events(order_id, item_id, stage, at, actor) VALUES (?,?,?,?,?)",
                           (oid, iid, stage, at, actor))


def build(parts=("restaurant", "stock")):
    random.seed(7)
    parts = set(parts)
    R, S = "restaurant" in parts, "stock" in parts
    # FastAPI runs endpoints in a thread pool; one lock in app.py keeps access to this connection serial
    db = connect(check_same_thread=False)
    add_plan_owner(db, OWNER, "setup")
    for m in MEMBERS:     # the roster comes first: everything else hangs off the people who work here
        if "restaurant" not in parts and m["id"] not in STOCK_MEMBERS:
            continue
        add_member(db, m["name"], m["title"], OWNER, m["language"], 0 if m["level"] >= 4 else m["level"], member_id=m["id"],
                   reason="sample roster")
    for m in ("manager_jo", "accounting_sue"):
        for scope in ("payroll", "refund"):
            certify(db, m, scope, OWNER)
    if R:
        grant_task(db, "fry_rosa", "receiving_check", OWNER, TODAY)

        # ---------- Staff (from staff_demo.py, moved to today) ----------
        db.executemany("INSERT INTO staffing_rules VALUES (?,?,?,?,?,?)", [
            ("cook", "covers", 40, 2, 16, 22), ("dishwasher", "covers", 75, 1, 17, 23)])
        db.executemany("INSERT INTO staff VALUES (?,?,?,?)", [
            ("cook_luis", "cook", 1800, 3), ("fry_rosa", "cook", 2000, 3), ("cook_dan", "cook", 1700, 3), ("cook_ana", "cook", 1800, 3),
            ("dish_ray", "dishwasher", 1500, 3), ("dish_eve", "dishwasher", 1500, 3)])
        for m in ("cook_luis", "fry_rosa", "cook_dan", "cook_ana", "dish_ray", "dish_eve"):
            db.execute("INSERT INTO staff_availability VALUES (?,?,15,23)", (m, TODAY))
        guests = add_contact(db, "Reservations (sample)", "host_ana")["id"]
        for _ in range(25):
            db.execute("INSERT INTO appointments(contact_id, starts_at, ends_at, party_size, kind) VALUES (?,?,?,6,'reservation')",
                       (guests, TODAY + "T19:00", TODAY + "T21:00"))
        hr_request(db, "cook_dan", "time_off", "2026-11-17T09:00", "2026-12-24", "2026-12-26", "family travel")
        hr_request(db, "cook_ana", "pay_question", "2026-11-19T16:00", detail="overtime on last check")
        hr_request(db, "dish_eve", "swap", "2026-11-18T11:00", "2026-11-28", "2026-11-28", "swap Sat with Ray")
        rid = hr_request(db, "dish_ray", "time_off", TODAY + "T14:00", TODAY, TODAY, "sick")
        decide_hr(db, rid, "approved", "chef_marco", TODAY + "T14:02", "feel better")

        pos = db.execute("INSERT INTO positions(role, title, hourly_cents, opened_by) VALUES "
                         "('dishwasher', 'Dishwasher, prime time', 1600, 'owner_alex')").lastrowid
        for name, months, days, start, end, phone, lang in [
                ("Marcus Bell", 18, [4, 5], 16, 24, "910-555-0161", "en"), ("Tia Owens", 30, [4, 5, 6], 17, 23, "910-555-0162", "es"),
                ("Gus Hall", 6, [0, 1, 2], 16, 23, "910-555-0163", "en"), ("Nina Park", 4, [4], 17, 23, "910-555-0164", "en")]:
            cid = db.execute("INSERT INTO candidates(name, phone, language) VALUES (?,?,?)", (name, phone, lang)).lastrowid
            db.execute("INSERT INTO candidate_experience VALUES (?, 'dishwasher', ?, 'sample')", (cid, months))
            for wd in days:
                db.execute("INSERT INTO candidate_availability VALUES (?,?,?,?)", (cid, wd, start, end))

        # ---------- Prep: fryer (from prep_demo.py) ----------
        db.execute("INSERT INTO prep_items VALUES ('fried tortilla chips', 'fryer', 'oz', 48, 8, 180, 0.10, 'corn tortilla', 60)")
        db.executemany("INSERT INTO prep_usage VALUES (?,?,?)", [("Chips and salsa", "fried tortilla chips", 4), ("Nachos", "fried tortilla chips", 6)])
        for w in range(1, 5):
            d = (date.fromisoformat(TODAY) - timedelta(weeks=w)).isoformat()
            for dish, hours in ALL_SALES.items():
                for h, q in hours.items():
                    db.execute("INSERT INTO dish_sales VALUES (?,?,?,?)", (d, h, dish, max(0, round(q * random.uniform(0.85, 1.15)))))

        # ---------- Stew + Warehouse (from inventory_demo.py) ----------
        mats = [("corn tortilla", "each", 1500, 12), ("white fish", "oz", 400, 55), ("roma tomato", "oz", 320, 9), ("white onion", "oz", 200, 4),
                ("cilantro", "oz", 40, 25), ("lime", "each", 60, 20), ("crema", "oz", 120, 15), ("brioche bun", "each", 120, 45),
                ("beef patty", "each", 110, 135), ("lettuce", "oz", 200, 8), ("mayo", "oz", 160, 6), ("ketchup", "oz", 160, 5),
                ("pickle", "oz", 80, 10), ("chicken breast", "oz", 300, 30), ("mixed greens", "oz", 240, 14), ("vinaigrette", "oz", 100, 12)]
        for m, unit, on, cents in mats:
            db.execute("INSERT INTO materials VALUES (?,?,?,0)", (m, unit, on))
            db.execute("INSERT INTO material_sources VALUES (?, 'Sysco', 2, ?, 1, 1)", (m, cents))
        db.executemany("INSERT INTO material_allergens VALUES (?,?)", [
            ("roma tomato", "tomato"), ("ketchup", "tomato"), ("roma tomato", "nightshade"), ("ketchup", "nightshade"),
            ("crema", "dairy"), ("mayo", "egg"), ("brioche bun", "gluten")])
        db.executemany("INSERT INTO recipes VALUES (?,?,?,?,?)", [
            ("Pico de gallo", "sub", 32, "oz", None), ("House sauce", "sub", 20, "oz", None),
            ("Fish tacos", "dish", 1, "plate", 1500), ("House burger", "dish", 1, "plate", 1400), ("Chicken salad", "dish", 1, "plate", 1300)])
        db.executemany("INSERT INTO recipe_lines VALUES (?,?,?,?)", [
            ("Pico de gallo", "roma tomato", 20, "oz"), ("Pico de gallo", "white onion", 8, "oz"), ("Pico de gallo", "cilantro", 2, "oz"),
            ("Pico de gallo", "lime", 2, "each"), ("House sauce", "mayo", 12, "oz"), ("House sauce", "ketchup", 6, "oz"),
            ("House sauce", "pickle", 2, "oz"), ("Fish tacos", "corn tortilla", 3, "each"), ("Fish tacos", "white fish", 5, "oz"),
            ("Fish tacos", "Pico de gallo", 2, "oz"), ("Fish tacos", "crema", 1, "oz"), ("House burger", "brioche bun", 1, "each"),
            ("House burger", "beef patty", 1, "each"), ("House burger", "lettuce", 1, "oz"), ("House burger", "House sauce", 1.5, "oz"),
            ("Chicken salad", "chicken breast", 5, "oz"), ("Chicken salad", "mixed greens", 4, "oz"), ("Chicken salad", "vinaigrette", 1.5, "oz")])
        sell(db, "Fish tacos", 12, TODAY + "T13:00", "POS lunch")
        sell(db, "House burger", 9, TODAY + "T13:00", "POS lunch")
        receive_short(db, "white fish", 160, 60, TODAY + "T08:00", "INV-8812")
        open_ticket(db, "SHIPMENT", "Sysco INV-8812 short 100 oz white fish (perishable)",
                    ticket_priority(db, "SHIPMENT", perishable=True, dollars=55), TODAY + "T08:00", "receiver_jo")

    if S:
        for sku, name, cat, shelf, back, exp, aisle, cap, par in [
                ("CHIP-01", "Tortilla chips 13oz", "snacks", 0, 20, None, "A3", 36, 12),
                ("SALS-02", "Fresh salsa 16oz", "deli", 5, 96, "2026-12-08", "D1", 24, 10),
                ("BREW-04", "Cold brew 6-pack", "drinks", 8, 12, "2027-05-01", "B2", 18, 9),
                ("YOGT-03", "Greek yogurt 32oz", "dairy", 0, 0, "2026-12-01", "D4", 20, 8),
                ("SOUP-06", "Canned soup 15oz", "pantry", 40, 200, "2028-06-01", "C5", 48, 16),
                ("RICE-08", "Jasmine rice 5lb", "pantry", 3, 0, None, "C2", 15, 6)]:
            db.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?,?)", (sku, name, cat, 449, 210, shelf, back, exp))
            db.execute("INSERT INTO shelf_plan VALUES (?,?,?,?)", (sku, aisle, cap, par))
        if not R:
            # stock house only: a customer return waiting for double approval
            request_money_action(db, "refund", 1347, "Refund 3 Greek yogurt 32oz, returned past date at register 2", "manager_jo")

    if R:
        # ---------- Money actions waiting for double approval ----------
        request_money_action(db, "refund", 3000, "Refund 2 plates fish tacos, table 12 (served cold), DSP pending", "chef_marco")
        request_money_action(db, "payroll", 8000, "Call-in pay 5h x $16.00 for dish temp, shift cancelled with 3h notice", "chef_marco")

    # ---------- Daily tasks the owner assigned (employees see these in the top bar) ----------
    from hiwr_contacts import assign
    for who, kind, title in [
            ("fry_rosa", "task", "Change fryer oil before 5 PM"), ("fry_rosa", "task", "Log fryer oil temp at open and 7 PM"),
            ("fry_rosa", "task", "Receiving check: Sysco produce, temps and dates"),
            ("stocker_tim", "task", "Pull list for aisle A and D before 7 AM"), ("stocker_tim", "task", "Date-check dairy case"),
            ("chef_marco", "analysis", "Why fish tacos waste went up this week"), ("chef_marco", "call", "Call Sysco rep about INV-8812 short fish")]:
        if (R or who in STOCK_MEMBERS) and (S or who != "stocker_tim"):
            assign(db, OWNER, who, kind, title, due_on=TODAY)

    if R:
        add_order_history(db)
        # ---------- Prep clock: 4 PM, 3 batches fried at 10:30 ----------
        add_today_sales(db, 16)
        make_batches(db, "fried tortilla chips", 3, TODAY + "T10:30", "fry_rosa")
    db.commit()
    return db

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


def build():
    random.seed(7)
    # FastAPI runs endpoints in a thread pool; one lock in app.py keeps access to this connection serial
    db = connect(check_same_thread=False)
    add_plan_owner(db, OWNER, "setup")
    for m in MEMBERS:     # the roster comes first: everything else hangs off the people who work here
        add_member(db, m["name"], m["title"], OWNER, m["language"], 0 if m["level"] >= 4 else m["level"], member_id=m["id"],
                   reason="sample roster")
    for m in ("manager_jo", "accounting_sue"):
        for scope in ("payroll", "refund"):
            certify(db, m, scope, OWNER)
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

    for sku, name, cat, shelf, back, exp, aisle, cap, par in [
            ("CHIP-01", "Tortilla chips 13oz", "snacks", 0, 20, None, "A3", 36, 12),
            ("SALS-02", "Fresh salsa 16oz", "deli", 5, 96, "2026-12-08", "D1", 24, 10),
            ("BREW-04", "Cold brew 6-pack", "drinks", 8, 12, "2027-05-01", "B2", 18, 9),
            ("YOGT-03", "Greek yogurt 32oz", "dairy", 0, 0, "2026-12-01", "D4", 20, 8),
            ("SOUP-06", "Canned soup 15oz", "pantry", 40, 200, "2028-06-01", "C5", 48, 16),
            ("RICE-08", "Jasmine rice 5lb", "pantry", 3, 0, None, "C2", 15, 6)]:
        db.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?,?)", (sku, name, cat, 449, 210, shelf, back, exp))
        db.execute("INSERT INTO shelf_plan VALUES (?,?,?,?)", (sku, aisle, cap, par))

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
        assign(db, OWNER, who, kind, title, due_on=TODAY)

    # ---------- Prep clock: 4 PM, 3 batches fried at 10:30 ----------
    add_today_sales(db, 16)
    make_batches(db, "fried tortilla chips", 3, TODAY + "T10:30", "fry_rosa")
    db.commit()
    return db

"""Run: python prep_demo.py
The fryer's prep screen (tortilla chips) and a welder's station screen. Sample data."""
import random
from datetime import date, timedelta
from hiwr_contacts import *
from hiwr_prep import *

random.seed(7)
db = connect()
db.execute("INSERT INTO materials VALUES ('corn tortilla', 'each', 1200, 0)")
db.execute("INSERT INTO prep_items VALUES ('fried tortilla chips', 'fryer', 'oz', 48, 8, 180, 0.10, 'corn tortilla', 60)")
db.executemany("INSERT INTO prep_usage VALUES (?,?,?)", [("Chips and salsa", "fried tortilla chips", 4), ("Nachos", "fried tortilla chips", 6)])
TODAY = "2026-11-20"   # Friday
base = {"Chips and salsa": {11: 4, 12: 8, 13: 6, 14: 3, 15: 2, 16: 3, 17: 8, 18: 14, 19: 16, 20: 12, 21: 7, 22: 3},
        "Nachos": {11: 1, 12: 3, 13: 2, 14: 1, 15: 1, 16: 1, 17: 3, 18: 6, 19: 7, 20: 5, 21: 3, 22: 1}}
for w in range(1, 5):
    d = (date.fromisoformat(TODAY) - timedelta(weeks=w)).isoformat()
    for dish, hours in base.items():
        for h, q in hours.items():
            db.execute("INSERT INTO dish_sales VALUES (?,?,?,?)", (d, h, dish, max(0, round(q * random.uniform(0.85, 1.15)))))

def today_sales(upto, boost):
    for dish, hours in base.items():
        for h, q in hours.items():
            if h < upto and not db.execute("SELECT 1 FROM dish_sales WHERE day=? AND hour=? AND dish=?", (TODAY, h, dish)).fetchone():
                db.execute("INSERT INTO dish_sales VALUES (?,?,?,?)", (TODAY, h, dish, round(q * (boost if h >= 17 else 1.0))))

def show(p, at):
    print(f"   [{at}] chips sold today {p['sold']} {p['unit']} ({p['running']}), good on hand {p['good_on_hand']}, waste {p['waste']}")
    print(f"          expected next hour {p['next_hour']}, next 3h {p['next_window']}, rest of night {p['rest_of_night']}")
    print(f"          -> FRY {p['fry_batches_now']} batch(es) now ({p['batch']}), uses {p['input']}; next batch about {p['next_batch_at']}")

print("FRYER - prep screen for fried tortilla chips (Fri, 150 reservations vs usual 120)")
today_sales(16, 1.0)
make_batches(db, "fried tortilla chips", 3, TODAY + "T10:30", "fry_rosa")
for hh, label, boost in [(16, "4:00 PM", 1.0), (17, "5:00 PM", 1.0), (18, "6:00 PM", 1.45), (19, "7:00 PM", 1.45)]:
    today_sales(hh, boost)
    if hh == 18:
        print("   ... 5-6 PM ran 45% busier than forecast (a big party ordered nachos for the table)")
    p = prep_plan(db, "fried tortilla chips", f"{TODAY}T{hh}:00", 150, 120); show(p, label)
    make_batches(db, "fried tortilla chips", p["fry_batches_now"], f"{TODAY}T{hh}:02", "fry_rosa")
left = db.execute('SELECT on_hand FROM materials').fetchone()[0]
print(f"   Corn tortillas left in Stew inventory: {left:g} (enough for {int(left // 60)} more batches)")

print("\nWELDER - station screen for cruz (quota 20 pipes this shift, 7 AM - 3 PM)")
for at, done in [("2026-11-20T11:00", 7), ("2026-11-20T13:00", 13)]:
    s = station_pace(20, done, "2026-11-20T07:00", 8, at)
    print(f"   [{at[11:]}] done {s['done']}/{s['quota']}, pace {s['pace_per_hour']}/h, need {s['needed_per_hour']}/h -> {s['status']}")
print("   Behind at 11 AM shows on the foreman's board too; cruz sees only his own numbers.")

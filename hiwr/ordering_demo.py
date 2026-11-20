"""Run: python ordering_demo.py
Manager ordering from predicted use, with UPCs per vendor, and suggestions for using back stock. Sample data."""
import random
from datetime import date, timedelta
from hiwr_contacts import *
from hiwr_access import *
from hiwr_processes import open_ticket
from hiwr_ordering import *

random.seed(3)
TODAY = "2026-11-20"
db = connect()
add_plan_owner(db, "owner_alex", "setup")
set_level(db, "chef_marco", 2, "owner_alex"); set_level(db, "cook_luis", 0, "owner_alex")

mats = [  # material, unit, on hand, cost/unit cents, vendor, lead, upc, pack, label, expires
    ("corn tortilla", "each", 900, 12, "Sysco", 2, "0074865200118", 360, "360 ct case", "2026-12-04"),
    ("white fish", "oz", 300, 55, "Harbor Seafood", 1, "0085012300045", 160, "10 lb case", "2026-11-24"),
    ("roma tomato", "oz", 1200, 9, "Sysco", 2, "0004068200071", 400, "25 lb case", "2026-11-26"),
    ("white onion", "oz", 300, 4, "Sysco", 2, "0004066300012", 800, "50 lb sack", "2026-12-20"),
    ("cilantro", "oz", 30, 25, "Sysco", 2, "0004088900030", 32, "2 lb case", "2026-11-25"),
    ("lime", "each", 80, 20, "Sysco", 2, "0004004800048", 150, "150 ct case", "2026-12-05"),
    ("crema", "oz", 100, 15, "Sysco", 2, "0007072300016", 128, "8 x 16 oz", "2026-12-02"),
    ("brioche bun", "each", 60, 45, "Pine St Bakery", 1, "0061032700005", 48, "4 dz", "2026-11-23"),
    ("beef patty", "each", 400, 135, "Sysco", 2, "0007470300201", 80, "80 ct case", "2027-02-01"),
    ("lettuce", "oz", 200, 8, "Sysco", 2, "0004064000024", 384, "24 ct case", "2026-11-27"),
    ("mayo", "oz", 300, 6, "Sysco", 2, "0004800000128", 128, "1 gal", "2027-03-01"),
    ("ketchup", "oz", 250, 5, "Sysco", 2, "0001300000646", 114, "#10 can x 1", "2027-04-01"),
    ("pickle", "oz", 120, 10, "Sysco", 2, "0004670000025", 128, "1 gal", "2027-01-15"),
]
for m, unit, on, cents, v, lead, upc, pack, label, exp in mats:
    db.execute("INSERT INTO materials VALUES (?,?,?,0)", (m, unit, on))
    db.execute("INSERT INTO material_sources VALUES (?,?,?,?,1,1)", (m, v, lead, cents))
    db.execute("INSERT INTO vendor_items VALUES (?,?,?,?,?)", (m, v, upc, pack, label))
    db.execute("INSERT INTO material_par VALUES (?, 1, 7, ?)", (m, exp))
db.executemany("INSERT INTO recipes VALUES (?,?,?,?,?)", [("Pico de gallo", "sub", 32, "oz", None), ("House sauce", "sub", 20, "oz", None),
    ("Fish tacos", "dish", 1, "plate", 1500), ("House burger", "dish", 1, "plate", 1400), ("Tomato salad", "dish", 1, "plate", 900)])
db.executemany("INSERT INTO recipe_lines VALUES (?,?,?,?)", [
    ("Pico de gallo", "roma tomato", 20, "oz"), ("Pico de gallo", "white onion", 8, "oz"), ("Pico de gallo", "cilantro", 2, "oz"), ("Pico de gallo", "lime", 2, "each"),
    ("House sauce", "mayo", 12, "oz"), ("House sauce", "ketchup", 6, "oz"), ("House sauce", "pickle", 2, "oz"),
    ("Fish tacos", "corn tortilla", 3, "each"), ("Fish tacos", "white fish", 5, "oz"), ("Fish tacos", "Pico de gallo", 2, "oz"), ("Fish tacos", "crema", 1, "oz"),
    ("House burger", "brioche bun", 1, "each"), ("House burger", "beef patty", 1, "each"), ("House burger", "lettuce", 1, "oz"), ("House burger", "House sauce", 1.5, "oz"),
    ("Tomato salad", "roma tomato", 6, "oz"), ("Tomato salad", "white onion", 1, "oz"), ("Tomato salad", "cilantro", 0.25, "oz")])
for w in range(1, 5):
    for i in range(7):
        d = (date.fromisoformat(TODAY) - timedelta(weeks=w) + timedelta(days=i)).isoformat()
        busy = 1.6 if (date.fromisoformat(d).weekday() >= 4) else 1.0
        for dish, n in [("Fish tacos", 40), ("House burger", 55), ("Tomato salad", 4)]:
            db.execute("INSERT INTO dish_sales VALUES (?, 19, ?, ?)", (d, dish, round(n * busy * random.uniform(0.9, 1.1))))
open_ticket(db, "SHIPMENT", "Harbor Seafood INV-8812 short 100 oz white fish", 20, "2026-11-18T08:00", "receiver_jo")

pos, back = plan_orders(db, TODAY)
print("1. REORDER LIST built ahead from predicted use (next 21 days) - drafts for chef_marco")
for p in sorted(pos, key=lambda p: p["order_by"]):
    print(f"   PO #{p['po']} {p['vendor']}: order by {p['order_by']}, total {usd(p['total'])}"
          + (f"   NOTE: open tickets {', '.join(p['open_tickets'])}" if p["open_tickets"] else ""))
    for l in p["lines"]:
        print(f"      UPC {l['upc']}  {l['material']:<13} {l['packs']} x {l['pack']:<12} {usd(l['cost']):>9}  order by {l['order_by']}  ({l['why']})")
try:
    send_po(db, pos[0]["po"], "cook_luis")
except PermissionError as e:
    print("   cook_luis sends a PO: BLOCKED:", e)
send_po(db, pos[0]["po"], "chef_marco")
print(f"   chef_marco reviewed and sent PO #{pos[0]['po']}")

print("\n2. BACK STOCK - more than will be used in time")
for b in back:
    print(f"   {b['material']}: {b['on_hand']:g} {b['unit']} on hand, uses {b['avg_use']}/day, "
          + (f"expires in {b['expires_in']} days, {b['excess']:g} {b['unit']} will be left" if b["excess"] else f"{b['days_of_supply']} days of supply"))
    for s in backstock_suggestions(db, b, TODAY, other_locations={"Downtown location": {"roma tomato": 300}}):
        print(f"      - {s}")
tom = next(b for b in back if b["material"] == "roma tomato")
choose(db, "roma tomato", "special + transfer", "Tomato salad special Fri-Sun; send 300 oz downtown; donate what's left Wed", "chef_marco", TODAY + "T15:00")
print("   chef_marco picks: Tomato salad special this weekend, transfer 300 oz downtown, donate the rest Wed. Logged.")

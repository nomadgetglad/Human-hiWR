"""Run: python inventory_demo.py
Inventory Management on the Human menu: Warehouse (stocker's pull list) and Stew (recipe costing).
Lab (long jobs) is in lab_demo.py. Sample data."""
from hiwr_contacts import *
from hiwr_access import *
from hiwr_warehouse import *
from hiwr_stew import *
from hiwr_processes import open_ticket, ticket_priority

def usd(c): return f"${c / 100:,.2f}"
D = "2026-11-20"
db = connect()
add_plan_owner(db, "owner_alex", "setup")
set_level(db, "stocker_tim", 0, "owner_alex"); set_level(db, "cook_luis", 0, "owner_alex"); set_level(db, "chef_marco", 2, "owner_alex")

# ------------------------------------------------------------ Warehouse
print("WAREHOUSE - stocker_tim's pull list, 6 AM")
items = [("CHIP-01", "Tortilla chips 13oz", "snacks", 0, 20, None, "A3", 36, 12),
         ("SALS-02", "Fresh salsa 16oz", "deli", 5, 96, "2026-12-08", "D1", 24, 10),
         ("BREW-04", "Cold brew 6-pack", "drinks", 8, 12, "2027-05-01", "B2", 18, 9),
         ("YOGT-03", "Greek yogurt 32oz", "dairy", 0, 0, "2026-12-01", "D4", 20, 8),
         ("SOUP-06", "Canned soup 15oz", "pantry", 40, 200, "2028-06-01", "C5", 48, 16),
         ("RICE-08", "Jasmine rice 5lb", "pantry", 3, 0, None, "C2", 15, 6)]
for sku, name, cat, shelf, back, exp, aisle, cap, par in items:
    db.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?,?)", (sku, name, cat, 449, 210, shelf, back, exp))
    db.execute("INSERT INTO shelf_plan VALUES (?,?,?,?)", (sku, aisle, cap, par))
for r in pull_list(db, D):
    print(f"   {r['aisle']:<3} {r['item']:<20} floor {r['floor']:>2}/{r['par']:<2} back {r['back']:>3}  pull {r['pull']:>2}  {r['status']}"
          + (f" ({r['rotate']})" if r["rotate"] and r["pull"] else ""))
record_pull(db, "CHIP-01", 20, "stocker_tim", D + "T06:20")
r = db.execute("SELECT shelf, back_stock FROM products WHERE sku='CHIP-01'").fetchone()
print(f"   Tim pulls 20 chips -> floor {r['shelf']}, back {r['back_stock']}. Move logged; chips now go on the order list.")
print("   Tim opens order costs:", "allowed" if can_see(db, "stocker_tim", "order_costs") else "BLOCKED (Staff level sees units and aisles only)")

# ------------------------------------------------------------ Stew
print("\nSTEW - recipe costing and live counts")
mats = [("corn tortilla", "each", 300, 12), ("white fish", "oz", 400, 55), ("roma tomato", "oz", 320, 9), ("white onion", "oz", 200, 4),
        ("cilantro", "oz", 40, 25), ("lime", "each", 60, 20), ("crema", "oz", 120, 15), ("brioche bun", "each", 120, 45),
        ("beef patty", "each", 110, 135), ("lettuce", "oz", 200, 8), ("mayo", "oz", 160, 6), ("ketchup", "oz", 160, 5),
        ("pickle", "oz", 80, 10), ("chicken breast", "oz", 300, 30), ("mixed greens", "oz", 240, 14), ("vinaigrette", "oz", 100, 12)]
for m, unit, on, cents in mats:
    db.execute("INSERT INTO materials VALUES (?,?,?,0)", (m, unit, on))
    db.execute("INSERT INTO material_sources VALUES (?, 'Sysco', 2, ?, 1, 1)", (m, cents))
db.executemany("INSERT INTO material_allergens VALUES (?,?)", [("roma tomato", "tomato"), ("ketchup", "tomato"),
               ("roma tomato", "nightshade"), ("ketchup", "nightshade"), ("crema", "dairy"), ("mayo", "egg"), ("brioche bun", "gluten")])
db.executemany("INSERT INTO recipes VALUES (?,?,?,?,?)", [
    ("Pico de gallo", "sub", 32, "oz", None), ("House sauce", "sub", 20, "oz", None),
    ("Fish tacos", "dish", 1, "plate", 1500), ("House burger", "dish", 1, "plate", 1400), ("Chicken salad", "dish", 1, "plate", 1300)])
db.executemany("INSERT INTO recipe_lines VALUES (?,?,?,?)", [
    ("Pico de gallo", "roma tomato", 20, "oz"), ("Pico de gallo", "white onion", 8, "oz"), ("Pico de gallo", "cilantro", 2, "oz"), ("Pico de gallo", "lime", 2, "each"),
    ("House sauce", "mayo", 12, "oz"), ("House sauce", "ketchup", 6, "oz"), ("House sauce", "pickle", 2, "oz"),
    ("Fish tacos", "corn tortilla", 3, "each"), ("Fish tacos", "white fish", 5, "oz"), ("Fish tacos", "Pico de gallo", 2, "oz"), ("Fish tacos", "crema", 1, "oz"),
    ("House burger", "brioche bun", 1, "each"), ("House burger", "beef patty", 1, "each"), ("House burger", "lettuce", 1, "oz"), ("House burger", "House sauce", 1.5, "oz"),
    ("Chicken salad", "chicken breast", 5, "oz"), ("Chicken salad", "mixed greens", 4, "oz"), ("Chicken salad", "vinaigrette", 1.5, "oz")])

print("   Cost cards (chef_marco, Manager level):")
for dish in ("Fish tacos", "House burger", "Chicken salad"):
    c = cost_card(db, dish)
    print(f"   {dish:<14} cost {usd(c['cost'])} / price {usd(c['price'])} = {c['food_cost_pct']}% food cost")
    for l in c["lines"]:
        if l["via"] != "direct":
            print(f"        {l['material']} {l['qty']:g} {l['unit']} via {l['via']}: {l['cents']:.1f}c")
print("\n   Hidden-ingredient check on order notes:")
for dish, note in [("House burger", "no tomato"), ("Fish tacos", "no tomato"), ("Chicken salad", "no tomato")]:
    print(f"   {dish} '{note}': {check_order_note(db, dish, note)}")

print("\n   Portions left before service:")
for r in portions_left(db):
    print(f"     {r['dish']:<14} {r['portions']:>3}  (limited by {r['limited_by']})")
sell(db, "Fish tacos", 40, D + "T19:30", "POS dinner"); sell(db, "House burger", 30, D + "T19:30", "POS dinner")
print("   After selling 40 fish tacos and 30 burgers (inventory depleted from POS sales):")
for r in portions_left(db):
    print(f"     {r['dish']:<14} {r['portions']:>3}  (limited by {r['limited_by']})")

print("\n   Next morning: fish delivery short, 160 oz expected, 60 oz received")
before = next(r for r in portions_left(db) if r["dish"] == "Fish tacos")["portions"]
receive_short(db, "white fish", 160, 60, "2026-11-21T08:00", "INV-8812")
tid = open_ticket(db, "SHIPMENT", "Sysco INV-8812 short 100 oz white fish (perishable)",
                  ticket_priority(db, "SHIPMENT", perishable=True, dollars=55 * 100 / 100), "2026-11-21T08:00", "receiver_jo")
after = next(r for r in portions_left(db) if r["dish"] == "Fish tacos")
print(f"   Fish tacos: {after['portions']} portions instead of {before + 32}  -> {tid} opened (only received fish entered inventory)")
if after["portions"] < 30:
    print(f"   86 WARNING: Fish tacos will run out mid-service; push House burger or Chicken salad")
print("   Cook view: portions and allergens only:", "costs BLOCKED" if not can_see(db, "cook_luis", "order_costs") else "costs visible")

"""Run: python cs_demo.py
Customer Service: questions instead of silent changes, tied to order lines (UPC, price, qty, specs). Sample data."""
import json
from hiwr_contacts import *
from hiwr_stew import portions_left
from hiwr_cs import *

AT = "2026-11-20T19:"
db = connect()
add_plan_owner(db, "owner_alex", "setup")
mats = [("brioche bun", 50), ("beef patty", 50), ("lettuce", 100), ("mayo", 100), ("ketchup", 100), ("pickle", 50),
        ("corn tortilla", 300), ("white fish", 200), ("roma tomato", 200), ("white onion", 100), ("cilantro", 30), ("lime", 40), ("crema", 60), ("potato", 400)]
for m, on in mats:
    db.execute("INSERT INTO materials VALUES (?, 'unit', ?, 0)", (m, on))
db.executemany("INSERT INTO material_allergens VALUES (?,?)", [("roma tomato", "tomato"), ("ketchup", "tomato"), ("crema", "dairy"), ("mayo", "egg")])
db.executemany("INSERT INTO recipes VALUES (?,?,?,?,?)", [("Pico de gallo", "sub", 32, "oz", None), ("House sauce", "sub", 20, "oz", None),
    ("House burger", "dish", 1, "plate", 1400), ("Fish tacos", "dish", 1, "plate", 1500), ("Fries", "dish", 1, "plate", 400)])
db.executemany("INSERT INTO recipe_lines VALUES (?,?,?,?)", [
    ("Pico de gallo", "roma tomato", 20, "oz"), ("Pico de gallo", "white onion", 8, "oz"), ("Pico de gallo", "cilantro", 2, "oz"), ("Pico de gallo", "lime", 2, "each"),
    ("House sauce", "mayo", 12, "oz"), ("House sauce", "ketchup", 6, "oz"), ("House sauce", "pickle", 2, "oz"),
    ("House burger", "brioche bun", 1, "each"), ("House burger", "beef patty", 1, "each"), ("House burger", "lettuce", 1, "oz"), ("House burger", "House sauce", 1.5, "oz"),
    ("Fish tacos", "corn tortilla", 3, "each"), ("Fish tacos", "white fish", 5, "oz"), ("Fish tacos", "Pico de gallo", 2, "oz"), ("Fish tacos", "crema", 1, "oz"),
    ("Fries", "potato", 8, "oz")])
db.executemany("INSERT INTO catalog VALUES (?,?,?)", [("0001", "House burger", 1400), ("0002", "Fish tacos", 1500), ("0003", "Fries", 400)])

maria = add_contact(db, "Maria Lopez", "server_kim", phone="910-555-0101")["id"]
record_consent(db, "910-555-0101", "transactional", "granted", "waitlist", "server_kim")
o = create_order(db, "restaurant_live", "dine_in", [], "server_kim", contact_id=maria, source_ref="POS-2210")
b1 = add_line(db, o, "0001", 1, {"no": ["tomato"]}, "server_kim", AT + "02")
b2 = add_line(db, o, "0001", 1, {"allergy": ["tomato"]}, "server_kim", AT + "02")
t1 = add_line(db, o, "0002", 2, {}, "server_kim", AT + "02")

print("1. ORDER POS-2210, table 12 (Maria): 2 burgers, 2 fish tacos")
print("   Burger A: guest said 'no tomato' (preference).  Burger B: guest has a declared tomato allergy.")
for line in check_line(db, b1, AT + "03") + check_line(db, b2, AT + "03"):
    print("  ", line)
print("   Kitchen ticket now:")
for k in kitchen_ticket(db, o):
    print("     ", k)

print("\n2. SERVER ASKS THE TABLE")
print("  ", say(db, o, "server", "table", "Maria says ketchup in the sauce is fine, she just doesn't want sliced tomato.", AT + "05"))
answer(db, b1, "keep", "server_kim", AT + "05", component="House sauce", tag="tomato")
print("  ", say(db, o, "server", "table", "Allergy guest confirms: hold the house sauce, swap for plain mayo.", AT + "06"))
answer(db, b2, "swap:mayo", "server_kim", AT + "06", component="House sauce")
print("   Kitchen ticket:")
for k in kitchen_ticket(db, o):
    print("     ", k)
advance_order(db, o, "fired", "cook_luis")

print("\n3. CUSTOMER UPDATE BY TEXT after firing")
print("  ", say(db, o, "customer", "sms", "can we add 2 fries and make it 3 tacos?", AT + "12"))
d = propose_change(db, o, "hiwr", AT + "12", add={"upc": "0003", "qty": 2}, set_qty=(t1, 3))
print(f"   Draft: {'; '.join(d['lines'])}. New total {usd(d['new_total'])} (was {usd(order_total(db, o))}). Needs: {d['needs']}")
try:
    apply_change(db, o, d, "customer", "customer", AT + "13", add={"upc": "0003", "qty": 2}, set_qty=(t1, 3))
except PermissionError as e:
    print("   Customer YES alone: BLOCKED:", e)
total = apply_change(db, o, d, "customer", "chef_marco", AT + "14", add={"upc": "0003", "qty": 2}, set_qty=(t1, 3))
print(f"   chef_marco OKs (fries and one more taco can still be made) -> total {usd(total)}")
print("  ", status_update(db, o, "Got it: 2 fries added and a third taco. They'll be out in about 8 minutes.", AT + "14"))

print("\n4. INVENTORY FOLLOWS THE ANSWERS")
before = {m: db.execute("SELECT on_hand FROM materials WHERE material=?", (m,)).fetchone()[0] for m in ("ketchup", "mayo", "white fish")}
sell_order(db, o, AT + "40")
after = {m: db.execute("SELECT on_hand FROM materials WHERE material=?", (m,)).fetchone()[0] for m in before}
for m in before:
    print(f"   {m:<11} {before[m]:g} -> {after[m]:g}")
print("   (Burger B's house sauce was held, so only Burger A used ketchup; tacos used fish for 3, not 2.)")

print("\n5. CHANGE LOG for POS-2210")
for r in db.execute("SELECT c.*, i.item FROM order_line_changes c LEFT JOIN order_items i ON i.id=c.order_item_id WHERE c.order_id=? ORDER BY c.id", (o,)):
    print(f"   {r['at'][11:]} {r['item'] or '':<13} {r['field']:<6} by {r['requested_by']:<26} confirmed {r['confirmed_by'] or '-'}")

print("\n6. MADE-TO-ORDER: boot size change")
sam = add_contact(db, "Sam Carter", "owner_alex", phone="302-555-0142")["id"]
db.execute("INSERT INTO catalog VALUES ('B-100', 'Western boot, roper toe', 65000)")
bo = create_order(db, "boots_made_to_order", "ship", [], "owner_alex", contact_id=sam, due_at="2027-01-15")
bl = add_line(db, bo, "B-100", 1, {"size": "10.5D"}, "owner_alex", "2026-11-20T10:00")
d = propose_change(db, bo, "hiwr", "2026-11-21T09:00", set_spec=(bl, "size", "11D"))
print(f"   Before cutting: {d['lines'][0]} -> needs {d['needs']}")
apply_change(db, bo, d, "customer", "customer", "2026-11-21T09:05", set_spec=(bl, "size", "11D"))
for st in ("pattern", "cut"):
    advance_order(db, bo, st, "maker_dee")
d = propose_change(db, bo, "hiwr", "2026-11-25T09:00", set_spec=(bl, "size", "11.5D"))
print(f"   After cutting:  {d['lines'][0]} -> needs {d['needs']}; leather already cut, remake cost goes on a DISPUTE ticket if charged")

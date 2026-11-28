"""Run: python barcode_demo.py
Barcodes and inventory vectors: check digits, case vs can, a deli label with the price inside, a GS1-128 case
with lot and use-by, a recall search, then the stock matrix with reorder proposals and similar-name suggestions.
Sample data."""
from hiwr_contacts import connect, add_plan_owner
from hiwr_access import set_level
from hiwr_barcodes import *
from hiwr_inventory_vectors import *

D = "2026-11-28"
db = connect()
add_plan_owner(db, "owner_alex", "setup")
set_level(db, "stocker_tim", 0, "owner_alex"); set_level(db, "lead_rosa", 1, "owner_alex"); set_level(db, "chef_marco", 2, "owner_alex")

print("CHECK DIGITS")
print(f"   0 36000 29145 ? -> check digit {check_digit('03600029145')}  (036000291452)")
print(f"   EAN-13 400638133393 ? -> {check_digit('400638133393')}")
print(f"   UPC-E 00123457 expands to {expand_upce('00123457')}")
print(f"   6-pack GTIN-14: {with_indicator('036000291452', 1)}   24-can case: {with_indicator('036000291452', 5)}")
print(f"   House code #1023 (marinara, no UPC): {house_code(1023)}")
print(f"   Typo 036000291453 valid? {is_valid('036000291453')}")

# Warehouse items and their codes at each pack level
items = [("COLA-12", "Cola 12oz can", "drinks", 125, 48, 6, 30, None),
         ("CHIP-01", "Tortilla chips 13oz", "snacks", 449, 210, 4, 2, None),
         ("CHIP-09", "Tortilla Chips 13 oz", "snacks", 449, 205, 3, 0, None),
         ("YOGT-03", "Greek yogurt 32oz", "dairy", 599, 310, 6, 10, "2026-12-03")]
for sku, name, cat, price, cost, shelf, back, exp in items:
    db.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?,?)", (sku, name, cat, price, cost, shelf, back, exp))
add_barcode(db, "036000291452", "product", "COLA-12", "each", 1, "each", "vendor", "owner_alex", D)
add_barcode(db, with_indicator("036000291452", 1), "product", "COLA-12", "inner", 6, "each", "vendor", "owner_alex", D)
add_barcode(db, with_indicator("036000291452", 5), "product", "COLA-12", "case", 24, "each", "vendor", "owner_alex", D)

print("\nSCANS AT THE DOCK (stocker_tim)")
for i, code in enumerate(("036000291452", "50036000291457", "50036000291458")):
    r = scan(db, code, "stocker_tim", D + f"T07:0{i}")
    print(f"   {code:<16} {r['result']:<16}" + (f" {r['item_ref']} +{r['base_qty']:g} {r['base_unit']} ({r['pack_level']})" if r["result"] == "ok" else f" \"{r['ask']}\""))

print("\nUNKNOWN CODE: Tim scans a chips case HiWR has never seen")
r = scan(db, "10012345000017", "stocker_tim", D + "T07:05")
print(f"   {r['result']}: \"{r['ask']}\"")
s = suggest_for_unknown(db, "10012345000017", "tortilla chips 13 oz case of 12", D)
for c in s["candidates"]:
    print(f"   suggestion {c['item_ref']:<8} {c['name']:<22} similarity {c['score']}")
add_barcode(db, "10012345000017", "product", "CHIP-01", "case", 12, "each", "staff_scan", "stocker_tim", D)
r = scan(db, "10012345000017", "stocker_tim", D + "T07:06")
print(f"   Tim picks CHIP-01, 12 per case -> next scan: {r['result']}")
confirm_barcode(db, "10012345000017", "chef_marco", D + "T07:30")
r = scan(db, "10012345000017", "stocker_tim", D + "T07:31")
receive(db, r, "stocker_tim", D + "T07:31")
back = db.execute("SELECT back_stock FROM products WHERE sku='CHIP-01'").fetchone()[0]
print(f"   chef_marco confirms -> {r['result']}, +{r['base_qty']:g} {r['base_unit']}; CHIP-01 back stock now {back}")

print("\nDELI SCALE: 2 01234 0 0599 5")
db.execute("INSERT INTO materials VALUES ('turkey breast sliced', 'lb', 12, 1)")
db.execute("INSERT INTO variable_measure_items VALUES ('01234', 'material', 'turkey breast sliced', 'price', 899, 'lb')")
r = scan(db, "201234005995", "lead_rosa", D + "T11:00", purpose="sale")
print(f"   $5.99 at $8.99/lb -> {r['base_qty']} lb of {r['item_ref']}")

print("\nGS1-128 CHICKEN CASE")
db.execute("INSERT INTO materials VALUES ('chicken thigh', 'lb', 30, 1)")
add_barcode(db, "10012345000208", "material", "chicken thigh", "case", 40, "lb", "vendor", "owner_alex", D)
label = "(01)10012345000208(17)270315(10)LOT4471B(3201)000402"
print(f"   {label}")
print(f"   parsed: {parse_gs1(label)}")
r = scan(db, label, "lead_rosa", D + "T08:00", vendor="Sysco", lot_tracked=("chicken thigh",))
receive(db, r, "lead_rosa", D + "T08:00")
print(f"   {r['result']}: +{r['base_qty']} {r['base_unit']} (label weight wins over the nominal 40), lot id {r['lot_id']}")
r = scan(db, "10012345000208", "lead_rosa", D + "T08:01", lot_tracked=("chicken thigh",))
print(f"   same case, plain code only -> {r['result']}: \"{r['ask']}\"")

print("\nRECALL: lot LOT4471B")
set_lot_status(db, 1, "on_hold", "FDA recall notice", "lead_rosa", D + "T09:00")
for t in trace_lot(db, "LOT4471B"):
    print(f"   {t['item']}, use by {t['expires_on']}, from {t['vendor']}, status {t['status']}, {len(t['scans'])} receiving scan(s)")
r = scan(db, label, "lead_rosa", D + "T09:05", vendor="Sysco")
print(f"   next scan of that lot -> {r['result']}: \"{r['ask']}\"")

print("\nSTOCK VECTORS (one row of numbers per SKU)")
from datetime import date, timedelta
for back in range(1, 29):
    day = (date(2026, 11, 28) - timedelta(days=back)).isoformat()
    for sku, n in (("COLA-12", 6), ("CHIP-01", 2), ("CHIP-09", 1), ("YOGT-03", 1)):
        db.execute("INSERT INTO daily_sales VALUES (?,?,?)", (sku, day, n))
for who in ("stocker_tim", "chef_marco"):
    m = matrix(db, who, D)
    print(f"   {who}: {len(m['rows'])} rows x {len(m['columns'])} columns{' (costs hidden)' if 'cost_cents' not in m['columns'] else ''}")
m = matrix(db, "chef_marco", D)
print("   " + "SKU".ljust(9) + "".join(c.rjust(15) for c in ("on_hand", "per_day_28", "days_of_supply", "days_to_expiry", "margin_pct")))
for k, row in zip(m["keys"], m["rows"]):
    pick = [row[m["columns"].index(c)] for c in ("on_hand", "per_day_28", "days_of_supply", "days_to_expiry", "margin_pct")]
    print("   " + k.ljust(9) + "".join(f"{x:>15g}" for x in pick))

print("\nPROPOSALS (a manager decides; nothing is ordered here)")
props = stock_proposals(db, "chef_marco", D)
first = next(p["id"] for p in props if p["kind"] == "reorder")
for p in props:
    if p["kind"] == "reorder":
        print(f"   #{p['id']} reorder {p['item']}: {p['why']} -> {p['cases']} case(s) of {p['case_size']:g} = {p['units']:g}")
    else:
        print(f"   #{p['id']} expiry risk {p['item']}: ~{p['expiring_unsold']:g} units by use-by")
for p in possible_duplicates(db, D):
    print(f"   #{p['id']} possible duplicate SKUs {p['a']} and {p['b']} (similarity {p['score']})")
try:
    decide(db, first, "accepted", "stocker_tim", D)
except PermissionError as e:
    print(f"   stocker_tim accepts #{first}: BLOCKED ({e})")
decide(db, first, "accepted", "chef_marco", D)
print(f"   chef_marco accepts #{first}: recorded; the order itself is still a purchase draft for a person")

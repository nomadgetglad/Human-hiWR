"""Run: python retail_demo.py
A grocery with two fast sellers and several items piling up in back stock. Sample data."""
import json, random
from datetime import date, timedelta
from hiwr_contacts import *
from hiwr_access import *
from hiwr_retail import *

def usd(c): return f"${c / 100:,.2f}"
TODAY = "2026-11-20"
random.seed(4)
db = connect()
add_plan_owner(db, "owner_alex", "setup")
set_level(db, "manager_jo", 2, "owner_alex"); set_level(db, "clerk_sam", 0, "owner_alex")

products = [  # sku, name, category, price, cost, shelf, back, expiry, avg/day
    ("CHIP-01", "Tortilla chips 13oz", "snacks", 449, 210, 30, 20, "2027-03-01", 9.0),
    ("SALS-02", "Fresh salsa 16oz", "deli", 499, 230, 24, 96, "2026-12-08", 2.5),
    ("YOGT-03", "Greek yogurt 32oz", "dairy", 599, 300, 20, 10, "2026-12-01", 3.0),
    ("BREW-04", "Cold brew 6-pack", "drinks", 899, 420, 18, 12, "2027-05-01", 7.0),
    ("OATM-05", "Oat milk 64oz", "drinks", 549, 260, 24, 120, "2027-01-10", 1.6),
    ("SOUP-06", "Canned soup 15oz", "pantry", 249, 110, 48, 200, "2028-06-01", 2.2),
    ("CERE-07", "Granola 12oz", "pantry", 699, 330, 18, 30, "2027-04-01", 1.5),
]
start = date.fromisoformat(TODAY) - timedelta(days=28)
for sku, name, cat, price, cost, shelf, back, exp, avg in products:
    db.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?,?)", (sku, name, cat, price, cost, shelf, back, exp))
    for i in range(28):
        db.execute("INSERT INTO daily_sales VALUES (?,?,?)",
                   (sku, (start + timedelta(days=i)).isoformat(), max(0, round(random.gauss(avg, avg * 0.3)))))

rows = sweep(db, TODAY)
print("1. SWEEP (last 28 days)")
print(f"   {'item':<22}{'sold/day':>9}{'stock':>7}{'days of supply':>16}{'expires in':>12}{'unsold at expiry':>18}  status")
for r in rows:
    exp = f"{r['days_to_expiry']}d" if r["days_to_expiry"] is not None else "-"
    dos = "inf" if r["days_of_supply"] == float("inf") else f"{r['days_of_supply']:g}"
    print(f"   {r['name']:<22}{r['velocity']:>9}{r['stock']:>7}{dos:>16}{exp:>12}{r['units_expiring_unsold']:>18}  {r['status'].upper()}")

fast = sorted([r for r in rows if r["status"] == "fast"], key=lambda r: -r["velocity"])
slow = [r for r in rows if r["status"] == "slow"]
print("\n2. PROPOSALS to manager_jo  (Inventory Management > sales)")
props = []
for item in slow:
    pid, fc = propose(db, item, fast, TODAY, 14, "hiwr")
    s, n = fc["sale"], fc["no_sale"]
    props.append((pid, item, fc))
    print(f"   #{pid} {item['name']} ({'perishable, ' + str(item['days_to_expiry']) + ' days to expiry' if fc['perishable'] else 'shelf-stable'})")
    print(f"       best discount {int(s['pct_off'] * 100)}% ({usd(item['price'])} -> {usd(s['sale_price'])}) over {fc['horizon_days']} days:"
          f" sells {s['units_sold']}/{item['stock']} vs {n['units_sold']} with no sale")
    print(f"       margin {usd(s['margin'])} vs {usd(n['margin'])}"
          + (f" (no sale throws out {n['left_over']} units, {usd(n['write_off'])})" if n["write_off"] else "")
          + f"  -> {'+' if fc['gain_vs_no_sale'] >= 0 else '-'}{usd(abs(fc['gain_vs_no_sale']))}")
    if not fc["perishable"]:
        print(f"       {usd(fc['back_stock_cost_tied_up'])} tied up in back stock; pause reorders {fc['pause_reorders_days']} days"
              f" (until {fc['pause_until']}) to get back to 30 days of supply")
    if fc.get("bundle_idea"):
        print(f"       bundle: {fc['bundle_idea']}")
print(f"   Lift assumption: {fc['lift_assumption']} (sample; check-ins replace it with this store's results)")

print("\n3. DECISIONS")
try:
    decide(db, props[0][0], "approved", "clerk_sam", TODAY)
except PermissionError as e:
    print("   Blocked:", e)
for pid, item, fc in props:
    if fc["perishable"] and fc["gain_vs_no_sale"] > 0:
        print(f"   #{pid} {item['name']}: approved ->", decide(db, pid, "approved", "manager_jo", TODAY, "clear before expiry"))
    elif not fc["perishable"]:
        decide(db, pid, "changed", "manager_jo", TODAY, f"no markdown; pause reorders until {fc['pause_until']}; run bundle display only")
        print(f"   #{pid} {item['name']}: changed -> no markdown, reorders paused until {fc['pause_until']}, bundle display only")
    else:
        print(f"   #{pid} {item['name']}: rejected ->", decide(db, pid, "rejected", "manager_jo", TODAY, "sale loses margin"))

print("\n4. CHECK-IN after the sale (Dec 8, sample results)")
salsa = next(p for p in props if p[1]["sku"] == "SALS-02")
fc = json.loads(db.execute("SELECT forecast FROM sale_proposals WHERE id=?", (salsa[0],)).fetchone()["forecast"])
actual = fc["sale"]["units_sold"] - 6
print(f"   Fresh salsa: forecast {fc['sale']['units_sold']} units, actual {actual}",
      "->", check_in(db, f"sale:{salsa[0]}", "manager_jo", works=True, intended_consequence=actual >= fc["sale"]["units_sold"] * 0.9,
                     note=f"sold {actual}; 6 short of forecast, lift was lower than assumed"))
print("   Next proposals use the measured lift for deli items.")

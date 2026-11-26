"""Run: python margin_sim.py
Margin simulation on the Stew demo (sample data): 5 dinner hours (17:00 to 21:00) under bad, average and good
numbers, run once with a fixed plan and once with Human's adaptive algorithms. For each run it compares:

- Profit margin, and how steady it is from hour to hour.
- Manipulation cost: how much people had to step in to steer it. Every confirm card someone has to answer
  (fry, call cover, send someone home, 86 a dish) counts as a touch at 2 minutes of a manager's time, plus the
  dollars those corrections cost (call-in premium, chips thrown out, margin lost to running out).
- Fluidity of motion: the share of hours that ran without a stall (running out of something) or a jolt
  (frying 2+ batches more or less than the hour before, or changing who is on the line).

Everything is sample data. Nothing here touches a real business or moves money.
"""
import math
import sys
from pathlib import Path
from statistics import pstdev

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
from server import seed  # noqa: E402  (also puts hiwr/ on the path)
from hiwr_prep import prep_plan, make_batches, good_on_hand, item_forecast  # noqa: E402
from hiwr_stew import cost_card, sell, explode, unit_cost  # noqa: E402

ITEM = "fried tortilla chips"
STEPS = [17, 18, 19, 20, 21]
TOUCH_CENTS = round(2 / 60 * 2500)          # 2 minutes of a $25/h manager per confirm card
COOK_CENTS, COVERS_PER_COOK = 1800, 40      # sample rate; staffing rule: 1 cook per 40 covers
CALL_IN_PREMIUM = 0.5                        # same-night cover costs time and a half
COVERS_PER_PLATE = 1.0

SCENARIOS = {
    # demand vs forecast, cooks scheduled, cooks who call out, white fish on hand at 17:00
    "bad": {"demand": [0.75, 0.55, 0.45, 0.60, 0.50], "cooks": 4, "call_outs": 1, "fish_oz": 60},
    "average": {"demand": [1.00, 0.95, 1.05, 1.00, 1.00], "cooks": 4, "call_outs": 0, "fish_oz": 400},
    "good": {"demand": [1.20, 1.45, 1.60, 1.50, 1.30], "cooks": 4, "call_outs": 0, "fish_oz": 400},
}


def _price(db, dish):
    r = db.execute("SELECT price_cents FROM recipes WHERE name=?", (dish,)).fetchone()
    return r["price_cents"] if r and r["price_cents"] else seed.SIDE_PRICES.get(dish, 0)


def _usage(db):
    return {r["dish"]: r["qty"] for r in db.execute("SELECT dish, qty FROM prep_usage WHERE item=?", (ITEM,))}


def _can_make(db, dish):
    """How many plates the stock allows (chips are handled separately)."""
    if not db.execute("SELECT 1 FROM recipes WHERE name=?", (dish,)).fetchone():
        return 10**9
    lim = 10**9
    for m, (q, u, via) in explode(db, dish).items():
        on = db.execute("SELECT on_hand FROM materials WHERE material=?", (m,)).fetchone()["on_hand"]
        lim = min(lim, math.floor(on / q) if q else lim)
    return lim


def run(scenario, policy):
    sc = SCENARIOS[scenario]
    db = seed.build_demo("stew")
    db.execute("UPDATE materials SET on_hand=? WHERE material='white fish'", (sc["fish_oz"],))
    p = db.execute("SELECT * FROM prep_items WHERE item=?", (ITEM,)).fetchone()
    chip_cost_per_oz = p["input_qty"] * unit_cost(db, p["input_material"]) / p["batch_qty"]
    usage = _usage(db)
    fc = item_forecast(db, ITEM, seed.TODAY, 1.0)
    cooks = sc["cooks"] - sc["call_outs"]
    rows, prev_batches, prev_cooks, out_86 = [], None, cooks, set()
    waste_seen = 0.0
    for i, h in enumerate(STEPS):
        now = f"{seed.TODAY}T{h:02d}:00"
        touches, fix_cents, jolt = 0, 0, False

        # ---- fryer: decide batches for this hour ----
        good, _ = good_on_hand(db, ITEM, now)
        if policy == "human":
            n = prep_plan(db, ITEM, now, seed.RESERVATIONS, seed.USUAL_RESERVATIONS)["fry_batches_now"]
        else:   # fixed: last 4 weeks' forecast for the hour, no reading of how tonight is going
            n = max(0, math.ceil((fc[h] * (1 + p["buffer_pct"]) - good) / p["batch_qty"]))
        if n:
            make_batches(db, ITEM, n, now, "fry_rosa")
            touches += 1
        if prev_batches is not None and abs(n - prev_batches) >= 2:
            jolt = True
        prev_batches = n

        # ---- demand for the hour ----
        want = {d: round(q.get(h, 0) * seed.BUSY_BOOST * sc["demand"][i]) for d, q in seed.ALL_SALES.items()}
        covers = sum(want.values()) * COVERS_PER_PLATE

        # ---- line: cooks needed vs on the line ----
        need_cooks = max(2, math.ceil(covers / COVERS_PER_COOK))
        if policy == "human":
            if need_cooks > cooks:              # call same-night cover (find_same_night + text offers)
                extra = need_cooks - cooks
                fix_cents += round(extra * COOK_CENTS * CALL_IN_PREMIUM)
                cooks += extra
                touches += 1
            elif cooks > need_cooks + 1 and i < len(STEPS) - 1:   # send one home early
                cooks -= 1
                touches += 1
        if cooks != prev_cooks:
            jolt = True
        prev_cooks = cooks
        capacity = 1.0 if cooks >= need_cooks else cooks / need_cooks     # short-handed: slower tickets

        # ---- sell what we can ----
        good, _ = good_on_hand(db, ITEM, now)
        chips_left = good
        rev = food = lost_margin = 0
        stall = False
        for dish, q in want.items():
            q_cap = math.floor(q * capacity)
            can = min(q_cap, _can_make(db, dish))
            if dish in usage:
                can = min(can, math.floor(chips_left / usage[dish]))
                chips_left -= can * usage[dish]
            if can < q:
                stall = True
                price = _price(db, dish)
                unit_food = cost_card(db, dish)["cost"] if db.execute(
                    "SELECT 1 FROM recipes WHERE name=?", (dish,)).fetchone() else usage.get(dish, 0) * chip_cost_per_oz
                lost_margin += (q - can) * (price - unit_food)
                if can < q_cap and dish not in out_86:    # out of stock: someone 86's it on the menu
                    out_86.add(dish)
                    touches += 1
            if can:
                if db.execute("SELECT 1 FROM recipes WHERE name=?", (dish,)).fetchone():
                    food += can * cost_card(db, dish)["cost"]
                    sell(db, dish, can, now, "sim")
                food += can * usage.get(dish, 0) * chip_cost_per_oz
                rev += can * _price(db, dish)
                db.execute("INSERT INTO dish_sales VALUES (?,?,?,?)", (seed.TODAY, h, dish, can))

        # ---- waste: chips past their hold time by the end of the hour ----
        _, waste_oz = good_on_hand(db, ITEM, f"{seed.TODAY}T{h:02d}:59")
        new_waste = max(0.0, waste_oz - waste_seen)
        waste_seen = waste_oz
        waste_cents = round(new_waste * chip_cost_per_oz)
        fry_cents = round(n * p["batch_qty"] * chip_cost_per_oz)

        labor = cooks * COOK_CENTS
        manip = touches * TOUCH_CENTS + fix_cents + waste_cents + round(lost_margin)
        profit = rev - food - labor - fix_cents - touches * TOUCH_CENTS - waste_cents
        rows.append({"hour": h, "batches": n, "cooks": cooks, "revenue": rev, "food": round(food), "labor": labor,
                     "fry_cents": fry_cents, "waste_cents": waste_cents, "touches": touches, "fix_cents": fix_cents,
                     "lost_margin": round(lost_margin), "manip_cents": manip, "profit": round(profit),
                     "margin": profit / rev if rev else -1.0, "stall": stall, "jolt": jolt})
    db.close()
    rev = sum(r["revenue"] for r in rows)
    profit = sum(r["profit"] for r in rows)
    return {"scenario": scenario, "policy": policy, "rows": rows, "revenue": rev, "profit": profit,
            "margin": profit / rev if rev else 0.0,
            "margin_swing": pstdev([r["margin"] for r in rows]),
            "touches": sum(r["touches"] for r in rows),
            "manip_cents": sum(r["manip_cents"] for r in rows),
            "waste_cents": sum(r["waste_cents"] for r in rows),
            "lost_margin": sum(r["lost_margin"] for r in rows),
            "stalls": sum(1 for r in rows if r["stall"]), "jolts": sum(1 for r in rows if r["jolt"]),
            "fluidity": sum(1 for r in rows if not r["stall"] and not r["jolt"]) / len(rows)}


def usd(c):
    return f"${c / 100:,.0f}"


def main():
    results = [run(s, pol) for s in SCENARIOS for pol in ("fixed", "human")]
    print("Margin simulation, Stew demo, 5 dinner hours each (sample data)\n")
    print(f"{'numbers':<9}{'plan':<7}{'revenue':>9}{'profit':>9}{'margin':>8}{'swing':>7}"
          f"{'touches':>9}{'manip $':>9}{'waste':>7}{'lost':>7}{'ran out':>8}{'jolts':>6}{'fluid':>7}")
    for r in results:
        print(f"{r['scenario']:<9}{r['policy']:<7}{usd(r['revenue']):>9}{usd(r['profit']):>9}{r['margin']:>8.0%}"
              f"{r['margin_swing']:>7.0%}{r['touches']:>9}{usd(r['manip_cents']):>9}{usd(r['waste_cents']):>7}"
              f"{usd(r['lost_margin']):>7}{r['stalls']:>8}{r['jolts']:>6}{r['fluidity']:>7.0%}")
    print("\nHour by hour (batches fried / cooks on line / margin; ! = ran out, ~ = jolt)")
    for r in results:
        cells = []
        for x in r["rows"]:
            mark = ("!" if x["stall"] else "") + ("~" if x["jolt"] else "")
            cells.append(f"{x['hour']}h {x['batches']}b/{x['cooks']}c {x['margin']:.0%}{mark}")
        print(f"  {r['scenario']:<8}{r['policy']:<6} " + " | ".join(cells))
    return results


if __name__ == "__main__":
    main()

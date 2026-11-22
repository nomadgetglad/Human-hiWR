"""Retail sweep: find fast sellers and slow movers, then propose a sale to clear back stock.

Velocity       = units sold per day over the last 28 days.
Days of supply = (shelf + back stock) / velocity.
Slow mover     = days of supply over the target (default 30), or more stock than will sell before expiry.
Lift guess     = each 10% off sells 20% more units (sample assumption; replace with the store's own results
                 once a few sales have run, which the check-in records).
The proposal compares the sale against doing nothing, where unsold stock past expiry is written off at cost.
Prices change only after a manager (level 2+, or someone certified for price_change) approves.
"""
import json
from datetime import date, timedelta
from hiwr_access import level, can_do

LIFT_PER_10_PCT = 0.20
STEPS = [0.10, 0.20, 0.25, 0.30, 0.40, 0.50]


def _d(s): return date.fromisoformat(s)


def sweep(db, today, window=28, target_days=30):
    start = (_d(today) - timedelta(days=window)).isoformat()
    out = []
    for p in db.execute("SELECT * FROM products"):
        sold = db.execute("SELECT COALESCE(SUM(units),0) FROM daily_sales WHERE sku=? AND day>=? AND day<?",
                          (p["sku"], start, today)).fetchone()[0]
        v = sold / window
        stock = p["shelf"] + p["back_stock"]
        dos = stock / v if v else float("inf")
        to_expiry = (_d(p["expires_on"]) - _d(today)).days if p["expires_on"] else None
        will_expire = max(0, round(stock - v * to_expiry)) if to_expiry is not None else 0
        status = "fast" if dos < 10 else "slow" if dos > target_days or will_expire else "ok"
        out.append({"sku": p["sku"], "name": p["name"], "category": p["category"], "velocity": round(v, 1),
                    "stock": stock, "back_stock": p["back_stock"], "days_of_supply": round(dos, 1),
                    "days_to_expiry": to_expiry, "units_expiring_unsold": will_expire, "status": status,
                    "price": p["price_cents"], "cost": p["cost_cents"]})
    out.sort(key=lambda r: -r["days_of_supply"])
    return out


PERISHABLE_DAYS = 60


def _forecast(item, pct, horizon):
    """Units, revenue and margin over the horizon; stock still unsold at expiry is written off at cost."""
    v = item["velocity"] * (1 + LIFT_PER_10_PCT * pct / 0.10)
    units = min(item["stock"], round(v * horizon))
    price = round(item["price"] * (1 - pct))
    left = item["stock"] - units
    expires = item["days_to_expiry"] is not None and item["days_to_expiry"] <= horizon + 1
    write_off = left * item["cost"] if expires else 0
    return {"pct_off": pct, "sale_price": price, "units_sold": units, "left_over": left,
            "revenue": units * price, "margin": units * (price - item["cost"]) - write_off, "write_off": write_off,
            "below_cost": price < item["cost"]}


def propose(db, item, fast_items, today, days, actor, target_days=30):
    """Perishable: pick the discount with the best margin up to expiry, counting the write-off it avoids.
    Shelf-stable overstock: a discount rarely pays, so propose pausing reorders until supply is back to
    target, with an optional light bundle. Every proposal waits for a manager."""
    perishable = item["days_to_expiry"] is not None and item["days_to_expiry"] <= PERISHABLE_DAYS
    horizon = max(1, item["days_to_expiry"] - 1) if perishable else days
    base = _forecast(item, 0.0, horizon)
    options = [_forecast(item, p, horizon) for p in STEPS]
    best = max(options, key=lambda o: o["margin"])
    partner = next((f for f in fast_items if f["category"] == item["category"]), fast_items[0] if fast_items else None)
    fc = {"perishable": perishable, "horizon_days": horizon, "no_sale": base, "sale": best,
          "gain_vs_no_sale": best["margin"] - base["margin"],
          "lift_assumption": f"{int(LIFT_PER_10_PCT * 100)}% more units per 10% off"}
    if not perishable:
        pause = max(0, round((item["stock"] - target_days * item["velocity"]) / item["velocity"])) if item["velocity"] else None
        fc["pause_reorders_days"] = pause
        fc["pause_until"] = (_d(today) + timedelta(days=pause)).isoformat() if pause is not None else None
        fc["back_stock_cost_tied_up"] = item["back_stock"] * item["cost"]
    if partner:
        fc["bundle_idea"] = (f"Display beside {partner['name']} ({partner['velocity']}/day): "
                             f"buy {partner['name']}, get {item['name']} {int(best['pct_off'] * 100)}% off")
    pid = db.execute("INSERT INTO sale_proposals(sku, kind, pct_off, bundle_with, starts_on, ends_on, forecast, proposed_by) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     (item["sku"], "bundle" if partner else "markdown", best["pct_off"], partner["sku"] if partner else None,
                      today, (_d(today) + timedelta(days=horizon)).isoformat(), json.dumps(fc), actor)).lastrowid
    return pid, fc


def decide(db, proposal_id, decision, member, today, note=None):
    if not (level(db, member) >= 2 or can_do(db, member, "price_change", today)):
        raise PermissionError(f"{member} cannot approve price changes (needs Manager level or price_change)")
    db.execute("INSERT INTO sale_decisions(proposal_id, decision, decided_by, note) VALUES (?,?,?,?)",
               (proposal_id, decision, member, note))
    if decision == "approved":
        p = db.execute("SELECT * FROM sale_proposals WHERE id=?", (proposal_id,)).fetchone()
        return f"{p['sku']} goes to {int(p['pct_off'] * 100)}% off from {p['starts_on']} to {p['ends_on']}; shelf tags printed"
    return decision

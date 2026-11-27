"""HiWR side of margin work: sample-to-sample quantities. No advice here; that's Human's job (human_margins.py).

A sample is any window of days (a week, a Friday, a month). Monthly bills are spread by day so samples of
different lengths compare fairly. Counts (drinks, plates, hours) come from the POS and timeclock when posted;
the demo passes them in.
"""
from hiwr_access import level
from hiwr_accounting import numbers, _money, MOVE_FLAG_POINTS
from hiwr_contract import CONTRACT_VERSION

COLS = ("food_cost_pct", "beverage_cost_pct", "labor_pct", "prime_cost_pct", "operating_margin_pct")
SHORT = {"food_cost_pct": "food", "beverage_cost_pct": "bev", "labor_pct": "labor",
         "prime_cost_pct": "prime", "operating_margin_pct": "margin"}


def label(w):
    return f"{w[0][5:]}..{w[1][5:]}"


def _vendor_shares(db, start, end, food_sales):
    out = {}
    for r in db.execute("SELECT t.counterparty v, SUM(t.amount_cents) a FROM acct_txns t JOIN acct_class_events e "
                        "ON e.txn_id=t.id AND e.id=(SELECT MAX(id) FROM acct_class_events WHERE txn_id=t.id) "
                        "WHERE e.class='food_cost' AND e.status='confirmed' AND t.txn_date BETWEEN ? AND ? "
                        "GROUP BY t.counterparty", (start, end)):
        out[r["v"]] = round(100 * r["a"] / food_sales, 1) if food_sales else 0
    return out


def snapshot(db, member, window, counts):
    """counts: {"labor_hours", "drinks_sold", "plates_sold", "waste"} from timeclock, POS and the waste log."""
    if level(db, member) < 3:
        raise PermissionError(f"snapshots carry dollars; needs Finance level (3), {member} is level {level(db, member)}")
    n = numbers(db, *window, prorate_fixed=True)
    m, bc = n["metrics"], n["by_class"]
    return {
        "contract": CONTRACT_VERSION, "window": window,
        "net_sales": m["net_sales"], "food_sales": bc.get("food_sales", 0), "beverage_sales": bc.get("beverage_sales", 0),
        "food_cost": bc.get("food_cost", 0), "beverage_cost": bc.get("beverage_cost", 0), "labor": m["labor"],
        "direct_labor": sum(d["direct_labor"] for d in n["departments"].values()), "shared_labor": n["shared_labor"],
        "occupancy": m["occupancy"], "operating": m["operating"], "waste": counts.get("waste", m["waste_memo"]),
        **{k: m[k] for k in COLS},
        "labor_hours": counts["labor_hours"], "avg_wage": round(bc.get("wages", 0) / counts["labor_hours"]),
        "drinks_sold": counts["drinks_sold"], "avg_drink_price": round(bc.get("beverage_sales", 0) / counts["drinks_sold"]),
        "plates_sold": counts["plates_sold"], "avg_plate_price": round(bc.get("food_sales", 0) / counts["plates_sold"]),
        "food_vendor_share_pct": _vendor_shares(db, *window, bc.get("food_sales", 0)),
        "unconfirmed_cents": n["unconfirmed"]["amount"],
    }


def sample_table(snaps, finance=True):
    """Sample to sample: each row is one sample; moves of 2+ points against the previous sample are called out."""
    L = ["   sample        " + ("net sales    " if finance else "") + "  ".join(f"{SHORT[c]:>6}" for c in COLS)]
    prev = None
    for s in snaps:
        line = f"   {label(s['window']):13} " + (f"{_money(s['net_sales']):>11}  " if finance else "")
        line += "  ".join(f"{s[c]:>5}%" for c in COLS)
        if prev:
            mv = [f"{SHORT[c]} {s[c] - prev[c]:+.1f}" for c in COLS if abs(s[c] - prev[c]) >= MOVE_FLAG_POINTS]
            if mv:
                line += "   moved: " + ", ".join(mv)
        L.append(line)
        prev = s
    return "\n".join(L)


def choose(db, member, window, option, needs_note):
    """The owner picks; logged with what Human said it needs, so the next sample can measure it."""
    if level(db, member) < 4:
        raise PermissionError("the account holder picks how to hold margin")
    db.execute("INSERT INTO margin_choices(sample, option, expected_cents, chosen_by, note) VALUES (?,?,?,?,?)",
               (label(window), option["name"], option["needs"].get("recover", 0), member, needs_note))

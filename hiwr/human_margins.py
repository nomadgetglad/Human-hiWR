"""Human side of margin work: reads a HiWR snapshot, returns OPTIONS with the NEEDED QUANTITIES for each.

It never reads the database. It only sees what HiWR put in the snapshot, checked against the shared contract,
so the two stay in step. Options are proposals ranked cheapest-to-the-guest first, with price last.
"""
from hiwr_contract import check_snapshot, check_option


def _m(c):
    return f"-${-c / 100:,.2f}" if c < 0 else f"${c / 100:,.2f}"


def break_even(s, target_pct=0.0):
    variable = (s["food_cost"] + s["beverage_cost"] + s["direct_labor"]) / s["net_sales"]
    fixed = s["shared_labor"] + s["occupancy"] + s["operating"]
    room = 1 - variable - target_pct / 100
    return round(fixed / room) if room > 0 else None


def options(cur, base, target_pct=None, max_price_pct=5.0, labor_cap_pct=35.0):
    """target_pct None = hold the margin the baseline sample had."""
    check_snapshot(cur)
    check_snapshot(base)
    if target_pct is None:
        target_pct = base["operating_margin_pct"]
    gap = round((target_pct - cur["operating_margin_pct"]) / 100 * cur["net_sales"])
    out = {"target": target_pct, "gap": gap, "break_even": break_even(cur), "sales_for_target": break_even(cur, target_pct), "options": []}
    if gap <= 0:
        return out
    opts = out["options"]
    if cur["food_cost_pct"] > base["food_cost_pct"]:
        rec = round((cur["food_cost_pct"] - base["food_cost_pct"]) / 100 * cur["food_sales"])
        vc, vb = cur["food_vendor_share_pct"], base["food_vendor_share_pct"]
        drv = max(vc, key=lambda v: vc[v] - vb.get(v, 0))
        opts.append({"name": "food_cost", "who": "Manager + owner",
                     "needs": {"recover": rec, "target_pct": base["food_cost_pct"]},
                     "how": f"{drv} went {vb.get(drv, 0)}% -> {vc[drv]}% of food sales. Check its prices against the quote, "
                            "price an alternate vendor in Ordering, and check portions on top sellers in Stew"})
    if cur["waste"]:
        opts.append({"name": "waste", "who": "Chef", "needs": {"recover": cur["waste"] // 2, "waste_to_avoid": cur["waste"] // 2},
                     "how": "prep to the hourly forecast (Prep level) and run back stock as specials before it turns"})
    lab_target = min(base["labor_pct"], labor_cap_pct)
    if cur["labor_pct"] > lab_target:
        rec = round((cur["labor_pct"] - lab_target) / 100 * cur["net_sales"])
        opts.append({"name": "labor", "who": "Manager",
                     "needs": {"recover": rec, "hours_to_move": round(rec / cur["avg_wage"], 1), "target_pct": lab_target},
                     "how": "take the hours from the slowest stretches in the schedule draft; keep minimum staffing; "
                            "no pay rate changes"})
    if cur["drinks_sold"]:
        extra = round(cur["drinks_sold"] * 0.05)
        keep = round(extra * cur["avg_drink_price"] * (1 - cur["beverage_cost_pct"] / 100))
        opts.append({"name": "beverage_attach", "who": "Manager", "needs": {"recover": keep, "extra_drinks": extra},
                     "how": f"suggest a drink at order; {extra} more drinks at {_m(cur['avg_drink_price'])} average"})
    left = gap - sum(o["needs"]["recover"] for o in opts)
    if left > 0 and cur["food_sales"]:
        pct = round(min(100 * left / cur["food_sales"], max_price_pct), 1)
        opts.append({"name": "price", "who": "Owner",
                     "needs": {"recover": round(pct / 100 * cur["food_sales"]), "price_change_pct": pct,
                               "per_plate_increase": round(pct / 100 * cur["avg_plate_price"])},
                     "how": "start with dishes over target on their Stew cost card; assumes guests keep ordering at the "
                            "same rate, so try a few items first" + (f"; capped at {max_price_pct:g}%" if pct >= max_price_pct else "")})
    for o in opts:
        check_option(o)
    return out


def needs_text(o):
    return ", ".join(f"{k.replace('_', ' ')} {_m(v)}" if k in ("recover", "waste_to_avoid", "per_plate_increase")
                     else f"{k.replace('_', ' ')} {v}{'%' if k.endswith('pct') else ''}" for k, v in o["needs"].items())


def render(cur, base, result):
    target_pct = result["target"]
    L = [f"Margin {cur['operating_margin_pct']}% vs target {target_pct}% (previous sample {base['operating_margin_pct']}%)",
         f"   break-even net sales {_m(result['break_even'])}; to keep {target_pct}% by more covers alone (same costs per "
         f"sale) it would need {_m(result['sales_for_target'])}, it did {_m(cur['net_sales'])}"]
    if result["gap"] <= 0:
        L.append(f"   Margin held, {_m(-result['gap'])} above target.")
        return "\n".join(L)
    L.append(f"   Gap {_m(result['gap'])}. Options, cheapest to the guest first:")
    run = 0
    for o in result["options"]:
        run += o["needs"]["recover"]
        needs = needs_text(o)
        L.append(f"   - {o['name']:15} needs: {needs}   [{o['who']}]  ({min(100, 100 * run / result['gap']):.0f}% of gap)")
        L.append(f"        {o['how']}")
    return "\n".join(L)

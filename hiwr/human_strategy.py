"""Human suggests inventory tasks to meet expected sales under three mindsets; the owner picks one.

  stable  - stock to cover most demand (about 84% of weekends never run out), keep the menu as is
  lean    - the risky one: stock to the middle of the forecast or under, least cash tied up and least waste,
            accept running out and 86-ing items late in the night
  growth  - plan for more sales than usual (a push such as a special or a catering offer), stock above that,
            more cash and more waste risk in exchange for more sales

Demand per item is a forecast with a spread (mean and standard deviation from past samples). For each mindset
the math gives the order, expected sales, expected leftover and waste, chance of running out, and cash tied up.
"""
from math import erf, exp, pi, sqrt

MINDSETS = {
    "stable": {"z": 1.0, "lift": 0.0, "label": "Stable"},
    "lean": {"z": -0.25, "lift": 0.0, "label": "Lean (risky)"},
    "growth": {"z": 1.3, "lift": 0.15, "label": "Growth"},
}


def _cdf(z):
    return 0.5 * (1 + erf(z / sqrt(2)))


def _pdf(z):
    return exp(-z * z / 2) / sqrt(2 * pi)


def outcome(mu, sd, q):
    """Expected units sold, leftover, and chance of running out when stocking q against demand ~ N(mu, sd)."""
    z = (q - mu) / sd
    short = sd * (_pdf(z) - z * (1 - _cdf(z)))
    sold = mu - short
    return sold, q - sold, 1 - _cdf(z)


def plan(items, on_hand, mindset):
    """items: [{dish, material, per_dish, mu, sd, price, unit_cost, spoil}] per dish for the period.
    on_hand: {material: units}. spoil = share of leftover that's thrown out (perishables near 1)."""
    m = MINDSETS[mindset]
    rows, tasks = [], []
    tot = {"sales": 0, "food_cost": 0, "waste": 0, "cash": 0}
    for it in items:
        mu = it["mu"] * (1 + m["lift"])
        q = max(0, round(mu + m["z"] * it["sd"]))
        sold, left, p_out = outcome(mu, it["sd"], q)
        need_units = q * it["per_dish"]
        buy = max(0, need_units - on_hand.get(it["material"], 0))
        cash = buy * it["unit_cost"]
        waste = left * it["per_dish"] * it["unit_cost"] * it["spoil"]
        tot["sales"] += sold * it["price"]
        tot["food_cost"] += sold * it["per_dish"] * it["unit_cost"]
        tot["waste"] += waste
        tot["cash"] += cash
        rows.append({"dish": it["dish"], "plan": q, "sold": sold, "left": left, "p_out": p_out, "buy": buy,
                     "material": it["material"], "unit": it.get("unit", "")})
        if buy:
            tasks.append(("manager", f"Order {buy:,.0f} {it.get('unit', '')} {it['material']} for {q} {it['dish']}"))
        tasks.append(("chef", f"Prep for {q} {it['dish']}" + (" ; 86 it when out, no rush orders" if mindset == "lean" else "")))
    if mindset == "growth":
        tasks.append(("manager", "Run the push: feature the special at order and offer the catering package to past clients who opted in"))
    if mindset == "lean":
        tasks.append(("manager", "Line up a same-day backup source for the top two items in case they run out early"))
    tot["margin"] = tot["sales"] - tot["food_cost"] - tot["waste"]
    return {"mindset": mindset, "label": m["label"], "rows": rows, "tasks": tasks, "totals": tot}


def compare(items, on_hand):
    return [plan(items, on_hand, k) for k in MINDSETS]


def render(plans):
    def usd(c):
        return f"${c / 100:,.0f}"
    L = ["   " + f"{'':18}" + "".join(f"{p['label']:>16}" for p in plans)]
    for key, name in (("sales", "expected sales"), ("food_cost", "main ingredient"), ("waste", "expected waste"),
                      ("margin", "sales - food - waste"), ("cash", "cash to order")):
        L.append("   " + f"{name:18}" + "".join(f"{usd(p['totals'][key]):>16}" for p in plans))
    worst = [max(p["rows"], key=lambda r: r["p_out"]) for p in plans]
    L.append("   " + f"{'worst run-out':18}" + "".join(f"{w['dish'][:9] + ' ' + str(round(100 * w['p_out'])) + '%':>16}" for w in worst))
    L.append("   (main ingredient cost only; growth sales assume the push brings 15% more guests)")
    for p in plans:
        L.append(f"   {p['label']}: " + "; ".join(f"{r['dish']} {r['plan']} (runs out {100 * r['p_out']:.0f}%)" for r in p["rows"]))
    return "\n".join(L)

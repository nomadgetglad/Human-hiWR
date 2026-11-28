"""Run-out risk for the fryer plan, using PyTorch when it is installed (on the GPU if there is one).

The plan says "fry N batches" from the 4-week average. This adds how likely the next hour is to run out with
those N batches, and the fewest batches that keep that chance at or under 5%. It replays many nights at once
by redrawing the past weeks with +/-15% noise. Optional: without torch, risk() returns None and the plan is
unchanged. It only reads; nothing is made or ordered.
"""
from datetime import date, timedelta

try:
    import torch
except ImportError:          # the app and tests run fine without it
    torch = None

TARGET = 0.05


def device():
    return "cuda" if torch is not None and torch.cuda.is_available() else "cpu"


def past_hours(db, item, day, hour, weeks=4):
    """How much of the item each of the last `weeks` same weekdays used in this hour."""
    out = []
    for w in range(1, weeks + 1):
        d = (date.fromisoformat(day) - timedelta(weeks=w)).isoformat()
        out.append(sum(u["qty"] * r[0] for u in db.execute("SELECT dish, qty FROM prep_usage WHERE item=?", (item,))
                       for r in db.execute("SELECT COALESCE(SUM(qty),0) FROM dish_sales WHERE day=? AND hour=? AND dish=?",
                                           (d, hour, u["dish"]))))
    return out


def runout_chances(history, expected, on_hand, batch_qty, max_batches=12, nights=20000, seed=0):
    """Chance of running out for 0..max_batches batches. history: past amounts; expected: the plan's next hour."""
    if torch is None or not history or not expected or sum(history) <= 0:
        return None
    g = torch.Generator(device=device()).manual_seed(seed)
    past = torch.tensor(history, dtype=torch.float32, device=device())
    pick = torch.randint(len(history), (nights,), generator=g, device=device())
    noise = 0.85 + 0.30 * torch.rand(nights, generator=g, device=device())
    demand = past[pick] / past.mean() * expected * noise                     # nights spread around the plan
    supply = on_hand + batch_qty * torch.arange(max_batches + 1, device=device())
    return (demand[:, None] > supply[None, :]).float().mean(0).tolist()


def risk(db, item, now, plan):
    """{'risk_pct', 'safe_batches', 'device'} for the prep plan, or None without torch or history."""
    p = db.execute("SELECT batch_qty FROM prep_items WHERE item=?", (item,)).fetchone()
    hour = int(now[11:13])
    chances = runout_chances(past_hours(db, item, now[:10], hour), plan["next_hour"], plan["good_on_hand"], p["batch_qty"])
    if chances is None:
        return None
    n = min(plan["fry_batches_now"], len(chances) - 1)
    safe = next((b for b, c in enumerate(chances) if c <= TARGET), None)
    return {"risk_pct": round(chances[n] * 100), "safe_batches": safe, "device": device()}

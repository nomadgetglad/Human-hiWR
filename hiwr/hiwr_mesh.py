"""Mesh: the staff organizer, built from the history between employees and sales.

Every order line moves through stages (received, fired, cooking, ready, served) and each stage records who did
it (order_stage_events, append-only). Mesh reads that history and refreshes, for each item, who has made it, how
fast, and how often it had to be remade; and for each person, the items they actually make. From that it suggests
who fits each item best. It only reads: nothing here changes a schedule or assigns anyone.
"""
from datetime import datetime

MIN_MADE = 3          # fewer than this and there isn't enough history to rank someone on an item


def _dt(s):
    return datetime.fromisoformat(s.replace(" ", "T"))


def history(db, since=None):
    """One row per order line someone cooked: who, what, minutes from fired to ready, remade or not."""
    rows = []
    q = ("SELECT oi.id AS item_id, oi.item, oi.qty, oi.order_id FROM order_items oi "
         "JOIN order_stage_events e ON e.item_id = oi.id AND e.stage = 'cooking'")
    seen = set()
    for r in db.execute(q):
        if r["item_id"] in seen:
            continue
        seen.add(r["item_id"])
        ev = {}
        for e in db.execute("SELECT stage, at, actor FROM order_stage_events WHERE item_id=? ORDER BY at, id", (r["item_id"],)):
            ev.setdefault(e["stage"], e)
        cook = ev["cooking"]["actor"]
        if since and ev["cooking"]["at"][:10] < since:
            continue
        minutes = None
        if "fired" in ev and "ready" in ev:
            minutes = (_dt(ev["ready"]["at"]) - _dt(ev["fired"]["at"])).total_seconds() / 60
        rows.append({"item": r["item"], "qty": r["qty"], "member": cook, "minutes": minutes,
                     "remade": "remade" in ev, "day": ev["cooking"]["at"][:10]})
    return rows


def _stats(rows):
    made = sum(r["qty"] for r in rows)
    times = [r["minutes"] for r in rows if r["minutes"] is not None]
    remakes = sum(1 for r in rows if r["remade"])
    return {"made": made, "avg_min": round(sum(times) / len(times), 1) if times else None,
            "remake_pct": round(100 * remakes / len(rows), 1) if rows else 0.0, "last": max(r["day"] for r in rows)}


def mesh(db, member=None, since=None):
    """by_item: for each item, everyone who made it, best fit first. by_member: each person's items, most made
    first. best: the suggested person per item. With member=..., only that person's own history comes back."""
    rows = history(db, since)
    if member:
        rows = [r for r in rows if r["member"] == member]
    pairs = {}
    for r in rows:
        pairs.setdefault((r["item"], r["member"]), []).append(r)
    by_item, by_member = {}, {}
    for (item, who), rs in pairs.items():
        s = {"item": item, "member": who, **_stats(rs)}
        by_item.setdefault(item, []).append(s)
        by_member.setdefault(who, []).append(s)
    # Best fit: enough history first, then fewest remakes, then fastest, then most made.
    rank = lambda s: (s["made"] < MIN_MADE, s["remake_pct"], s["avg_min"] if s["avg_min"] is not None else 1e9, -s["made"])
    for v in by_item.values():
        v.sort(key=rank)
    for v in by_member.values():
        v.sort(key=lambda s: (-s["made"], s["item"]))
    best = {item: v[0] for item, v in by_item.items() if v and v[0]["made"] >= MIN_MADE}
    return {"by_item": dict(sorted(by_item.items())), "by_member": dict(sorted(by_member.items())), "best": best,
            "lines": len(rows)}

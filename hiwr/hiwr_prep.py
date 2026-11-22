"""Prep level: the station worker's own screen (fryer, grill, welder ...).

Expected demand for the rest of the shift = the same weekday's average by hour over the last 4 weeks,
scaled by today's reservations against the usual, then corrected by how today is actually running
(sold so far vs forecast so far, weighted more as the night goes on).
The fryer sees: sold so far, expected for the next hold window, what's already made and still good,
and how many batches to fry now and when the next one is due. Each batch draws its raw input from
Stew inventory. A production station sees its quota, done so far, and the pace it needs.
"""
import math
from datetime import datetime, timedelta

def _dt(s): return datetime.fromisoformat(s)

DINNER_FROM = 17        # reservations scale the forecast from this hour on
FRY_AHEAD_MIN = 60      # fry enough for the next hour; fresher than frying the whole window at once


def hourly_forecast(db, dish, day, weeks=4):
    d = _dt(day + "T00:00")
    past = [(d - timedelta(weeks=w)).date().isoformat() for w in range(1, weeks + 1)]
    out = {}
    for h in range(10, 24):
        rows = [db.execute("SELECT COALESCE(SUM(qty),0) FROM dish_sales WHERE day=? AND hour=? AND dish=?", (p, h, dish)).fetchone()[0] for p in past]
        out[h] = sum(rows) / weeks
    return out


def item_forecast(db, item, day, scale):
    total = {h: 0.0 for h in range(10, 24)}
    for u in db.execute("SELECT * FROM prep_usage WHERE item=?", (item,)):
        for h, q in hourly_forecast(db, u["dish"], day).items():
            total[h] += q * u["qty"] * (scale if h >= DINNER_FROM else 1.0)
    return total


def sold_so_far(db, item, day, now_hour):
    return sum(r["qty"] * u["qty"] for u in db.execute("SELECT * FROM prep_usage WHERE item=?", (item,))
               for r in db.execute("SELECT qty FROM dish_sales WHERE day=? AND dish=? AND hour<?", (day, u["dish"], now_hour)))


def good_on_hand(db, item, now):
    """Made minus used, counting only batches still inside the hold window (FIFO)."""
    p = db.execute("SELECT * FROM prep_items WHERE item=?", (item,)).fetchone()
    day = now[:10]
    used = sold_so_far(db, item, day, _dt(now).hour)
    good, waste = 0.0, 0.0
    for b in db.execute("SELECT * FROM prep_batches WHERE item=? AND substr(made_at,1,10)=? ORDER BY made_at", (item, day)):
        take = min(b["qty"], used); used -= take
        left = b["qty"] - take
        if (_dt(now) - _dt(b["made_at"])).total_seconds() / 60 > p["hold_minutes"]:
            waste += left
        else:
            good += left
    return good, waste


def prep_plan(db, item, now, reservations_today, usual_reservations):
    p = db.execute("SELECT * FROM prep_items WHERE item=?", (item,)).fetchone()
    day, h = now[:10], _dt(now).hour
    scale = reservations_today / usual_reservations if usual_reservations else 1.0
    fc = item_forecast(db, item, day, scale)
    sold = sold_so_far(db, item, day, h)
    expected_so_far = sum(q for hh, q in fc.items() if hh < h)
    # how today is running vs forecast; trust it more later in the day
    weight = min(0.7, (h - 10) / 12)
    pace = (sold / expected_so_far) if expected_so_far else 1.0
    adj = (1 - weight) + weight * pace
    window_h = p["hold_minutes"] / 60
    upcoming = sum(q for hh, q in fc.items() if h <= hh < h + window_h) * adj
    rest = sum(q for hh, q in fc.items() if hh >= h) * adj
    good, waste = good_on_hand(db, item, now)
    next_hour = sum(q for hh, q in fc.items() if hh == h) * adj
    need = next_hour * (FRY_AHEAD_MIN / 60) * (1 + p["buffer_pct"]) - good
    batches = max(0, math.ceil(need / p["batch_qty"]))
    per_min = next_hour / 60
    minutes_cover = (good + batches * p["batch_qty"]) / per_min if per_min else None
    next_at = (_dt(now) + timedelta(minutes=minutes_cover - p["batch_minutes"])).strftime("%I:%M %p").lstrip("0") if minutes_cover else "-"
    return {"item": item, "unit": p["unit"], "sold": round(sold, 1), "expected_so_far": round(expected_so_far, 1),
            "running": f"{round(pace * 100)}% of forecast", "next_window": round(upcoming, 1), "rest_of_night": round(rest, 1),
            "good_on_hand": round(good, 1), "waste": round(waste, 1), "fry_batches_now": batches, "next_hour": round(next_hour, 1),
            "batch": f"{p['batch_qty']:g} {p['unit']}, {p['batch_minutes']} min", "next_batch_at": next_at,
            "input": f"{batches * p['input_qty']:g} {p['input_material']}" if batches else "-"}


def make_batches(db, item, n, at, member):
    p = db.execute("SELECT * FROM prep_items WHERE item=?", (item,)).fetchone()
    for i in range(n):
        db.execute("INSERT INTO prep_batches(item, qty, made_at, member) VALUES (?,?,?,?)", (item, p["batch_qty"], at, member))
    if p["input_material"]:
        q = n * p["input_qty"]
        db.execute("UPDATE materials SET on_hand=on_hand-? WHERE material=?", (q, p["input_material"]))
        db.execute("INSERT INTO material_moves(material, qty, reason, ref, at) VALUES (?,?,?,?,?)",
                   (p["input_material"], -q, f"fried {n} batch {item}", None, at))


def station_pace(quota, done, shift_start, shift_hours, now):
    """Production station: done vs quota, pace so far vs pace needed."""
    elapsed = (_dt(now) - _dt(shift_start)).total_seconds() / 3600
    left_h = shift_hours - elapsed
    pace = done / elapsed if elapsed else 0
    need = (quota - done) / left_h if left_h > 0 else float("inf")
    proj = round(done + pace * left_h)
    return {"quota": quota, "done": done, "pace_per_hour": round(pace, 2), "needed_per_hour": round(need, 2),
            "projected": proj, "status": "on track" if proj >= quota else f"behind: projected {proj} of {quota}"}

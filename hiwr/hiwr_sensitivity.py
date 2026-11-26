"""Sensitivity to falling numbers, 1-10, set by the account holder.

One dial, two effects:
- Alerts: how big a drop has to be before Human flags it. 1 = only big drops (about 40% below typical),
  10 = small dips (about 5% below typical).
- The assistant: how much it may estimate. 1-3 = computed data only (if data is missing, say so and ask);
  4-7 = may fill gaps from averages; 8-10 = may project from averages and trends. Every estimate is labeled.
Settings are versioned, append-only. Standard library only.
"""
import sqlite3
from collections import defaultdict
from datetime import date

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY, key TEXT NOT NULL, value TEXT NOT NULL, by TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TRIGGER IF NOT EXISTS app_settings_no_update BEFORE UPDATE ON app_settings BEGIN SELECT RAISE(ABORT, 'app_settings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS app_settings_no_delete BEFORE DELETE ON app_settings BEGIN SELECT RAISE(ABORT, 'app_settings is append-only'); END;
"""
DEFAULT = 5
KEY = "drop_sensitivity"


def ensure(db):
    db.executescript(SCHEMA)


def get(db):
    try:
        r = db.execute("SELECT value FROM app_settings WHERE key=? ORDER BY id DESC LIMIT 1", (KEY,)).fetchone()
    except sqlite3.OperationalError:
        return DEFAULT
    return int(r["value"]) if r else DEFAULT


def set_level(db, value, by):
    v = int(value)
    if not 1 <= v <= 10:
        raise ValueError("Sensitivity is 1 to 10")
    db.execute("INSERT INTO app_settings(key, value, by) VALUES (?,?,?)", (KEY, str(v), by))
    return v


# When the falling-numbers check refreshes. Default: after close on every open business day.
REFRESH = {"close": "End of every open business day", "shift": "At each shift change", "live": "Live, whenever you look"}
REFRESH_KEY = "drop_refresh"
CLOSE_HOUR = 22
SHIFT_HOURS = [11, 15, 17, 22]
CLOSED_DAYS = {6}                       # Sunday closed (sample); holidays below are closed too
HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01"}


def get_refresh(db):
    try:
        r = db.execute("SELECT value FROM app_settings WHERE key=? ORDER BY id DESC LIMIT 1", (REFRESH_KEY,)).fetchone()
    except sqlite3.OperationalError:
        return "close"
    return r["value"] if r and r["value"] in REFRESH else "close"


def set_refresh(db, mode, by):
    if mode not in REFRESH:
        raise ValueError("Pick one of: " + ", ".join(REFRESH))
    db.execute("INSERT INTO app_settings(key, value, by) VALUES (?,?,?)", (REFRESH_KEY, mode, by))
    return mode


def is_open_day(day_iso):
    return date.fromisoformat(day_iso).weekday() not in CLOSED_DAYS and day_iso not in HOLIDAYS


def last_refresh_point(mode, day_iso, hour):
    """The most recent scheduled check at or before (day, hour), as (day, hour); None for live."""
    if mode == "live":
        return None
    points = [CLOSE_HOUR] if mode == "close" else SHIFT_HOURS
    d = date.fromisoformat(day_iso)
    for back in range(0, 10):
        di = date.fromordinal(d.toordinal() - back).isoformat()
        if not is_open_day(di):
            continue
        ok = [p for p in points if back > 0 or p <= hour]
        if ok:
            return di, max(ok)
    return None


def snapshot(db, level, mode, day_iso, hour, by="schedule"):
    """The falling-numbers result as of the last scheduled check; computed once per check and kept."""
    import json
    pt = last_refresh_point(mode, day_iso, hour)
    if pt is None:
        return {"at": f"{day_iso} {hour:02d}:00", "drops": drops(db, day_iso, hour, level), "scheduled": False}
    stamp = f"{pt[0]} {pt[1]:02d}:00 L{level}"
    try:
        r = db.execute("SELECT value FROM app_settings WHERE key='drop_snapshot' ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        r = None
    if r:
        snap = json.loads(r["value"])
        if snap.get("stamp") == stamp:
            return snap
    snap = {"stamp": stamp, "at": f"{pt[0]} {pt[1]:02d}:00", "drops": drops(db, pt[0], pt[1], level), "scheduled": True}
    db.execute("INSERT INTO app_settings(key, value, by) VALUES ('drop_snapshot', ?, ?)", (json.dumps(snap), by))
    db.commit()
    return snap


def threshold_pct(level):
    """How far below typical a number must fall to alert: 41% at 1 down to 5% at 10."""
    return 45 - 4 * int(level)


def ai_rule(level):
    level = int(level)
    if level <= 3:
        return ("computed data only", "Use only numbers you can compute from the records. If something is missing, say what is "
                "missing and ask; do not estimate.")
    if level <= 7:
        return ("fill gaps from averages", "Use computed numbers first. Where data is missing you may estimate from recent "
                "averages; label every estimate as 'estimated' and say what it is based on.")
    return ("project from trends", "You may project from averages and trends to answer ahead of the data; label every "
            "projection as 'estimated' and say what it is based on.")


def drops(db, today, upto_hour, level, weeks=4):
    """Dishes selling below their typical pace so far today (same hours, same weekday, last few weeks)."""
    th = threshold_pct(level)
    wd = date.fromisoformat(today).weekday()
    try:
        rows = db.execute("SELECT day, dish, SUM(qty) AS q FROM dish_sales WHERE hour < ? GROUP BY day, dish", (upto_hour,)).fetchall()
    except sqlite3.OperationalError:
        return []
    past, now = defaultdict(list), {}
    for r in rows:
        if r["day"] == today:
            now[r["dish"]] = r["q"]
        elif date.fromisoformat(r["day"]).weekday() == wd:
            past[r["dish"]].append(r["q"])
    out = []
    for dish, qs in past.items():
        typical = sum(qs[-weeks:]) / len(qs[-weeks:])
        if typical < 3:
            continue
        got = now.get(dish, 0)
        pct = round(100 * (typical - got) / typical)
        if pct >= th:
            out.append({"what": dish, "typical": round(typical, 1), "now": got, "down_pct": pct})
    return sorted(out, key=lambda x: -x["down_pct"])

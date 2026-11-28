"""Portfolio: one business account running several lines at once, each measured in its own unit.

- Assembly (pipes made): runs with units, minutes, material lots used, revenue and cost.
- Repair and service (clients fulfilled): technician jobs billed as hours x rate, with a minimum and a trip fee.
- Café (table turns): seatings per table per hour.

"RPM" is each line's throughput in its own unit per hour (pipes/hour, clients/day, turns/table/hour). Each line
also has a "next up": the next client or order due on the schedule. Lots follow material from receiving through
production to sale. Everything here is sample data for the demo. Standard library only.
"""
import json
import random
from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS pf_lines (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, unit TEXT NOT NULL, per TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pf_runs (              -- append-only: work done
    id INTEGER PRIMARY KEY, line TEXT NOT NULL, started_at TEXT NOT NULL, minutes REAL NOT NULL,
    units REAL NOT NULL, revenue_cents INTEGER NOT NULL, material_cents INTEGER NOT NULL, labor_cents INTEGER NOT NULL,
    client TEXT, worker TEXT, lot TEXT
);
CREATE TABLE IF NOT EXISTS pf_queue (             -- what's next on the schedule, per line
    id INTEGER PRIMARY KEY, line TEXT NOT NULL, client TEXT NOT NULL, what TEXT NOT NULL, due_at TEXT NOT NULL,
    units REAL NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS pf_lots (
    lot TEXT PRIMARY KEY, material TEXT NOT NULL, vendor TEXT NOT NULL, received_on TEXT NOT NULL,
    qty REAL NOT NULL, unit TEXT NOT NULL, cost_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pf_lot_moves (         -- append-only: received, used in production, sold
    id INTEGER PRIMARY KEY, lot TEXT NOT NULL, at TEXT NOT NULL, kind TEXT NOT NULL, qty REAL NOT NULL, ref TEXT
);
CREATE TRIGGER IF NOT EXISTS pf_runs_no_update BEFORE UPDATE ON pf_runs BEGIN SELECT RAISE(ABORT, 'pf_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS pf_runs_no_delete BEFORE DELETE ON pf_runs BEGIN SELECT RAISE(ABORT, 'pf_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS pf_lot_moves_no_update BEFORE UPDATE ON pf_lot_moves BEGIN SELECT RAISE(ABORT, 'pf_lot_moves is append-only'); END;
CREATE TRIGGER IF NOT EXISTS pf_lot_moves_no_delete BEFORE DELETE ON pf_lot_moves BEGIN SELECT RAISE(ABORT, 'pf_lot_moves is append-only'); END;
"""

LINES = [
    ("assembly", "Pipe assembly", "assembly", "pipes", "hour"),
    ("service", "Repair and service", "service", "clients", "day"),
    ("cafe", "Café tables", "tables", "table turns", "table-hour"),
]
CAFE_TABLES = 12
# Technician fee rule (sample): billed hours x rate, at least MIN_HOURS, plus a trip fee per visit
TECH_RATE_CENTS, MIN_HOURS, TRIP_FEE_CENTS, TECH_PAY_CENTS = 9500, 1.0, 4500, 3200


def ensure(db):
    db.executescript(SCHEMA)


def tech_fee(hours, rate_cents=TECH_RATE_CENTS, min_hours=MIN_HOURS, trip_cents=TRIP_FEE_CENTS):
    """What a service visit is billed: max(hours, minimum) x rate + trip fee. Returns the breakdown in cents."""
    billed = max(float(hours), min_hours)
    labor = round(billed * rate_cents)
    return {"hours": round(float(hours), 2), "billed_hours": round(billed, 2), "rate_cents": rate_cents,
            "labor_cents": labor, "trip_cents": trip_cents, "total_cents": labor + trip_cents}


def seed(db, today):
    """Five days of sample work on three lines, plus lots and what's next on the schedule."""
    ensure(db)
    rng = random.Random(23)
    db.executemany("INSERT INTO pf_lines VALUES (?,?,?,?,?)", LINES)
    d0 = date.fromisoformat(today)
    # Lots: steel tube and fittings for assembly; coffee beans for the café
    lots = [("L-2611-01", "steel tube 2in", "Midwest Steel", (d0 - timedelta(days=9)).isoformat(), 600, "ft", 180),
            ("L-2611-02", "steel tube 2in", "Midwest Steel", (d0 - timedelta(days=3)).isoformat(), 600, "ft", 195),
            ("L-2611-07", "fittings kit", "Coastal Supply", (d0 - timedelta(days=8)).isoformat(), 300, "kit", 420),
            ("L-2611-11", "coffee beans", "Port Roasters", (d0 - timedelta(days=6)).isoformat(), 40, "lb", 1100)]
    for lot, mat, vendor, on, qty, unit, cents in lots:
        db.execute("INSERT INTO pf_lots VALUES (?,?,?,?,?,?,?)", (lot, mat, vendor, on, qty, unit, cents))
        db.execute("INSERT INTO pf_lot_moves(lot, at, kind, qty, ref) VALUES (?,?, 'received', ?, ?)", (lot, on + "T07:00", qty, f"PO {lot[-2:]}"))
    clients = ["Harbor Marine", "Pine St Bakery", "Cape Fear Brewing", "Wilmington Dental", "Seaside Laundry", "Oak Ave Apartments"]
    for back in range(5, 0, -1):
        day = (d0 - timedelta(days=back)).isoformat()
        tube_lot = "L-2611-01" if back > 2 else "L-2611-02"
        tube_cost = 180 if back > 2 else 195
        # Assembly: 3 runs a day of 6-10 pipes; each pipe uses 8 ft of tube and 1 fittings kit
        for h in (7, 10, 13):
            n = rng.randint(6, 10)
            mins = round(n * rng.uniform(5.5, 7.5), 1)
            material = n * (8 * tube_cost + 420)
            labor = round(mins / 60 * 2400)
            client = rng.choice(clients)
            db.execute("INSERT INTO pf_runs(line, started_at, minutes, units, revenue_cents, material_cents, labor_cents, client, worker, lot) "
                       "VALUES ('assembly',?,?,?,?,?,?,?,?,?)",
                       (f"{day}T{h:02d}:00", mins, n, n * 7900, material, labor, client, "assembly_ana", tube_lot))
            db.execute("INSERT INTO pf_lot_moves(lot, at, kind, qty, ref) VALUES (?,?, 'used', ?, ?)", (tube_lot, f"{day}T{h:02d}:30", -8 * n, f"{n} pipes"))
            db.execute("INSERT INTO pf_lot_moves(lot, at, kind, qty, ref) VALUES ('L-2611-07',?, 'used', ?, ?)", (f"{day}T{h:02d}:30", -n, f"{n} pipes"))
            db.execute("INSERT INTO pf_lot_moves(lot, at, kind, qty, ref) VALUES (?,?, 'sold', ?, ?)", (tube_lot, f"{day}T{h + 2:02d}:00", 0, f"to {client}"))
        # Service: 2-4 visits a day
        for k in range(rng.randint(2, 4)):
            hours = round(rng.uniform(0.5, 3.0), 2)
            fee = tech_fee(hours)
            db.execute("INSERT INTO pf_runs(line, started_at, minutes, units, revenue_cents, material_cents, labor_cents, client, worker) "
                       "VALUES ('service',?,?,1,?,?,?,?, 'tech_luis')",
                       (f"{day}T{8 + 2 * k:02d}:00", hours * 60, fee["total_cents"], rng.choice([0, 1800, 3500]),
                        round(hours * TECH_PAY_CENTS) + 1200, rng.choice(clients)))
        # Café: lunch and dinner seatings, 12 tables
        for h, turns in ((12, rng.randint(14, 20)), (13, rng.randint(10, 16)), (18, rng.randint(12, 18)), (19, rng.randint(14, 22))):
            db.execute("INSERT INTO pf_runs(line, started_at, minutes, units, revenue_cents, material_cents, labor_cents, worker, lot) "
                       "VALUES ('cafe',?,60,?,?,?,?, 'server_kim', 'L-2611-11')",
                       (f"{day}T{h:02d}:00", turns, turns * 2600, turns * 780, 2 * 1600))
            db.execute("INSERT INTO pf_lot_moves(lot, at, kind, qty, ref) VALUES ('L-2611-11',?, 'used', ?, ?)", (f"{day}T{h:02d}:30", -0.05 * turns, f"{turns} seatings"))
    # Next up on the schedule
    t = today
    db.executemany("INSERT INTO pf_queue(line, client, what, due_at, units) VALUES (?,?,?,?,?)", [
        ("assembly", "Harbor Marine", "24 pipes, 2in, threaded", f"{t}T15:00", 24),
        ("assembly", "Cape Fear Brewing", "12 pipes, 2in, flanged", f"{(d0 + timedelta(days=1)).isoformat()}T10:00", 12),
        ("service", "Wilmington Dental", "Compressor repair", f"{t}T13:30", 1),
        ("service", "Oak Ave Apartments", "Boiler check", f"{t}T16:00", 1),
        ("cafe", "Party of 6 (reservation)", "Table 4", f"{t}T18:30", 1),
    ])


def _hours(rows):
    return sum(r["minutes"] for r in rows) / 60


def lines(db, now):
    """Per line: RPM in its own unit, margin, and what's next on the schedule."""
    out = []
    for ln in db.execute("SELECT * FROM pf_lines ORDER BY rowid"):
        rows = db.execute("SELECT * FROM pf_runs WHERE line=?", (ln["id"],)).fetchall()
        units = sum(r["units"] for r in rows)
        rev = sum(r["revenue_cents"] for r in rows)
        cost = sum(r["material_cents"] + r["labor_cents"] for r in rows)
        days = len({r["started_at"][:10] for r in rows}) or 1
        if ln["per"] == "hour":
            rpm = units / _hours(rows) if rows else 0
        elif ln["per"] == "day":
            rpm = units / days
        else:                                   # table-hour: turns per table per open hour
            rpm = units / (len(rows) * CAFE_TABLES) if rows else 0
        nxt = db.execute("SELECT * FROM pf_queue WHERE line=? AND due_at>=? ORDER BY due_at LIMIT 1", (ln["id"], now)).fetchone()
        item = {"id": ln["id"], "name": ln["name"], "unit": ln["unit"], "per": ln["per"], "rpm": round(rpm, 2),
                "units": units, "revenue_cents": rev, "cost_cents": cost,
                "margin_pct": round(100 * (rev - cost) / rev, 1) if rev else 0.0, "days": days,
                "next": dict(nxt) if nxt else None}
        if ln["kind"] == "assembly" and rows:
            item["minutes_per_unit"] = round(sum(r["minutes"] for r in rows) / units, 1)
        if ln["kind"] == "service" and rows:
            item["avg_ticket_cents"] = round(rev / len(rows))
            item["fee_rule"] = {"rate_cents": TECH_RATE_CENTS, "min_hours": MIN_HOURS, "trip_cents": TRIP_FEE_CENTS}
        out.append(item)
    return out


def portfolio(db, now):
    ls = lines(db, now)
    rev = sum(x["revenue_cents"] for x in ls)
    cost = sum(x["cost_cents"] for x in ls)
    upcoming = [dict(r) for r in db.execute("SELECT * FROM pf_queue WHERE due_at>=? ORDER BY due_at", (now,))]
    return {"lines": ls, "revenue_cents": rev, "cost_cents": cost,
            "margin_pct": round(100 * (rev - cost) / rev, 1) if rev else 0.0,
            "mix": [{"id": x["id"], "share_pct": round(100 * x["revenue_cents"] / rev, 1) if rev else 0} for x in ls],
            "next_up": upcoming[0] if upcoming else None, "upcoming": upcoming}


def lots(db):
    out = []
    for l in db.execute("SELECT * FROM pf_lots ORDER BY received_on"):
        moves = [dict(m) for m in db.execute("SELECT at, kind, qty, ref FROM pf_lot_moves WHERE lot=? ORDER BY at, id", (l["lot"],))]
        left = sum(m["qty"] for m in moves)
        out.append({**dict(l), "left": round(left, 2), "moves": moves})
    return out



# ---------------- Appointments with their own bookkeeping, and late fees ----------------
# Open slots are booked into appointments. Each appointment carries an append-only ledger: invoice, deposit,
# payments, late fee. A late fee is never charged on its own: one business day before it would apply, staff are
# alerted and a reminder to the client is drafted (a person sends it). After the due date the fee is proposed as
# PENDING and a person confirms it; waiving a fee already charged needs two certified approvers.

APPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS pf_appointments (
    id INTEGER PRIMARY KEY, line TEXT NOT NULL, client TEXT NOT NULL, starts_at TEXT NOT NULL,
    what TEXT NOT NULL, price_cents INTEGER NOT NULL, due_on TEXT NOT NULL, booked_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pf_ledger (            -- append-only bookkeeping per appointment
    id INTEGER PRIMARY KEY, appt_id INTEGER NOT NULL REFERENCES pf_appointments(id), at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('invoice', 'deposit', 'payment', 'late_fee', 'late_fee_waived')),
    amount_cents INTEGER NOT NULL, by TEXT NOT NULL, note TEXT
);
CREATE TRIGGER IF NOT EXISTS pf_ledger_no_update BEFORE UPDATE ON pf_ledger BEGIN SELECT RAISE(ABORT, 'pf_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS pf_ledger_no_delete BEFORE DELETE ON pf_ledger BEGIN SELECT RAISE(ABORT, 'pf_ledger is append-only'); END;
"""

LATE_FEE_FLAT_CENTS, LATE_FEE_PCT = 2500, 0.05      # the greater of $25 or 5% of what's overdue
HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01"}  # sample: no alerts or due dates fall on these
OPEN_HOURS = {"service": [9, 11, 13, 15], "assembly": [8, 13]}


def ensure_appointments(db):
    db.executescript(APPT_SCHEMA)


def is_business_day(d):
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def business_day_before(day_iso):
    d = date.fromisoformat(day_iso) - timedelta(days=1)
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d.isoformat()


def late_fee(overdue_cents):
    return max(LATE_FEE_FLAT_CENTS, round(overdue_cents * LATE_FEE_PCT)) if overdue_cents > 0 else 0


def balance(db, appt_id):
    tot = 0
    for r in db.execute("SELECT kind, amount_cents FROM pf_ledger WHERE appt_id=?", (appt_id,)):
        tot += r["amount_cents"] if r["kind"] in ("invoice", "late_fee") else -r["amount_cents"]
    return tot


def _fee_charged(db, appt_id):
    return db.execute("SELECT 1 FROM pf_ledger WHERE appt_id=? AND kind='late_fee'", (appt_id,)).fetchone() is not None


def open_slots(db, line, start_iso, days=5):
    taken = {r["starts_at"] for r in db.execute("SELECT starts_at FROM pf_appointments WHERE line=?", (line,))}
    out, d = [], date.fromisoformat(start_iso)
    while len({s[:10] for s in out}) < days:
        if is_business_day(d):
            for h in OPEN_HOURS.get(line, []):
                s = f"{d.isoformat()}T{h:02d}:00"
                if s not in taken:
                    out.append(s)
        d += timedelta(days=1)
    return out


def book(db, line, client, starts_at, what, price_cents, deposit_cents, booked_by, due_days=7):
    if db.execute("SELECT 1 FROM pf_appointments WHERE line=? AND starts_at=?", (line, starts_at)).fetchone():
        raise ValueError("That slot was just taken")
    due = date.fromisoformat(starts_at[:10]) + timedelta(days=due_days)
    while not is_business_day(due):
        due += timedelta(days=1)
    aid = db.execute("INSERT INTO pf_appointments(line, client, starts_at, what, price_cents, due_on, booked_by) VALUES (?,?,?,?,?,?,?)",
                     (line, client, starts_at, what, price_cents, due.isoformat(), booked_by)).lastrowid
    db.execute("INSERT INTO pf_ledger(appt_id, at, kind, amount_cents, by, note) VALUES (?,?, 'invoice', ?, ?, ?)",
               (aid, starts_at, price_cents, booked_by, what))
    if deposit_cents:
        db.execute("INSERT INTO pf_ledger(appt_id, at, kind, amount_cents, by, note) VALUES (?,?, 'deposit', ?, ?, 'deposit at booking')",
                   (aid, starts_at, deposit_cents, booked_by))
    return aid


def appointments(db, today):
    """Every appointment with its balance and where it stands on late fees, as of today."""
    out = []
    for a in db.execute("SELECT * FROM pf_appointments ORDER BY due_on, starts_at"):
        bal = balance(db, a["id"])
        alert_on = business_day_before(a["due_on"])
        if bal <= 0:
            state = "paid"
        elif _fee_charged(db, a["id"]):
            state = "late_fee_charged"
        elif today > a["due_on"]:
            state = "late_fee_pending"           # proposed; a person confirms it
        elif today >= alert_on:
            state = "alert"                      # one business day to go: remind now, avoid the fee
        else:
            state = "open"
        ledger = [dict(r) for r in db.execute("SELECT at, kind, amount_cents, by, note FROM pf_ledger WHERE appt_id=? ORDER BY id", (a["id"],))]
        out.append({**dict(a), "balance_cents": bal, "alert_on": alert_on, "state": state,
                    "fee_if_late_cents": late_fee(bal) if bal > 0 else 0, "ledger": ledger})
    return out


def reminder_text(a):
    return (f"Hi {a['client']}, a friendly reminder: ${a['balance_cents'] / 100:,.2f} for {a['what']} is due "
            f"{a['due_on']}. Paying by then avoids a ${a['fee_if_late_cents'] / 100:,.2f} late fee. Thank you!")


def charge_late_fee(db, appt_id, by, today):
    a = next((x for x in appointments(db, today) if x["id"] == appt_id), None)
    if not a or a["state"] != "late_fee_pending":
        raise ValueError("No late fee is due on that appointment")
    db.execute("INSERT INTO pf_ledger(appt_id, at, kind, amount_cents, by, note) VALUES (?,?, 'late_fee', ?, ?, ?)",
               (appt_id, today, a["fee_if_late_cents"], by, f"past due {a['due_on']}"))
    return a["fee_if_late_cents"]


def record_payment(db, appt_id, amount_cents, by, at):
    db.execute("INSERT INTO pf_ledger(appt_id, at, kind, amount_cents, by, note) VALUES (?,?, 'payment', ?, ?, 'paid')",
               (appt_id, at, int(amount_cents), by))


def seed_appointments(db, today):
    """Sample appointments: one paid, one due next business day (alert today), one past due (fee pending)."""
    ensure_appointments(db)
    d0 = date.fromisoformat(today)
    a1 = book(db, "service", "Pine St Bakery", f"{(d0 - timedelta(days=12)).isoformat()}T09:00", "Oven igniter repair", 28500, 5000, "manager_jo")
    record_payment(db, a1, 23500, "accounting_sue", f"{(d0 - timedelta(days=6)).isoformat()}T10:00")
    a2 = book(db, "service", "Seaside Laundry", f"{(d0 - timedelta(days=4)).isoformat()}T11:00", "Dryer belt and bearings", 41000, 10000, "manager_jo", due_days=6)
    a3 = book(db, "assembly", "Harbor Marine", f"{(d0 - timedelta(days=14)).isoformat()}T08:00", "36 pipes, 2in, threaded", 284400, 50000, "manager_jo", due_days=10)
    return a1, a2, a3

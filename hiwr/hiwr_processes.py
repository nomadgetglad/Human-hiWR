"""HiWR processes: clinic waitlists, prime-time table moves with upsell math,
and hot / calculated processes that open tickets when they slip.

hot        = time-critical, checked against a running clock (food cooling, a truck at the dock).
calculated = deadlines worked backward from a target date (ship-by for a made-to-order order,
             a production test that must pass before it ships).
Money never moves here: deposits and fees become PENDING money_actions (double approval).
"""
import json
from datetime import datetime, timedelta
from hiwr_contacts import _event, can_text, request_money_action

def _dt(s): return datetime.fromisoformat(s)
def _iso(d): return d.isoformat(timespec="minutes")


def _stage(db, appointment_id):
    r = db.execute("SELECT stage FROM appointment_events WHERE appointment_id=? ORDER BY at DESC, id DESC LIMIT 1",
                   (appointment_id,)).fetchone()
    return r["stage"] if r else None


def _active_appointments(db, where, args):
    rows = db.execute(f"SELECT * FROM appointments WHERE {where}", args).fetchall()
    return [r for r in rows if _stage(db, r["id"]) not in ("cancelled", "no_show")]


# ================= Doctor's office: earliest openings and waitlist =================

def provider_bookings(db, provider):
    return [a for a in _active_appointments(
        db, "id IN (SELECT appointment_id FROM appointment_requirements WHERE assigned=?)", (provider,))]


def earliest_openings(db, provider, from_dt, days=60, slot_hours=0.5, n=3):
    booked = [(_dt(a["starts_at"]), _dt(a["ends_at"])) for a in provider_bookings(db, provider)]
    found, start = [], _dt(from_dt)
    for i in range(days):
        day = (start + timedelta(days=i)).date().isoformat()
        av = db.execute("SELECT * FROM staff_availability WHERE member=? AND day=?", (provider, day)).fetchone()
        if not av:
            continue
        t = _dt(day + "T00:00") + timedelta(hours=av["start_hour"])
        end = _dt(day + "T00:00") + timedelta(hours=av["end_hour"])
        while t + timedelta(hours=slot_hours) <= end:
            s, e = t, t + timedelta(hours=slot_hours)
            if s >= start and not any(s < be and e > bs for bs, be in booked):
                found.append(_iso(s))
                if len(found) == n:
                    return found
            t = e
    return found


def book_with_provider(db, contact_id, provider, starts_at, actor, slot_hours=0.5, note=None):
    end = _iso(_dt(starts_at) + timedelta(hours=slot_hours))
    aid = db.execute("INSERT INTO appointments(contact_id, starts_at, ends_at, kind, workflow, details) "
                     "VALUES (?,?,?,?,?,?)", (contact_id, starts_at, end, "clinic_visit", "clinic_checkin", note)).lastrowid
    db.execute("INSERT INTO appointment_requirements VALUES (?, 'role', 'doctor', 1, ?)", (aid, provider))
    db.execute("INSERT INTO appointment_events(appointment_id, stage, actor) VALUES (?, 'booked', ?)", (aid, actor))
    return aid


def join_waitlist(db, contact_id, provider, appointment_id, wants_before):
    return db.execute("INSERT INTO waitlist(contact_id, provider, appointment_id, wants_before) VALUES (?,?,?,?)",
                      (contact_id, provider, appointment_id, wants_before)).lastrowid


def _wl_state(db, wid):
    r = db.execute("SELECT event, hold_until FROM waitlist_events WHERE waitlist_id=? ORDER BY id DESC LIMIT 1",
                   (wid,)).fetchone()
    return (r["event"], r["hold_until"]) if r else (None, None)


def offer_opening(db, provider, slot, now, hold_hours=2):
    """Offer a freed slot to the first person on the waitlist it helps (FIFO), if we may text them."""
    for w in db.execute("SELECT w.*, p.e164 FROM waitlist w JOIN contact_phones p ON p.contact_id=w.contact_id "
                        "AND p.is_primary=1 WHERE w.provider=? ORDER BY w.joined_at, w.id", (provider,)):
        ev, hold = _wl_state(db, w["id"])
        if ev in ("accepted", "removed") or (ev == "offered" and hold and _dt(hold) > _dt(now)):
            continue
        held = db.execute("SELECT starts_at FROM appointments WHERE id=?", (w["appointment_id"],)).fetchone()
        if not (_dt(slot) < _dt(w["wants_before"]) and _dt(slot) < _dt(held["starts_at"])):
            continue
        channel = "sms" if can_text(db, w["e164"]) else "call"
        hold_until = _iso(_dt(now) + timedelta(hours=hold_hours))
        db.execute("INSERT INTO waitlist_events(waitlist_id, event, slot, at, hold_until) VALUES (?,?,?,?,?)",
                   (w["id"], "offered", slot, now, hold_until))
        return {"waitlist_id": w["id"], "contact_id": w["contact_id"], "channel": channel, "hold_until": hold_until,
                "message": f"An earlier appointment opened: {slot.replace('T', ' at ')}. Reply YES by {hold_until[11:]} to take it."}
    return None


def accept_offer(db, waitlist_id, now, actor):
    w = db.execute("SELECT * FROM waitlist WHERE id=?", (waitlist_id,)).fetchone()
    ev = db.execute("SELECT * FROM waitlist_events WHERE waitlist_id=? ORDER BY id DESC LIMIT 1", (waitlist_id,)).fetchone()
    if ev["event"] != "offered" or _dt(ev["hold_until"]) < _dt(now):
        raise ValueError("offer expired; the slot goes to the next person")
    new_id = book_with_provider(db, w["contact_id"], w["provider"], ev["slot"], actor)
    db.execute("INSERT INTO appointment_events(appointment_id, stage, actor, note) VALUES (?, 'cancelled', ?, ?)",
               (w["appointment_id"], actor, f"moved earlier to #{new_id} from waitlist"))
    db.execute("INSERT INTO waitlist_events(waitlist_id, event, slot, at) VALUES (?, 'accepted', ?, ?)",
               (waitlist_id, ev["slot"], now))
    _event(db, w["contact_id"], actor, "rescheduled", {"from": w["appointment_id"], "to": new_id, "via": "waitlist"})
    return new_id, w["appointment_id"]


# ================= Kitchen: move tables to seat a prime-time party =================

def _capacity(tables):
    """Pushed-together tables lose 2 seats per join."""
    return sum(t["seats"] for t in tables) - 2 * (len(tables) - 1)


def _current_tables(db, appointment_id):
    r = db.execute("SELECT tables FROM table_assignments WHERE appointment_id=? ORDER BY id DESC LIMIT 1",
                   (appointment_id,)).fetchone()
    return r["tables"].split("+") if r else []


def seating_options(db, party_size, starts_at, ends_at, max_options=3):
    tables = {t["name"]: t for t in db.execute("SELECT * FROM dining_tables")}
    overlapping = _active_appointments(db, "kind='reservation' AND starts_at < ? AND ends_at > ?", (ends_at, starts_at))
    occupant = {}
    for a in overlapping:
        for t in _current_tables(db, a["id"]):
            occupant[t] = a

    combos = [[t] for t in tables.values()]
    groups = {}
    for t in tables.values():
        if t["join_group"]:
            groups.setdefault(t["join_group"], []).append(t)
    for g in groups.values():
        g.sort(key=lambda t: t["join_order"])
        for i in range(len(g)):
            for j in range(i + 2, len(g) + 1):
                combos.append(g[i:j])

    def free_spots(blocked):
        spots = [c for c in combos if all(t["name"] not in occupant and t["name"] not in blocked for t in c)]
        return sorted(spots, key=lambda c: (_capacity(c), len(c)))

    options = []
    for combo in combos:
        if _capacity(combo) < party_size:
            continue
        names = {t["name"] for t in combo}
        to_move = {occupant[n]["id"]: occupant[n] for n in names if n in occupant}
        blocked, moves, ok = set(names), [], True
        for a in sorted(to_move.values(), key=lambda a: -a["party_size"]):
            spot = next((c for c in free_spots(blocked) if a["party_size"] <= _capacity(c) <= a["party_size"] + 2), None)
            if not spot:
                ok = False
                break
            blocked |= {t["name"] for t in spot}
            moves.append({"appointment_id": a["id"], "party_size": a["party_size"],
                          "from": "+".join(_current_tables(db, a["id"])), "to": "+".join(t["name"] for t in spot)})
        if ok:
            options.append({"tables": "+".join(t["name"] for t in combo), "seats": _capacity(combo), "moves": moves,
                            "guests_moved": sum(m["party_size"] for m in moves),
                            "empty_seats": _capacity(combo) - party_size})
    options.sort(key=lambda o: (len(o["moves"]), o["guests_moved"], o["empty_seats"]))
    return options[:max_options]


def apply_seating(db, party_appointment_id, option, actor):
    for m in option["moves"]:
        db.execute("INSERT INTO table_assignments(appointment_id, tables, actor, reason) VALUES (?,?,?,?)",
                   (m["appointment_id"], m["to"], actor, f"moved for party #{party_appointment_id}"))
    db.execute("INSERT INTO table_assignments(appointment_id, tables, actor, reason) VALUES (?,?,?,?)",
               (party_appointment_id, option["tables"], actor, "prime-time party seating"))


def upsell_estimate(party_size, package_cents_pp, usual_check_cents_pp, extra_staff_cents=0, deposit_pct=0.25):
    pkg, usual = party_size * package_cents_pp, party_size * usual_check_cents_pp
    return {"package_revenue_cents": pkg, "usual_revenue_cents": usual,
            "extra_staff_cents": extra_staff_cents, "net_uplift_cents": pkg - usual - extra_staff_cents,
            "deposit_cents": round(pkg * deposit_pct)}


# ================= Tickets =================

BASE_PRIORITY = {"DISPUTE": 1, "APPOINTMENT": 2, "STAFFING": 2, "INSPECTION": 2, "SHIPMENT": 3}


def ticket_priority(db, kind, dollars=0, perishable=False, party=None, hot=False):
    """v7 order: disputes, then appointment-affecting, then shipments at risk. Raised for high dollars,
    perishables, hot processes, or a vendor/client that already has open tickets."""
    p = BASE_PRIORITY[kind] * 10
    if dollars >= 500: p -= 3
    if perishable or hot: p -= 3
    if party and db.execute("SELECT COUNT(*) FROM tickets WHERE status!='closed' AND subject LIKE ?",
                            (f"%{party}%",)).fetchone()[0]:
        p -= 2
    return p


def open_ticket(db, ttype, subject, priority, now, actor, financial=None):
    n = db.execute("SELECT COUNT(*) FROM tickets WHERE type=?", (ttype,)).fetchone()[0] + 1
    tid = f"{ {'SHIPMENT': 'SHP', 'DISPUTE': 'DSP', 'STAFFING': 'STF', 'INSPECTION': 'INS'}[ttype] }-{n:04d}"
    db.execute("INSERT INTO tickets(id, type, status, priority, subject, financial, opened_at) VALUES (?,?,?,?,?,?,?)",
               (tid, ttype, "new", priority, subject, json.dumps(financial) if financial else None, now))
    db.execute("INSERT INTO ticket_events(ticket_id, at, actor, event, detail) VALUES (?,?,?,?,?)",
               (tid, now, actor, "opened", subject))
    return tid


# ================= Hot and calculated processes =================

def start_process(db, kind, cls, subject, started_at, checkpoints, deadline=None):
    pid = db.execute("INSERT INTO processes(kind, class, subject, started_at, deadline) VALUES (?,?,?,?,?)",
                     (kind, cls, subject, started_at, deadline)).lastrowid
    for seq, (name, due, rule) in enumerate(checkpoints, 1):
        db.execute("INSERT INTO process_checkpoints(process_id, seq, name, due_at, rule) VALUES (?,?,?,?,?)",
                   (pid, seq, name, due, json.dumps(rule) if rule else None))
    return pid


def start_cooling(db, batch, started_at, temp_f):
    """FDA Food Code cooling: 135F to 70F within 2 hours, then to 41F within 6 hours total."""
    t0 = _dt(started_at)
    if temp_f < 135:
        raise ValueError("cooling log starts at 135F or above")
    return start_process(db, "cooling", "hot", f"batch:{batch}", started_at, [
        ("at or below 70F", _iso(t0 + timedelta(hours=2)), {"max_f": 70}),
        ("at or below 41F", _iso(t0 + timedelta(hours=6)), {"max_f": 41}),
    ])


def start_order_shipment(db, order_id, promised_delivery, transit_days, stage_days, qc_rule):
    """Calculated: work back from the promised delivery date to a due date for every stage."""
    ship_by = _dt(promised_delivery) - timedelta(days=transit_days)
    names = list(stage_days)
    dues, t = {}, ship_by
    dues["shipped"] = t
    for name in reversed(names):
        dues[name] = t
        t -= timedelta(days=stage_days[name])
    cps = [(n, _iso(dues[n]), qc_rule if n == "qc_passed" else None) for n in names]
    cps.append(("shipped", _iso(ship_by), None))
    return start_process(db, "order_shipment", "calculated", f"order:{order_id}", _iso(t), cps,
                         deadline=promised_delivery)


def _passes(rule, value):
    if not rule:
        return True
    if "max_f" in rule:
        return value["temp_f"] <= rule["max_f"]
    if "min" in rule:
        return all(value.get(k, -1) >= v for k, v in rule["min"].items())
    return True


def record(db, process_id, seq, at, actor, value=None):
    cp = db.execute("SELECT * FROM process_checkpoints WHERE process_id=? AND seq=?", (process_id, seq)).fetchone()
    rule = json.loads(cp["rule"]) if cp["rule"] else None
    passed = _passes(rule, value or {}) and _dt(at) <= _dt(cp["due_at"])
    db.execute("INSERT INTO process_readings(checkpoint_id, at, actor, value, passed) VALUES (?,?,?,?,?)",
               (cp["id"], at, actor, json.dumps(value) if value else None, int(passed)))
    late = _dt(at) > _dt(cp["due_at"])
    return {"checkpoint": cp["name"], "passed": passed, "late": late}


def sweep(db, now):
    """Run every few minutes. Overdue checkpoints raise alerts (hot) or open SHIPMENT tickets (calculated)."""
    out = []
    for cp in db.execute("SELECT c.*, p.class, p.kind, p.subject, p.ticket_id, p.deadline FROM process_checkpoints c "
                         "JOIN processes p ON p.id=c.process_id WHERE c.due_at < ? AND NOT EXISTS ("
                         "SELECT 1 FROM process_readings r WHERE r.checkpoint_id=c.id AND r.passed=1)", (now,)).fetchall():
        item = {"process_id": cp["process_id"], "subject": cp["subject"], "class": cp["class"],
                "checkpoint": cp["name"], "was_due": cp["due_at"]}
        if cp["class"] == "hot":
            item["action"] = "ALERT manager now; log corrective action"
        elif not cp["ticket_id"]:
            pr = ticket_priority(db, "SHIPMENT", party=cp["subject"])
            tid = open_ticket(db, "SHIPMENT", f"{cp['subject']} at risk: '{cp['name']}' missed {cp['due_at']}, "
                              f"promised {cp['deadline']}", pr, now, "hiwr_sweep")
            db.execute("UPDATE processes SET ticket_id=? WHERE id=?", (tid, cp["process_id"]))
            item["action"] = f"opened {tid} (priority {pr})"
        else:
            item["action"] = f"already on {cp['ticket_id']}"
        out.append(item)
    return out


def log_corrective_action(db, at, process_id, what_failed, reading, action, actor):
    return db.execute("INSERT INTO corrective_actions(at, process_id, what_failed, reading, action, actor) "
                      "VALUES (?,?,?,?,?,?)", (at, process_id, what_failed, reading, action, actor)).lastrowid

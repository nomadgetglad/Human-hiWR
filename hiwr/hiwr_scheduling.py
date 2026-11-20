"""HiWR scheduling: reminder calls, cancellation with fee and sunk-cost estimates,
and rescheduling against staff availability with payroll and equipment costing.

Money never moves here. Fees, waivers and call-in pay become money_actions that stay
PENDING until two certified approvers sign (see hiwr_contacts.approve).
"""
import json
from datetime import datetime, timedelta
from hiwr_contacts import _event, advance_appointment, request_money_action

OVERTIME_AFTER = 40.0   # weekly hours
OVERTIME_RATE = 1.5


def _dt(s):
    return datetime.fromisoformat(s)


def _week_start(day):
    d = _dt(day + "T00:00") if "T" not in day else _dt(day)
    return (d - timedelta(days=d.weekday())).date().isoformat()


def log_call(db, appointment_id, actor, outcome, note=None, ticket_id=None):
    """Live-person handoff: the reminder call is recorded on the client's history."""
    cid = db.execute("SELECT contact_id FROM appointments WHERE id=?", (appointment_id,)).fetchone()[0]
    _event(db, cid, actor, "call", {"appointment": appointment_id, "type": "reminder", "outcome": outcome,
                                    "note": note}, ticket_id)


def cancellation_impact(db, appointment_id, now):
    """What cancelling now costs: client late fee, staff call-in pay, equipment cancel fees."""
    a = db.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
    notice = (_dt(a["starts_at"]) - _dt(now)).total_seconds() / 3600
    pol = db.execute("SELECT * FROM fee_policy WHERE kind=?", (a["kind"],)).fetchone()
    late = bool(pol and notice < pol["notice_hours"])
    fee = round((a["quote_cents"] or 0) * pol["late_fee_pct"]) if late else 0

    call_in = []
    for r in db.execute("SELECT r.assigned, s.hourly_cents, s.min_call_in_hours FROM appointment_requirements r "
                        "JOIN staff s ON s.member=r.assigned WHERE r.appointment_id=? AND r.need_type='role'",
                        (appointment_id,)):
        if late and r["min_call_in_hours"]:
            call_in.append({"member": r["assigned"], "hours": r["min_call_in_hours"],
                            "cents": round(r["hourly_cents"] * r["min_call_in_hours"])})
    equip = []
    for r in db.execute("SELECT e.* FROM appointment_requirements r JOIN equipment e ON e.name=r.assigned "
                        "WHERE r.appointment_id=? AND r.need_type='equipment'", (appointment_id,)):
        if notice < r["cancel_notice_hours"] and r["cancel_fee_cents"]:
            equip.append({"equipment": r["name"], "cents": r["cancel_fee_cents"]})
    return {"notice_hours": round(notice, 1), "late": late, "late_fee_cents": fee,
            "fee_waivable_on_reschedule": bool(pol and pol["reschedule_waives"]),
            "call_in_pay": call_in, "equipment_cancel_fees": equip,
            "sunk_cost_cents": sum(c["cents"] for c in call_in) + sum(e["cents"] for e in equip)}


def cancel(db, appointment_id, actor, now, reason):
    impact = cancellation_impact(db, appointment_id, now)
    advance_appointment(db, appointment_id, "cancelled", actor, note=reason)
    return impact


def _labor_cost(db, member, day, hours):
    s = db.execute("SELECT hourly_cents FROM staff WHERE member=?", (member,)).fetchone()
    booked = db.execute("SELECT hours FROM staff_booked_hours WHERE member=? AND week_start=?",
                        (member, _week_start(day))).fetchone()
    before = booked["hours"] if booked else 0.0
    ot = max(0.0, before + hours - OVERTIME_AFTER)
    reg = hours - min(ot, hours)
    cents = round(s["hourly_cents"] * (reg + min(ot, hours) * OVERTIME_RATE))
    return cents, round(min(ot, hours), 2)


def find_slots(db, appointment_id, from_day, days, earliest=None, latest=None, limit=5):
    """Every day and start hour where all required roles and equipment are free, cheapest first."""
    a = db.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
    dur = (_dt(a["ends_at"]) - _dt(a["starts_at"])).total_seconds() / 3600
    start_hour = _dt(a["starts_at"]).hour if earliest is None else earliest
    reqs = db.execute("SELECT need_type, need, SUM(qty) AS qty FROM appointment_requirements "
                      "WHERE appointment_id=? GROUP BY need_type, need", (appointment_id,)).fetchall()
    out = []
    for i in range(days):
        day = (_dt(from_day + "T00:00") + timedelta(days=i)).date().isoformat()
        for h in range(int(start_hour), int((latest if latest is not None else start_hour)) + 1):
            crew, labor, ot_total, ok = [], 0, 0.0, True
            for r in (r for r in reqs if r["need_type"] == "role"):
                cands = []
                for m in db.execute("SELECT s.member FROM staff s JOIN staff_availability v ON v.member=s.member "
                                    "WHERE s.role=? AND v.day=? AND v.start_hour<=? AND v.end_hour>=?",
                                    (r["need"], day, h, h + dur)):
                    if m["member"] in [c["member"] for c in crew]:
                        continue
                    cost, ot = _labor_cost(db, m["member"], day, dur)
                    cands.append((cost, ot, m["member"]))
                cands.sort()
                if len(cands) < r["qty"]:
                    ok = False
                    break
                for cost, ot, mem in cands[:r["qty"]]:
                    crew.append({"member": mem, "role": r["need"], "cents": cost, "overtime_hours": ot})
                    labor += cost
                    ot_total += ot
            if not ok:
                continue
            equip_cost, gear = 0, []
            for r in (r for r in reqs if r["need_type"] == "equipment"):
                if db.execute("SELECT 1 FROM equipment_blackout WHERE name=? AND day=?", (r["need"], day)).fetchone():
                    ok = False
                    break
                e = db.execute("SELECT cost_cents FROM equipment WHERE name=?", (r["need"],)).fetchone()
                equip_cost += e["cost_cents"] * r["qty"]
                gear.append(r["need"])
            if not ok:
                continue
            out.append({"starts_at": f"{day}T{h:02d}:00", "hours": dur, "crew": crew, "equipment": gear,
                        "payroll_cents": labor, "overtime_hours": ot_total, "equipment_cents": equip_cost,
                        "total_cost_cents": labor + equip_cost})
    out.sort(key=lambda s: (s["total_cost_cents"], s["starts_at"]))
    return out[:limit]


def rebook(db, old_id, slot, actor):
    """New appointment from a chosen slot. The old one stays, cancelled, and both are linked."""
    a = db.execute("SELECT * FROM appointments WHERE id=?", (old_id,)).fetchone()
    start = _dt(slot["starts_at"])
    new_id = db.execute(
        "INSERT INTO appointments(contact_id, starts_at, ends_at, party_size, quote_cents, kind, workflow, details) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (a["contact_id"], slot["starts_at"], (start + timedelta(hours=slot["hours"])).isoformat(timespec="minutes"),
         a["party_size"], a["quote_cents"], a["kind"], a["workflow"], f"rescheduled from #{old_id}")).lastrowid
    for c in slot["crew"]:
        db.execute("INSERT INTO appointment_requirements VALUES (?, 'role', ?, 1, ?)", (new_id, c["role"], c["member"]))
    for g in slot["equipment"]:
        db.execute("INSERT INTO appointment_requirements VALUES (?, 'equipment', ?, 1, ?)", (new_id, g, g))
    db.execute("INSERT INTO appointment_events(appointment_id, stage, actor, note) VALUES (?, 'booked', ?, ?)",
               (new_id, actor, f"rescheduled from #{old_id}"))
    _event(db, a["contact_id"], actor, "rescheduled", {"from": old_id, "to": new_id, "starts_at": slot["starts_at"]})
    return new_id


def propose_money_actions(db, impact, requested_by, ticket_id, rebooked_within_14_days):
    """Turn the impact into PENDING money actions. Nothing is charged or paid here."""
    ids = []
    if impact["late_fee_cents"]:
        waive = impact["fee_waivable_on_reschedule"] and rebooked_within_14_days
        amt = f"${impact['late_fee_cents'] / 100:,.2f}"
        desc = (f"Late cancellation fee {amt} - proposed WAIVED (rebooked within 14 days)" if waive
                else f"Late cancellation fee {amt} charged to client")
        ids.append(request_money_action(db, "fee", 0 if waive else impact["late_fee_cents"], desc,
                                        requested_by, ticket_id))
    for c in impact["call_in_pay"]:
        ids.append(request_money_action(db, "payroll", c["cents"],
                                        f"Call-in pay {c['hours']}h for {c['member']} (cancelled shift)",
                                        requested_by, ticket_id))
    return ids

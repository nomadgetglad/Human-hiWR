"""Human: the employee module of HiWR. It works only from HiWR's records.

  staffing   - staff needed each day from that day's appointments (covers, visits) or production quota
  schedule   - fills those needs from staff availability, cheapest first with overtime counted;
               gaps open a STAFFING ticket and go to the hiring matcher
  logistics  - materials needed for the week vs stock, order-by dates from lead times, and options
               when an order is already late (faster vendor, split the order, move production)
  documents  - a supply chain brief built from the records, ready to file or send

Human proposes; people decide. Schedules are drafts until a manager publishes them, purchase drafts until a
manager sends them, and payroll changes wait for certified double approval.
"""
import math
from datetime import datetime, timedelta
from hiwr_scheduling import _labor_cost
from hiwr_hiring import open_staffing_ticket

def _dt(s): return datetime.fromisoformat(s)
def _days(from_day, n): return [(_dt(from_day + "T00:00") + timedelta(days=i)).date().isoformat() for i in range(n)]


def _active(db, appointment_id):
    r = db.execute("SELECT stage FROM appointment_events WHERE appointment_id=? ORDER BY at DESC, id DESC LIMIT 1",
                   (appointment_id,)).fetchone()
    return not r or r["stage"] not in ("cancelled", "no_show")


def demand(db, day):
    rows = db.execute("SELECT id, kind, party_size FROM appointments WHERE substr(starts_at,1,10)=?", (day,)).fetchall()
    live = [r for r in rows if _active(db, r["id"])]
    covers = sum(r["party_size"] or 0 for r in live if r["kind"] in ("reservation", "catering"))
    visits = sum(1 for r in live if r["kind"] == "clinic_visit")
    units = db.execute("SELECT COALESCE(SUM(units),0) FROM production_quota WHERE day=?", (day,)).fetchone()[0]
    return {"covers": covers, "appointments": visits, "units": units}


def staff_needed(db, day):
    d = demand(db, day)
    out = []
    for r in db.execute("SELECT * FROM staffing_rules"):
        n = max(r["minimum"], math.ceil(d[r["driver"]] / r["per_staff"])) if d[r["driver"]] or r["minimum"] else 0
        if n:
            out.append({"role": r["role"], "count": n, "start_hour": r["start_hour"], "end_hour": r["end_hour"],
                        "because": f"{d[r['driver']]:g} {r['driver']} / {r['per_staff']:g} per {r['role']}"
                                   + (f", minimum {r['minimum']}" if r["minimum"] else "")})
    return d, out


def build_schedule(db, from_day, days, now, actor="human"):
    """Draft schedule + payroll estimate. Gaps open one STAFFING ticket."""
    plan, gaps, total, ot_hours = [], [], 0, 0.0
    hours_this_week = {}
    for day in _days(from_day, days):
        d, needs = staff_needed(db, day)
        for n in needs:
            hours = n["end_hour"] - n["start_hour"]
            cands = []
            for s in db.execute("SELECT s.member FROM staff s JOIN staff_availability v ON v.member=s.member "
                                "WHERE s.role=? AND v.day=? AND v.start_hour<=? AND v.end_hour>=?",
                                (n["role"], day, n["start_hour"], n["end_hour"])):
                cost, ot = _labor_cost_with(db, s["member"], day, hours, hours_this_week)
                cands.append((cost, ot, s["member"]))
            cands.sort()
            chosen = cands[:n["count"]]
            for cost, ot, m in chosen:
                hours_this_week[m] = hours_this_week.get(m, 0) + hours
                total += cost
                ot_hours += ot
            plan.append({"day": day, "role": n["role"], "need": n["count"], "because": n["because"],
                         "assigned": [m + (f" (OT {ot:g}h)" if ot else "") for _, ot, m in chosen],
                         "cost_cents": sum(c for c, _, _ in chosen)})
            if len(chosen) < n["count"]:
                gaps.append({"day": day, "weekday": _dt(day + "T00:00").weekday(), "role": n["role"],
                             "short": n["count"] - len(chosen), "start_hour": n["start_hour"], "end_hour": n["end_hour"]})
    ticket = open_staffing_ticket(db, gaps, now, actor) if gaps else None
    return {"status": "DRAFT - a manager publishes it", "plan": plan, "gaps": gaps, "staffing_ticket": ticket,
            "payroll_estimate_cents": total, "overtime_hours": ot_hours}


def _labor_cost_with(db, member, day, hours, extra):
    """Overtime counts hours already on the books plus hours this draft has given them."""
    s = db.execute("SELECT hourly_cents FROM staff WHERE member=?", (member,)).fetchone()
    base, _ = _labor_cost(db, member, day, 0)
    wk = (_dt(day + "T00:00") - timedelta(days=_dt(day + "T00:00").weekday())).date().isoformat()
    r = db.execute("SELECT hours FROM staff_booked_hours WHERE member=? AND week_start=?", (member, wk)).fetchone()
    before = (r["hours"] if r else 0) + extra.get(member, 0)
    ot = max(0.0, min(hours, before + hours - 40))
    return round(s["hourly_cents"] * (hours - ot + ot * 1.5)), ot


def material_plan(db, from_day, days, today, actor="human"):
    """Materials for the period vs stock. Each shortage gets an order-by date and options."""
    need, first_day = {}, {}
    for day in _days(from_day, days):
        d = demand(db, day)
        for product, qty in [("dinner_cover", d["covers"])] + [
                (r["product"], r["units"]) for r in db.execute("SELECT * FROM production_quota WHERE day=?", (day,))]:
            for b in db.execute("SELECT * FROM bill_of_materials WHERE product=?", (product,)):
                amt = b["per_unit"] * qty
                if amt:
                    need[b["material"]] = need.get(b["material"], 0) + amt
                    first_day.setdefault(b["material"], day)
    lines = []
    for mat, qty in sorted(need.items()):
        m = db.execute("SELECT * FROM materials WHERE material=?", (mat,)).fetchone()
        short = qty - m["on_hand"]
        line = {"material": mat, "unit": m["unit"], "need": round(qty, 1), "on_hand": m["on_hand"],
                "short": round(max(0, short), 1), "perishable": bool(m["perishable"]), "options": []}
        if short > 0:
            for src in db.execute("SELECT * FROM material_sources WHERE material=? ORDER BY preferred DESC, unit_cost_cents",
                                  (mat,)):
                q = math.ceil(short / src["min_order"]) * src["min_order"]
                order_by = (_dt(first_day[mat] + "T00:00") - timedelta(days=src["lead_days"])).date().isoformat()
                line["options"].append({"vendor": src["vendor"], "qty": q, "cost_cents": round(q * src["unit_cost_cents"]),
                                        "lead_days": src["lead_days"], "order_by": order_by,
                                        "on_time": order_by >= today, "preferred": bool(src["preferred"])})
            ok = [o for o in line["options"] if o["on_time"]]
            if not ok:
                line["advice"] = "No source arrives in time: move production later or split the run"
            elif not ok[0]["preferred"]:
                pref = next((o for o in line["options"] if o["preferred"]), None)
                extra = ok[0]["cost_cents"] - pref["cost_cents"] if pref else 0
                line["advice"] = (f"Preferred vendor is too late (order-by {pref['order_by']}). Use {ok[0]['vendor']}"
                                  f" (+${extra / 100:,.2f}), or move the first {mat} job later") if pref else f"Use {ok[0]['vendor']}"
            else:
                line["advice"] = f"Order from {ok[0]['vendor']} by {ok[0]['order_by']}"
            if ok:
                o = ok[0]
                line["draft_id"] = db.execute(
                    "INSERT INTO purchase_drafts(material, vendor, qty, cost_cents, order_by, needed_on, made_by) "
                    "VALUES (?,?,?,?,?,?,?)", (mat, o["vendor"], o["qty"], o["cost_cents"], o["order_by"],
                                               first_day[mat], actor)).lastrowid
        lines.append(line)
    return lines


def supply_chain_brief(db, schedule, materials, period, today):
    """Markdown brief built from the records: staffing, purchases, open tickets, corrective actions."""
    usd = lambda c: f"${c / 100:,.2f}"
    out = [f"# Supply chain and staffing brief: {period}", "", f"Prepared by Human on {today} from HiWR records. "
           "Drafts only: a manager publishes the schedule and sends purchase orders; payroll changes need double approval.", ""]
    out += ["## Staffing", "", "| Day | Role | Need | Why | Assigned | Cost |", "| --- | --- | --- | --- | --- | --- |"]
    for p in schedule["plan"]:
        out.append(f"| {p['day']} | {p['role']} | {p['need']} | {p['because']} | {', '.join(p['assigned']) or 'none'} | {usd(p['cost_cents'])} |")
    out += ["", f"Payroll estimate: {usd(schedule['payroll_estimate_cents'])}, overtime {schedule['overtime_hours']:g} h."]
    if schedule["gaps"]:
        out.append(f"Gaps: {len(schedule['gaps'])} shifts, on ticket {schedule['staffing_ticket']} for the hiring matcher.")
    out += ["", "## Materials", "", "| Material | Need | On hand | Short | Order | Order by | Advice |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
    for m in materials:
        o = next((x for x in m["options"] if x["on_time"]), None)
        order = f"{o['qty']:g} {m['unit']} from {o['vendor']}, {usd(o['cost_cents'])}" if o else "-"
        out.append(f"| {m['material']}{' (perishable)' if m['perishable'] else ''} | {m['need']:g} {m['unit']} | "
                   f"{m['on_hand']:g} | {m['short']:g} | {order} | {o['order_by'] if o else '-'} | {m.get('advice', 'In stock')} |")
    tickets = db.execute("SELECT * FROM tickets WHERE status!='closed' ORDER BY priority").fetchall()
    out += ["", "## Open tickets", ""] + ([f"- {t['id']} ({t['type']}, priority {t['priority']}): {t['subject']}" for t in tickets] or ["- None"])
    cas = db.execute("SELECT * FROM corrective_actions ORDER BY at DESC LIMIT 10").fetchall()
    out += ["", "## Corrective actions", ""] + ([f"- {c['at']}: {c['what_failed']} ({c['reading']}) -> {c['action']}"
                                                 f"{'' if c['verified_by'] else ', not yet verified'}" for c in cas] or ["- None"])
    return "\n".join(out) + "\n"

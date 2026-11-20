"""Lab: long jobs that run for months, such as building a house.

  phases       each with a labor and material budget and, where the code requires it, an inspection
  gates        a phase can't start until the previous phase's inspection passed; a failed inspection
               blocks the next phase and opens an INSPECTION ticket
  subcontracts plumbing, electrical, HVAC ... each with a contract amount, retainage (10% default),
               license and insurance on file
  draws        a sub's pay request = % complete x contract - already billed, minus retainage.
               Blocked if insurance has lapsed, no lien waiver came with it, or the final draw comes
               before that phase passed inspection. Otherwise it waits for two certified approvers.
  retainage    released only after the final inspection passes and an unconditional lien waiver is in
  crew payroll timesheets per phase, compared with the phase labor budget; each pay run waits for approval
Construction law (lien waivers, retainage limits, prompt-pay rules) varies by state: have the
contract forms reviewed locally. This is a working model, not legal advice.
"""
from hiwr_contacts import request_money_action
from hiwr_processes import open_ticket, ticket_priority

def usd(c): return f"${c / 100:,.2f}"


def phase(db, project_id, seq):
    return db.execute("SELECT * FROM lab_phases WHERE project_id=? AND seq=?", (project_id, seq)).fetchone()


def _passed(db, phase_id):
    r = db.execute("SELECT result FROM lab_inspections WHERE phase_id=? ORDER BY id DESC LIMIT 1", (phase_id,)).fetchone()
    return bool(r and r["result"] == "pass")


def start_phase(db, project_id, seq, at, actor):
    p = phase(db, project_id, seq)
    if seq > 1:
        prev = phase(db, project_id, seq - 1)
        if prev["inspection"] and not _passed(db, prev["id"]):
            db.execute("INSERT INTO lab_phase_events(phase_id, event, at, actor, note) VALUES (?, 'blocked', ?, ?, ?)",
                       (p["id"], at, actor, f"waiting on {prev['inspection']}"))
            raise PermissionError(f"'{p['name']}' can't start: {prev['inspection']} for '{prev['name']}' has not passed")
    db.execute("INSERT INTO lab_phase_events(phase_id, event, at, actor) VALUES (?, 'started', ?, ?)", (p["id"], at, actor))


def inspect(db, project_id, seq, at, inspector, result, notes=None, responsible_sub=None):
    p = phase(db, project_id, seq)
    ticket = None
    if result == "fail":
        ticket = open_ticket(db, "INSPECTION", f"{p['inspection']} failed on '{p['name']}': {notes}"
                             + (f" (responsible: {responsible_sub})" if responsible_sub else ""),
                             ticket_priority(db, "INSPECTION", party=responsible_sub), at, inspector)
    db.execute("INSERT INTO lab_inspections(phase_id, kind, at, inspector, result, notes, ticket_id) VALUES (?,?,?,?,?,?,?)",
               (p["id"], p["inspection"], at, inspector, result, notes, ticket))
    if result == "pass":
        db.execute("INSERT INTO lab_phase_events(phase_id, event, at, actor) VALUES (?, 'completed', ?, ?)", (p["id"], at, inspector))
        for t in db.execute("SELECT ticket_id FROM lab_inspections WHERE phase_id=? AND ticket_id IS NOT NULL", (p["id"],)):
            db.execute("UPDATE tickets SET status='resolved pending financial sync' WHERE id=? AND status!='closed'", (t["ticket_id"],))
            db.execute("INSERT INTO ticket_events(ticket_id, at, actor, event, detail) VALUES (?,?,?,?,?)",
                       (t["ticket_id"], at, inspector, "reinspection passed", None))
    return ticket


def request_draw(db, subcontract_id, pct_complete, at, lien_waiver=None, backcharge_cents=0, backcharge_note=None,
                 requested_by="pm"):
    s = db.execute("SELECT * FROM lab_subcontracts WHERE id=?", (subcontract_id,)).fetchone()
    billed = db.execute("SELECT COALESCE(SUM(gross_cents),0) FROM lab_draws WHERE subcontract_id=? AND status!='blocked'",
                        (subcontract_id,)).fetchone()[0]
    gross = round(s["amount_cents"] * pct_complete) - billed
    ret = round(gross * s["retainage_pct"])
    net = gross - ret - backcharge_cents
    problems = []
    if s["insurance_expires"] and s["insurance_expires"] < at[:10]:
        problems.append(f"insurance expired {s['insurance_expires']}")
    if not lien_waiver:
        problems.append("no lien waiver with this draw")
    if pct_complete >= 1.0 and not _passed(db, s["phase_id"]):
        problems.append("final draw before the phase passed inspection")
    status = "blocked" if problems else "pending_approval"
    action = None
    if not problems:
        action = request_money_action(db, "subcontract", net,
                                      f"{s['sub']} draw to {int(pct_complete * 100)}%: {usd(gross)} - retainage {usd(ret)}"
                                      + (f" - backcharge {usd(backcharge_cents)} ({backcharge_note})" if backcharge_cents else ""),
                                      requested_by)
    db.execute("INSERT INTO lab_draws(subcontract_id, at, pct_complete, gross_cents, retainage_cents, backcharge_cents, net_cents, "
               "lien_waiver, money_action_id, status, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (subcontract_id, at, pct_complete, gross, ret, backcharge_cents, net, lien_waiver, action, status,
                "; ".join(problems) or backcharge_note))
    return {"sub": s["sub"], "gross": gross, "retainage": ret, "backcharge": backcharge_cents, "net": net,
            "status": status, "problems": problems, "money_action": action}


def release_retainage(db, project_id, at, requested_by="pm"):
    final = db.execute("SELECT * FROM lab_phases WHERE project_id=? ORDER BY seq DESC LIMIT 1", (project_id,)).fetchone()
    if not _passed(db, final["id"]):
        raise PermissionError(f"retainage stays held until {final['inspection']} passes")
    out = []
    for s in db.execute("SELECT * FROM lab_subcontracts WHERE project_id=?", (project_id,)):
        held = db.execute("SELECT COALESCE(SUM(retainage_cents),0) FROM lab_draws WHERE subcontract_id=? AND status!='blocked'",
                          (s["id"],)).fetchone()[0]
        last = db.execute("SELECT lien_waiver FROM lab_draws WHERE subcontract_id=? AND status!='blocked' ORDER BY id DESC LIMIT 1",
                          (s["id"],)).fetchone()
        if last and last["lien_waiver"] == "unconditional":
            a = request_money_action(db, "subcontract", held, f"Release retainage to {s['sub']}", requested_by)
            out.append((s["sub"], held, f"pending approval #{a}"))
        else:
            out.append((s["sub"], held, "held: final unconditional lien waiver missing"))
    return out


def log_time(db, project_id, seq, member, day, hours, rate_cents):
    db.execute("INSERT INTO lab_timesheets(phase_id, member, day, hours, rate_cents) VALUES (?,?,?,?,?)",
               (phase(db, project_id, seq)["id"], member, day, hours, rate_cents))


def payroll_run(db, project_id, week_start, week_end, requested_by="pm"):
    """One pay run per phase for the week; each waits for double approval."""
    out = []
    for p in db.execute("SELECT * FROM lab_phases WHERE project_id=? ORDER BY seq", (project_id,)):
        cents = db.execute("SELECT COALESCE(SUM(hours * rate_cents),0) FROM lab_timesheets WHERE phase_id=? AND day BETWEEN ? AND ?",
                           (p["id"], week_start, week_end)).fetchone()[0]
        if cents:
            a = request_money_action(db, "payroll", round(cents), f"Crew payroll {week_start}..{week_end}, {p['name']}", requested_by)
            out.append((p["name"], round(cents), a))
    return out


def job_cost(db, project_id):
    rows = []
    for p in db.execute("SELECT * FROM lab_phases WHERE project_id=? ORDER BY seq", (project_id,)):
        labor = round(db.execute("SELECT COALESCE(SUM(hours * rate_cents),0) FROM lab_timesheets WHERE phase_id=?", (p["id"],)).fetchone()[0])
        subs = db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM lab_subcontracts WHERE phase_id=?", (p["id"],)).fetchone()[0]
        last = db.execute("SELECT result FROM lab_inspections WHERE phase_id=? ORDER BY id DESC LIMIT 1", (p["id"],)).fetchone()
        rows.append({"phase": p["name"], "labor": labor, "labor_budget": p["labor_budget_cents"], "subs": subs,
                     "over": labor > p["labor_budget_cents"] > 0, "inspection": p["inspection"],
                     "status": last["result"] if last else "-"})
    return rows

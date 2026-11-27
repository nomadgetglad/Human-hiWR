"""Staff section of Human, same-night fills, and work agreements with terms for both sides.

Pay rule: every hour worked is paid at the agreed rate. Broken conditions are answered with
documented warnings, a reliability score and, for repeat no-shows, ending the agreement and pausing
matching. Pay is never docked as a penalty. Employer breaches are recorded the same way and owed
pay (call-in pay, minimum hours) becomes a payroll action that waits for double approval.
The contract text is a template to review with an employment lawyer, not legal advice.
"""
import json
from datetime import datetime, timedelta
from hiwr_contacts import request_money_action
from hiwr_human import staff_needed
from hiwr_roles import experience

def _dt(s): return datetime.fromisoformat(s)
def _iso(d): return d.isoformat(timespec="minutes")
def usd(c): return f"${c / 100:,.2f}"

TERMS_VERSION = "temp-v1"
LATE_GRACE_MIN = 10
SCORE = {"worker": {"late": -5, "no_call_no_show": -25, "left_early_no_notice": -10},
         "employer": {"late_cancel": -10, "hours_cut": -10, "late_pay": -20}}
EMPLOYER_TAX_EST = 0.0765   # employer Social Security + Medicare; state unemployment not included


# ---------------- HR requests ----------------

def hr_request(db, member, kind, at, from_day=None, to_day=None, detail=None):
    return db.execute("INSERT INTO hr_requests(member, kind, from_day, to_day, detail, at) VALUES (?,?,?,?,?,?)",
                      (member, kind, from_day, to_day, detail, at)).lastrowid


def decide_hr(db, request_id, status, actor, at, note=None):
    db.execute("INSERT INTO hr_request_events(request_id, status, actor, note, at) VALUES (?,?,?,?,?)",
               (request_id, status, actor, note, at))
    r = db.execute("SELECT * FROM hr_requests WHERE id=?", (request_id,)).fetchone()
    if r["kind"] == "time_off" and status == "approved":   # approved time off removes availability
        db.execute("DELETE FROM staff_availability WHERE member=? AND day BETWEEN ? AND ?",
                   (r["member"], r["from_day"], r["to_day"]))


def _open_hr(db):
    return [dict(r) for r in db.execute(
        "SELECT * FROM hr_requests h WHERE NOT EXISTS (SELECT 1 FROM hr_request_events e WHERE e.request_id=h.id) "
        "ORDER BY CASE kind WHEN 'time_off' THEN 0 WHEN 'swap' THEN 1 ELSE 2 END, COALESCE(from_day, '9999'), at")]


# ---------------- Staff section ----------------

def staff_section(db, day, roles=None):
    """What a chef sees: needed vs on shift by role, call-outs, open HR requests, staffing tickets."""
    demand, needs = staff_needed(db, day)
    rows = []
    for n in needs:
        if roles and n["role"] not in roles:
            continue
        on = [r["member"] for r in db.execute(
            "SELECT s.member FROM staff s JOIN staff_availability v ON v.member=s.member WHERE s.role=? AND v.day=? "
            "AND v.start_hour<=? AND v.end_hour>=?", (n["role"], day, n["start_hour"], n["end_hour"]))]
        temps = [r["member"] for r in db.execute(
            "SELECT a.member FROM work_agreements a JOIN shifts s ON s.agreement_id=a.id JOIN positions p ON p.id=a.position_id "
            "WHERE p.role=? AND substr(s.starts_at,1,10)=?", (n["role"], day))]
        rows.append({"role": n["role"], "need": n["count"], "on_shift": on + [t + " (temp)" for t in temps],
                     "short": max(0, n["count"] - len(on) - len(temps)), "because": n["because"]})
    pressing = []
    for r in rows:
        if r["short"]:
            pressing.append(f"Tonight short {r['short']} {r['role']} - find same-night cover")
    for h in _open_hr(db):
        when = f" {h['from_day']} to {h['to_day']}" if h["from_day"] else ""
        pressing.append(f"HR: {h['member']} {h['kind'].replace('_', ' ')}{when}" + (f" ({h['detail']})" if h["detail"] else ""))
    for t in db.execute("SELECT * FROM tickets WHERE type='STAFFING' AND status!='closed'"):
        pressing.append(f"Ticket {t['id']}: {t['subject']}")
    return {"day": day, "demand": demand, "roles": rows, "pressing": pressing}


# ---------------- same-night fill ----------------

def reliability(db, party, key):
    """Score from 100 down. key = member (worker side) or 'employer' for the business."""
    col = "a.member=?" if party == "worker" else "1=1 AND ?=?"
    args = (key,) if party == "worker" else (key, key)
    total = 100
    for r in db.execute(f"SELECT c.kind FROM conduct_events c JOIN work_agreements a ON a.id=c.agreement_id "
                        f"WHERE c.party=? AND {col}", (party, *args)):
        total += SCORE[party].get(r["kind"], 0)
    return max(0, total)


def find_same_night(db, role, starts_at, ends_at, now):
    """Candidates free tonight: available that weekday and hours, not paused, best reliability first."""
    wd, sh = _dt(starts_at).weekday(), _dt(starts_at).hour + _dt(starts_at).minute / 60
    eh = sh + (_dt(ends_at) - _dt(starts_at)).total_seconds() / 3600
    out = []
    for c in db.execute("SELECT c.* FROM candidates c JOIN candidate_availability v ON v.candidate_id=c.id "
                        "WHERE v.weekday=? AND v.start_hour<=? AND v.end_hour>=?", (wd, sh, eh)):
        months = experience(db, c["id"], role)[0]
        member = f"temp_{c['name'].split()[0].lower()}"
        paused = db.execute("SELECT 1 FROM conduct_events e JOIN work_agreements a ON a.id=e.agreement_id "
                            "WHERE a.candidate_id=? AND e.response LIKE 'agreement ended%' AND e.at > ?",
                            (c["id"], _iso(_dt(now) - timedelta(days=60)))).fetchone()
        if paused:
            continue
        out.append({"candidate_id": c["id"], "name": c["name"], "months": months, "phone": c["phone"],
                    "reliability": reliability(db, "worker", member), "language": c["language"]})
    out.sort(key=lambda x: (-x["reliability"], -x["months"]))
    return out


def send_offers(db, cands, role, starts_at, ends_at, hourly_cents, now, hold_minutes=30, top=3):
    """Text the top few at once; the first yes takes the shift."""
    hold = _iso(_dt(now) + timedelta(minutes=hold_minutes))
    ids = []
    for c in cands[:top]:
        ids.append(db.execute("INSERT INTO shift_offers(candidate_id, role, starts_at, ends_at, hourly_cents, sent_at, hold_until) "
                              "VALUES (?,?,?,?,?,?,?)", (c["candidate_id"], role, starts_at, ends_at, hourly_cents, now, hold)).lastrowid)
    return ids, hold


def answer_offer(db, offer_id, answer, at):
    o = db.execute("SELECT * FROM shift_offers WHERE id=?", (offer_id,)).fetchone()
    taken = db.execute("SELECT 1 FROM shift_offers WHERE starts_at=? AND role=? AND answer='yes'",
                       (o["starts_at"], o["role"])).fetchone()
    if answer == "yes" and (taken or _dt(at) > _dt(o["hold_until"])):
        db.execute("UPDATE shift_offers SET answer='late', answered_at=? WHERE id=?", (at, offer_id))
        return "already filled, thanks"
    db.execute("UPDATE shift_offers SET answer=?, answered_at=? WHERE id=?", (answer, at, offer_id))
    return "confirmed" if answer == "yes" else "noted"


# ---------------- work agreements ----------------

def create_agreement(db, position_id, candidate_id, kind, starts_on, ends_on, hourly_cents, min_hours, shifts,
                     employer_signer, signed_at):
    c = db.execute("SELECT name FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    member = f"temp_{c['name'].split()[0].lower()}"
    aid = db.execute("INSERT INTO work_agreements(position_id, candidate_id, member, kind, starts_on, ends_on, hourly_cents, "
                     "min_hours_per_shift, terms_version, signed_worker_at, signed_employer_at, signed_employer_by) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (position_id, candidate_id, member, kind, starts_on, ends_on, hourly_cents, min_hours, TERMS_VERSION,
                      signed_at, signed_at, employer_signer)).lastrowid
    for s, e in shifts:
        db.execute("INSERT INTO shifts(agreement_id, starts_at, ends_at) VALUES (?,?,?)", (aid, s, e))
    return aid


def contract_text(db, agreement_id, business="the Restaurant"):
    a = db.execute("SELECT a.*, p.title, c.name FROM work_agreements a JOIN positions p ON p.id=a.position_id "
                   "JOIN candidates c ON c.id=a.candidate_id WHERE a.id=?", (agreement_id,)).fetchone()
    sh = db.execute("SELECT * FROM shifts WHERE agreement_id=? ORDER BY starts_at", (agreement_id,)).fetchall()
    hours = sum((_dt(s["ends_at"]) - _dt(s["starts_at"])).total_seconds() / 3600 for s in sh)
    lines = [f"# {a['kind'].title()} work agreement: {a['title']}", "",
             f"_Template {a['terms_version']} for review by an employment lawyer before use. Not legal advice._", "",
             f"**Worker:** {a['name']}  **Employer:** {business}  **Signed:** worker {a['signed_worker_at']}, "
             f"employer {a['signed_employer_at']} by {a['signed_employer_by']}", "",
             "## Work and pay", "",
             f"- Role: {a['title']}. Dates: {a['starts_on']} to {a['ends_on'] or 'open'}.",
             f"- Scheduled shifts ({len(sh)}, {hours:g} hours in total):"]
    lines += [f"    - {_dt(s['starts_at']).strftime('%a %b %d, %I:%M %p')} to {_dt(s['ends_at']).strftime('%I:%M %p')}" for s in sh]
    lines += [f"- Pay: {usd(a['hourly_cents'])} per hour for every hour worked, paid on the regular payday. "
              "Overtime past 40 hours in a week is paid at 1.5 times the rate.",
              "- Breaks and meals follow the posted policy and state law.", "",
              "## What the worker agrees to", "",
              f"1. Clock in and out for every shift. Arriving more than {LATE_GRACE_MIN} minutes late is recorded.",
              "2. Give at least 4 hours' notice if unable to work a shift (call or text the manager).",
              "3. Not leave a shift early without the manager's OK.",
              "4. Follow food safety, sanitation and safety rules.", "",
              "## What the employer agrees to", "",
              f"1. Cancel a shift at least 24 hours ahead, or pay {a['min_hours_per_shift']:g} hours at the agreed rate.",
              f"2. If a shift is cut short by the employer, pay at least {a['min_hours_per_shift']:g} hours.",
              "3. Pay all hours worked on the regular payday.",
              "4. Provide safe working conditions, equipment and training.", "",
              "## If conditions are not honored", "",
              "| Who | What happens | Response |", "| --- | --- | --- |",
              "| Worker | Late more than 10 minutes | Recorded; second time, written warning |",
              "| Worker | Leaves early without OK | Written warning |",
              "| Worker | No call, no show | Written warning; a second one ends the agreement and pauses matching for 60 days |",
              f"| Employer | Cancels with less than 24 hours' notice | {a['min_hours_per_shift']:g} hours call-in pay |",
              f"| Employer | Cuts a shift short | Pay for at least {a['min_hours_per_shift']:g} hours |",
              "| Employer | Pays late | Flagged to the owner; worker may end the agreement with no record against them |", "",
              "Pay is never reduced as a penalty: all hours worked are paid. Both sides' records are kept in HiWR "
              "and either side can open a dispute ticket. Any pay correction needs two certified approvers.", ""]
    return "\n".join(lines)


def shift_event(db, shift_id, event, at, actor, note=None):
    db.execute("INSERT INTO shift_events(shift_id, event, at, actor, note) VALUES (?,?,?,?,?)", (shift_id, event, at, actor, note))


def close_shift(db, shift_id, now, requested_by):
    """Evaluate a finished shift against the agreement. Returns hours worked and any conduct records."""
    s = db.execute("SELECT s.*, a.id AS aid, a.member, a.hourly_cents, a.min_hours_per_shift FROM shifts s "
                   "JOIN work_agreements a ON a.id=s.agreement_id WHERE s.id=?", (shift_id,)).fetchone()
    ev = {r["event"]: r for r in db.execute("SELECT * FROM shift_events WHERE shift_id=? ORDER BY id", (shift_id,))}
    start, end, notes, hours, owed = _dt(s["starts_at"]), _dt(s["ends_at"]), [], 0.0, None

    def conduct(party, kind, detail, response):
        db.execute("INSERT INTO conduct_events(agreement_id, party, kind, shift_id, at, detail, response) VALUES (?,?,?,?,?,?,?)",
                   (s["aid"], party, kind, shift_id, now, detail, response))
        notes.append(f"{party}: {kind.replace('_', ' ')} -> {response}")

    if "cancelled_by_employer" in ev:
        notice = (start - _dt(ev["cancelled_by_employer"]["at"])).total_seconds() / 3600
        if notice < 24:
            owed = round(s["min_hours_per_shift"] * s["hourly_cents"])
            conduct("employer", "late_cancel", f"{notice:.1f}h notice", f"{s['min_hours_per_shift']:g}h call-in pay owed")
    elif "called_out" in ev:
        notice = (start - _dt(ev["called_out"]["at"])).total_seconds() / 3600
        notes.append(f"worker: called out with {notice:.1f}h notice" + (" (within the 4h rule)" if notice >= 4 else ""))
        if notice < 4:
            conduct("worker", "late", f"call-out with {notice:.1f}h notice", "recorded")
    elif "clock_in" not in ev:
        prior = db.execute("SELECT COUNT(*) FROM conduct_events WHERE agreement_id=? AND kind='no_call_no_show'",
                           (s["aid"],)).fetchone()[0]
        conduct("worker", "no_call_no_show", "no punch, no notice",
                "agreement ended; matching paused 60 days" if prior else "written warning")
    else:
        cin, cout = _dt(ev["clock_in"]["at"]), _dt(ev["clock_out"]["at"])
        hours = (cout - cin).total_seconds() / 3600
        late_min = (cin - start).total_seconds() / 60
        if late_min > LATE_GRACE_MIN:
            prior = db.execute("SELECT COUNT(*) FROM conduct_events WHERE agreement_id=? AND kind='late'", (s["aid"],)).fetchone()[0]
            conduct("worker", "late", f"{late_min:.0f} min late", "written warning" if prior else "recorded")
        early = (end - cout).total_seconds() / 60
        if early > 15:
            if ev["clock_out"]["note"] and "manager ok" in ev["clock_out"]["note"].lower():
                notes.append(f"worker: left {early:.0f} min early with manager OK")
                if hours < s["min_hours_per_shift"]:
                    owed = round((s["min_hours_per_shift"] - hours) * s["hourly_cents"])
                    conduct("employer", "hours_cut", f"sent home after {hours:g}h", "paid up to minimum hours")
            else:
                conduct("worker", "left_early_no_notice", f"left {early:.0f} min early", "written warning")
    pay = round(hours * s["hourly_cents"])
    action = None
    if pay or owed:
        action = request_money_action(db, "payroll", pay + (owed or 0),
                                      f"{s['member']} shift {s['starts_at']}: {hours:g}h worked"
                                      + (f" + {usd(owed)} owed by employer terms" if owed else ""), requested_by)
    return {"shift": s["starts_at"], "hours": round(hours, 2), "pay_cents": pay, "owed_cents": owed or 0,
            "notes": notes, "money_action": action}


# ---------------- certified search (e.g. foreman, third shift) ----------------

def search_certified(db, role, all_of, any_of, weekdays, start_hour, end_hour, on_day, base_cents, night_diff_cents=0):
    """People holding every cert in all_of and at least one in any_of, verified and current on on_day,
    available on all the weekdays for the shift. Hours past 24 mean the next morning (22-30 = 10 PM-6 AM)."""
    found, near = [], []
    for c in db.execute("SELECT * FROM candidates"):
        certs = {r["cert"]: r for r in db.execute("SELECT * FROM candidate_certs WHERE candidate_id=?", (c["id"],))}
        def state(x):
            r = certs.get(x)
            if not r: return "missing"
            if r["expires_on"] and r["expires_on"] < on_day: return f"expired {r['expires_on']}"
            return "verified" if r["verified_by"] else "unverified"
        problems = [f"{x} {state(x)}" for x in all_of if state(x) != "verified"]
        any_ok = [x for x in any_of if state(x) == "verified"]
        if any_of and not any_ok:
            problems.append("needs one of " + "/".join(any_of) + " (" + ", ".join(f"{x} {state(x)}" for x in any_of) + ")")
        days_ok = [wd for wd in weekdays if db.execute(
            "SELECT 1 FROM candidate_availability WHERE candidate_id=? AND weekday=? AND start_hour<=? AND end_hour>=?",
            (c["id"], wd, start_hour, end_hour)).fetchone()]
        if len(days_ok) < len(weekdays):
            problems.append(f"available {len(days_ok)}/{len(weekdays)} nights")
        months = experience(db, c["id"], role)[0]
        if not months:
            continue
        row = {"name": c["name"], "months": months, "certs": {x: state(x) for x in all_of + any_of if x in certs},
               "rate": usd(base_cents + night_diff_cents) + "/h" + (f" (incl. {usd(night_diff_cents)} night differential)" if night_diff_cents else "")}
        (near if problems else found).append({**row, "problems": problems})
    found.sort(key=lambda r: -r["months"])
    return found, near

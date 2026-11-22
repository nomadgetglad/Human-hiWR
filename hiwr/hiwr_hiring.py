"""HiWR hiring: find staffing gaps, open a STAFFING ticket, rank candidates, and leave
every hire decision to a person.

Ranking uses only job-related facts:
  certifications  - required ones must be current on the start date; unverified ones are flagged
  availability    - share of the gap shifts the candidate can cover
  experience      - months in the same role
  work style      - the candidate's own answers to questions about what the job involves
                    (fast pace, team work, customer facing, late nights). Not a personality test.
Nothing about age, sex, race, religion, national origin, disability or family status is stored.
"""
import json
from datetime import datetime, timedelta
from hiwr_contacts import _is_owner, request_money_action
from hiwr_processes import open_ticket, ticket_priority

WEIGHTS = {"availability": 50, "experience": 30, "work_style": 20}


def _dt(s): return datetime.fromisoformat(s)


def find_gaps(db, needs, from_day, days):
    """needs = {weekday: [(role, count, start_hour, end_hour)]}. Compares with staff_availability."""
    gaps = []
    for i in range(days):
        day = _dt(from_day + "T00:00") + timedelta(days=i)
        for role, count, sh, eh in needs.get(day.weekday(), []):
            have = db.execute("SELECT COUNT(*) FROM staff s JOIN staff_availability v ON v.member=s.member "
                              "WHERE s.role=? AND v.day=? AND v.start_hour<=? AND v.end_hour>=?",
                              (role, day.date().isoformat(), sh, eh)).fetchone()[0]
            if have < count:
                gaps.append({"day": day.date().isoformat(), "weekday": day.weekday(), "role": role,
                             "short": count - have, "start_hour": sh, "end_hour": eh})
    return gaps


def open_staffing_ticket(db, gaps, now, actor):
    days = ", ".join(sorted({datetime.fromisoformat(g["day"]).strftime("%a %b %d") for g in gaps}))
    roles = ", ".join(sorted({g["role"] for g in gaps}))
    return open_ticket(db, "STAFFING", f"Short {roles} on {days} ({len(gaps)} shifts)",
                       ticket_priority(db, "STAFFING"), now, actor)


def _cert_status(db, cid, cert, start_on):
    r = db.execute("SELECT * FROM candidate_certs WHERE candidate_id=? AND cert=? ORDER BY expires_on DESC LIMIT 1",
                   (cid, cert)).fetchone()
    if not r:
        return "missing"
    if r["expires_on"] and r["expires_on"] < start_on:
        return f"expired {r['expires_on']}"
    return "verified" if r["verified_by"] else "unverified"


def rank_candidates(db, position_id, gaps, start_on):
    pos = db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
    req, pref = json.loads(pos["required_certs"]), json.loads(pos["preferred_certs"])
    style = json.loads(pos["work_style"] or "{}")
    role_gaps = [g for g in gaps if g["role"] == pos["role"]]
    ranked, not_eligible = [], []
    for c in db.execute("SELECT * FROM candidates"):
        cid, reasons, flags = c["id"], [], []
        certs = {x: _cert_status(db, cid, x, start_on) for x in req + pref}
        blocking = [f"{x}: {certs[x]}" for x in req if certs[x] in ("missing",) or certs[x].startswith("expired")]
        flags += [f"verify {x} with issuer before start" for x in req + pref if certs[x] == "unverified"]
        months = db.execute("SELECT COALESCE(SUM(months),0) FROM candidate_experience WHERE candidate_id=? AND role=?",
                            (cid, pos["role"])).fetchone()[0]
        if months < pos["min_experience_months"]:
            blocking.append(f"{months} months as {pos['role']} (needs {pos['min_experience_months']})")
        covered = [g for g in role_gaps if db.execute(
            "SELECT 1 FROM candidate_availability WHERE candidate_id=? AND weekday=? AND start_hour<=? AND end_hour>=?",
            (cid, g["weekday"], g["start_hour"], g["end_hour"])).fetchone()]
        if role_gaps and not covered:
            blocking.append("not available for any open shift")
        if blocking:
            not_eligible.append({"candidate": c["name"], "why": blocking})
            continue
        answers = {r["question"]: r["answer"] for r in db.execute(
            "SELECT question, answer FROM candidate_work_style WHERE candidate_id=?", (cid,))}
        fit = [q for q, want in style.items() if answers.get(q) == want or answers.get(q) == "sometimes" and want == "sometimes"]
        part = {
            "availability": WEIGHTS["availability"] * len(covered) / max(1, len(role_gaps)),
            "experience": WEIGHTS["experience"] * min(months, 36) / 36,
            "work_style": WEIGHTS["work_style"] * len(fit) / max(1, len(style)),
        }
        bonus = [x for x in pref if certs[x] in ("verified", "unverified")]
        reasons.append(f"covers {len(covered)}/{len(role_gaps)} open shifts")
        reasons.append(f"{months} months as {pos['role']}")
        reasons.append(f"work style matches {len(fit)}/{len(style)} job demands ({', '.join(fit) or 'none'})")
        if bonus:
            reasons.append("has preferred: " + ", ".join(bonus))
        ranked.append({"candidate_id": cid, "candidate": c["name"], "score": round(sum(part.values()) + 3 * len(bonus), 1),
                       "parts": {k: round(v, 1) for k, v in part.items()}, "reasons": reasons, "flags": flags,
                       "covers": [g["day"] for g in covered]})
    ranked.sort(key=lambda r: -r["score"])
    return ranked, not_eligible


def swipe(db, position_id, candidate_id, side, decision, actor):
    db.execute("INSERT INTO swipes(position_id, candidate_id, side, decision, actor) VALUES (?,?,?,?,?)",
               (position_id, candidate_id, side, decision, actor))
    latest = {r["side"]: r["decision"] for r in db.execute(
        "SELECT side, decision FROM swipes WHERE position_id=? AND candidate_id=? ORDER BY id", (position_id, candidate_id))}
    return latest.get("candidate") == "yes" and latest.get("employer") == "yes"


def decide(db, position_id, candidate_id, decision, decided_by, reason, ticket_id=None, weekly_hours=0, start_on=None):
    """A person decides. A hire also proposes the payroll change, which waits for double approval."""
    if not _is_owner(db, decided_by):
        raise PermissionError("only a plan owner can make hiring decisions")
    if not reason.strip():
        raise ValueError("a job-related reason is required")
    if decision == "hire":
        c = db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        blocking = [x for x in json.loads(db.execute("SELECT required_certs FROM positions WHERE id=?",
                    (position_id,)).fetchone()[0]) if _cert_status(db, candidate_id, x, start_on) != "verified"]
        if blocking:
            raise ValueError(f"cannot hire {c['name']} until these are verified: {', '.join(blocking)}")
    db.execute("INSERT INTO hire_decisions(position_id, candidate_id, decision, decided_by, reason, ticket_id) "
               "VALUES (?,?,?,?,?,?)", (position_id, candidate_id, decision, decided_by, reason, ticket_id))
    if decision != "hire":
        return None
    pos = db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
    c = db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    weekly = round(pos["hourly_cents"] * weekly_hours)
    return request_money_action(db, "payroll", weekly,
                                f"Add {c['name']} to payroll as {pos['title']}, ${pos['hourly_cents']/100:.2f}/h, "
                                f"about {weekly_hours}h/week, start {start_on}", decided_by, ticket_id)

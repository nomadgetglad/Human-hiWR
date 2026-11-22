"""Factory line (e.g. a pipe manufacturer).

"Training" the AI = the owner writes the procedures (SOPs) and checks for each station. They are stored,
versioned and approved. When a worker asks a question, HiWR finds the matching SOP text and the local
model answers from that text only, citing station and version. No model fine-tuning; changing a
procedure is editing the SOP, and every check records which version it was done under.

Check-through: each pipe must pass each station's checks, in line order, before it moves on. A failed
check puts the pipe on hold and logs a corrective action.

Quotas: pipes per worker-hour at each station (from real output) -> workers needed for a target,
and labor cost per pipe at pay rate.

Rotation: workers move between the stations they are certified for (task grants station_<name>),
no more than 2 days running on one station, spreading the work evenly.

Sections: my_station (everyone), foreman_board (task 'foreman'), hr_roll_call (task 'hr_manager').
A foreman doesn't see HR or roll call unless separately certified for it.
"""
import json, math, re
from collections import defaultdict
from hiwr_access import can_do, level
from hiwr_processes import log_corrective_action

SECTION_TASK = {"my_station": None, "foreman_board": "foreman", "hr_roll_call": "hr_manager"}


def section_allowed(db, member, section, on_day):
    task = SECTION_TASK[section]
    return task is None or can_do(db, member, task, on_day)


def open_section(db, member, section, on_day):
    if not section_allowed(db, member, section, on_day):
        db.execute("INSERT INTO access_log(actor, role, action) VALUES (?,?,?)", (member, str(level(db, member)), f"denied:{section}"))
        raise PermissionError(f"{member} can't open {section} (needs {SECTION_TASK[section]})")
    return True


# ---------------- SOPs and teaching ----------------

def publish_sop(db, station, seq, instruction, checks, approved_by):
    v = (db.execute("SELECT MAX(version) FROM sop_steps WHERE station=?", (station,)).fetchone()[0] or 0) + 1
    db.execute("INSERT INTO sop_steps(station, seq, version, instruction, checks, approved_by) VALUES (?,?,?,?,?,?)",
               (station, seq, v, instruction, json.dumps(checks), approved_by))
    return v


def current_sop(db, station):
    return db.execute("SELECT * FROM sop_steps WHERE station=? ORDER BY version DESC LIMIT 1", (station,)).fetchone()


def line_order(db):
    return [r["station"] for r in db.execute(
        "SELECT station, MIN(seq) s FROM sop_steps GROUP BY station ORDER BY s")]


def teach(db, question, station=None):
    """Find the SOP passages that answer a question. In the app, these passages (and only these)
    go to the local model to phrase the answer; here we return them with the citation."""
    words = set(re.findall(r"[a-z]+", question.lower())) - {"the", "a", "what", "how", "do", "i", "is", "to", "on", "of", "for", "my"}
    best = []
    for st in line_order(db):
        if station and st != station:
            continue
        sop = current_sop(db, st)
        for sentence in re.split(r"(?<=\.)\s+", sop["instruction"]):
            score = len(words & set(re.findall(r"[a-z]+", sentence.lower())))
            if score:
                best.append((score, st, sop["version"], sentence))
    best.sort(key=lambda b: -b[0])
    if not best:
        return "Not in the procedures. Ask the foreman; the owner can add it to the SOP."
    _, st, v, text = best[0]
    return f"{text}  [SOP {st} v{v}]"


# ---------------- check-throughs ----------------

def _evaluate(checks, values, spec):
    fails = []
    for c in checks:
        val = values.get(c["name"])
        if c["type"] == "number":
            lo = c.get("min"); hi = c.get("max")
            if isinstance(lo, str): lo = eval(lo, {}, spec)      # e.g. "1.5 * rated_psi" from the unit spec
            if isinstance(hi, str): hi = eval(hi, {}, spec)
            if val is None or (lo is not None and val < lo) or (hi is not None and val > hi):
                fails.append(f"{c['name']}={val} (allowed {lo if lo is not None else '-'}..{hi if hi is not None else '-'})")
        elif c["type"] == "yes_no" and val != c["must"]:
            fails.append(f"{c['name']}={val} (must be {c['must']})")
        elif c["type"] == "photo" and not val:
            fails.append(f"{c['name']} photo missing")
    return fails


def check_through(db, serial, station, member, at, values):
    order = line_order(db)
    done = {r["station"] for r in db.execute("SELECT station FROM unit_checks WHERE serial=? AND passed=1", (serial,))}
    held = db.execute("SELECT 1 FROM unit_checks c WHERE serial=? AND passed=0 AND NOT EXISTS (SELECT 1 FROM unit_checks d "
                      "WHERE d.serial=c.serial AND d.station=c.station AND d.passed=1 AND d.id>c.id)", (serial,)).fetchone()
    if held and station not in {r["station"] for r in db.execute(
            "SELECT station FROM unit_checks WHERE serial=? AND passed=0", (serial,))}:
        raise PermissionError(f"{serial} is on hold from a failed check")
    for prev in order[:order.index(station)]:
        if prev not in done:
            raise PermissionError(f"{serial} hasn't passed {prev} yet")
    if not can_do(db, member, f"station_{station}", at[:10]):
        raise PermissionError(f"{member} isn't certified for {station}")
    sop = current_sop(db, station)
    spec = json.loads(db.execute("SELECT spec FROM units WHERE serial=?", (serial,)).fetchone()["spec"])
    fails = _evaluate(json.loads(sop["checks"]), values, spec)
    db.execute("INSERT INTO unit_checks(serial, station, sop_version, member, at, values_json, passed, failures) VALUES (?,?,?,?,?,?,?,?)",
               (serial, station, sop["version"], member, at, json.dumps(values), int(not fails), "; ".join(fails) or None))
    if fails:
        log_corrective_action(db, at, None, f"{serial} failed {station}", "; ".join(fails), "HOLD pipe; foreman to decide repair or scrap", member)
    return {"serial": serial, "station": station, "sop": f"v{sop['version']}", "passed": not fails, "failures": fails}


# ---------------- quotas and labor cost ----------------

def throughput(db, from_day, to_day):
    rows = db.execute("SELECT station, SUM(hours) h, SUM(units) u FROM station_output WHERE day BETWEEN ? AND ? GROUP BY station",
                      (from_day, to_day)).fetchall()
    return {r["station"]: r["u"] / r["h"] for r in rows if r["h"]}


def staffing_quota(db, target_units_per_day, shift_hours, from_day, to_day, avg_rate_cents):
    rate = throughput(db, from_day, to_day)
    out, total_hours = [], 0
    for st in line_order(db):
        per_worker_day = rate[st] * shift_hours
        need = math.ceil(target_units_per_day / per_worker_day)
        total_hours += need * shift_hours
        out.append({"station": st, "pipes_per_worker_hour": round(rate[st], 2), "workers": need,
                    "capacity": round(need * per_worker_day)})
    cost = total_hours * avg_rate_cents
    return out, {"workers": sum(o["workers"] for o in out), "labor_cents_per_day": round(cost),
                 "labor_cents_per_pipe": round(cost / target_units_per_day)}


def actual_labor_cost(db, from_day, to_day, rates):
    cents = sum(r["hours"] * rates[r["member"]] for r in db.execute(
        "SELECT member, hours FROM station_output WHERE day BETWEEN ? AND ?", (from_day, to_day)))
    finished = db.execute("SELECT SUM(units) FROM station_output WHERE day BETWEEN ? AND ? AND station=?",
                          (from_day, to_day, line_order(db)[-1])).fetchone()[0]
    return round(cents), finished, round(cents / finished) if finished else None


# ---------------- rotation ----------------

def rotate(db, members, quota, days, on_day, max_run=2):
    """Assign each day's station slots from certified workers, avoiding more than max_run days in a row
    on one station and favouring the stations each person has done least."""
    history = defaultdict(list)
    count = defaultdict(lambda: defaultdict(int))
    plan = []
    for d in days:
        today, used = {}, set()
        slots = [(q["station"], i) for q in quota for i in range(q["workers"])]
        slots.sort(key=lambda s: sum(1 for m in members if can_do(db, m, f"station_{s[0]}", on_day)))  # scarce skills first
        for st, _ in slots:
            cands = [m for m in members if m not in used and can_do(db, m, f"station_{st}", on_day)
                     and history[m][-max_run:] != [st] * max_run]
            if not cands:
                cands = [m for m in members if m not in used and can_do(db, m, f"station_{st}", on_day)]
            if not cands:
                today.setdefault(st, []).append("UNFILLED")
                continue
            m = min(cands, key=lambda x: (count[x][st], len([s for s in count[x].values() if s])))
            used.add(m); today.setdefault(st, []).append(m)
            count[m][st] += 1
        for m in members:
            history[m].append(next((s for s, ms in today.items() if m in ms), "off"))
        plan.append((d, today))
    return plan, count

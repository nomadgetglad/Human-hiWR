"""Human UI server: a small FastAPI layer over the hiwr/ sample modules.

Run from the ui/ folder:   python -m uvicorn server.app:app --port 8000
Then open http://localhost:8000 (after `npm run build` in ui/web), or use `npm run dev` for live editing.

Rules this layer keeps:
- Role filtering happens here, on the server. The browser never receives data above the member's level.
- Nothing changes on a single click. Every change is a proposal that comes back as a confirm card
  (what will happen, who it affects). The member presses Confirm, Fix (edit and re-propose) or Cancel.
- Money only moves through the two-approver rule in hiwr_contacts.approve().
- After a confirmed change the member is asked for the authority check-in: did it work, and did it
  have the intended consequence.

Sign-in is a sample switcher (X-Member header). Real sign-in comes later.
"""
import itertools
import threading
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import seed
from hiwr_access import level, can_see, LEVEL_NAMES
from hiwr_contacts import approve, money_action_status, is_certified, check_in, my_day, update_assignment
from hiwr_staff import staff_section, decide_hr, find_same_night, send_offers
from hiwr_prep import prep_plan, make_batches
from hiwr_stew import portions_left, cost_card, check_order_note
from hiwr_warehouse import pull_list, record_pull

app = FastAPI(title="Human (HiWR) sample UI")
LOCK = threading.Lock()
STATE = {}
PROPOSALS = {}
_ids = itertools.count(1)
PREP_ITEM = "fried tortilla chips"


def reset():
    with LOCK:
        STATE.clear()
        PROPOSALS.clear()
        STATE["db"] = seed.build()
        STATE["clock"] = 16          # prep clock hour, sample


reset()


def db():
    return STATE["db"]


def now_iso(minute=0):
    return f"{seed.TODAY}T{STATE['clock']:02d}:{minute:02d}"


def member(x_member):
    m = next((m for m in seed.MEMBERS if m["id"] == x_member), None)
    if not m:
        raise HTTPException(401, "Pick who is signed in")
    return {**m, "level": level(db(), m["id"]), "level_name": LEVEL_NAMES[level(db(), m["id"])]}


def need(m, lvl, what):
    if m["level"] < lvl:
        raise HTTPException(403, f"{what} needs level {lvl} ({LEVEL_NAMES[lvl]}). You are {m['level_name']}.")


# ---------------- session ----------------

@app.get("/api/members")
def members():
    return [{k: m[k] for k in ("id", "name", "title", "language")} for m in seed.MEMBERS]


@app.get("/api/me")
def me(x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        menu = [
            {"id": "staff", "label": "Staff"},
            {"id": "inventory", "label": "Inventory", "children": ["warehouse", "stew", "lab"]},
            {"id": "prep", "label": "My station"},
            {"id": "cs", "label": "Customer service"},
        ]
        if m["level"] >= 3:
            menu.append({"id": "accounting", "label": "Accounting"})
        open_cards = sum(1 for p in PROPOSALS.values() if p["actor"] == m["id"] and p["state"] == "open")
        return {**m, "menu": menu, "today": seed.TODAY, "clock": STATE["clock"], "open_cards": open_cards}


@app.post("/api/demo/reset")
def demo_reset():
    reset()
    return {"ok": True}


class Clock(BaseModel):
    hour: int


@app.post("/api/demo/clock")
def demo_clock(c: Clock):
    """Move the sample clock forward (16 to 22). POS sales up to that hour are added."""
    with LOCK:
        if not (STATE["clock"] <= c.hour <= 22):
            raise HTTPException(400, f"The clock only moves forward, from {STATE['clock']}:00 to 22:00")
        STATE["clock"] = c.hour
        seed.add_today_sales(db(), c.hour)
        return {"clock": c.hour}


# ---------------- Top bar: notifications + one key number that depends on the level ----------------

def _price(d, dish):
    r = d.execute("SELECT price_cents FROM recipes WHERE name=?", (dish,)).fetchone()
    return (r["price_cents"] if r and r["price_cents"] else seed.SIDE_PRICES.get(dish, 0))


def _revenue(d, day, upto=24):
    return sum(r["qty"] * _price(d, r["dish"]) for r in d.execute("SELECT dish, qty FROM dish_sales WHERE day=? AND hour<?", (day, upto)))


def _forecast_revenue(d, upto=24):
    """Same weekday, last 4 weeks, dinner scaled by tonight's reservations vs usual."""
    from datetime import date, timedelta
    scale = seed.RESERVATIONS / seed.USUAL_RESERVATIONS
    total = 0.0
    for w in range(1, 5):
        day = (date.fromisoformat(seed.TODAY) - timedelta(weeks=w)).isoformat()
        for r in d.execute("SELECT dish, hour, qty FROM dish_sales WHERE day=? AND hour<?", (day, upto)):
            total += r["qty"] * _price(d, r["dish"]) * (scale if r["hour"] >= 17 else 1.0)
    return round(total / 4)


def _labor(d):
    rows = d.execute("SELECT s.member, s.hourly_cents, v.start_hour, v.end_hour FROM staff s "
                     "JOIN staff_availability v ON v.member=s.member WHERE v.day=?", (seed.TODAY,)).fetchall()
    hours = sum(r["end_hour"] - r["start_hour"] for r in rows)
    cents = sum((r["end_hour"] - r["start_hour"]) * r["hourly_cents"] for r in rows)
    return len(rows), hours, cents


@app.get("/api/topbar")
def topbar(x_member: str = Header(None)):
    """What the bar at the top shows. Each level gets only the numbers it is allowed to see."""
    with LOCK:
        m = member(x_member)
        d, lvl, h = db(), m["level"], STATE["clock"]
        stats, notes = [], []
        if lvl >= 4:          # account holder: revenue
            rev, fc_now, fc_day = _revenue(d, seed.TODAY, h), _forecast_revenue(d, h), _forecast_revenue(d)
            stats = [{"label": "Revenue today", "value": rev, "format": "usd",
                      "detail": f"{round(rev / fc_now * 100) if fc_now else 0}% of forecast so far"},
                     {"label": "Forecast tonight", "value": fc_day, "format": "usd", "detail": f"{seed.RESERVATIONS} reservations vs usual {seed.USUAL_RESERVATIONS}"}]
        elif lvl >= 2:        # managers: labor. Finance level sees dollars; level 2 sees % and hours only
            n, hours, cents = _labor(d)
            fc_day = _forecast_revenue(d)
            pct = round(cents / fc_day * 100, 1) if fc_day else 0
            v = staff_section(d, seed.TODAY)
            short = sum(r["short"] for r in v["roles"])
            stats = [{"label": "Kitchen labor tonight", "value": pct, "format": "pct", "detail": f"of forecast sales, {hours:g} h"},
                     {"label": "On shift", "value": n, "format": "int", "detail": f"short {short}" if short else "fully staffed"}]
            if lvl >= 3:
                stats.insert(1, {"label": "Labor cost tonight", "value": cents, "format": "usd", "detail": f"{n} people"})
        else:                 # employees: their tasks
            tasks = my_day(d, m["id"], seed.TODAY)
            open_t = [t for t in tasks if t["status"] != "done"]
            stats = [{"label": "My tasks", "value": len(open_t), "format": "int", "detail": f"{len(tasks) - len(open_t)} done"}]

        # notifications, filtered by level
        if lvl < 2:
            for t in my_day(d, m["id"], seed.TODAY):
                if t["status"] != "done":
                    notes.append({"kind": "task", "text": t["title"], "go": "tasks", "id": t["id"]})
        if d.execute("SELECT 1 FROM staff WHERE member=? AND role='cook'", (m["id"],)).fetchone() or lvl >= 2:
            p = prep_plan(d, PREP_ITEM, now_iso(), seed.RESERVATIONS, seed.USUAL_RESERVATIONS)
            if p["fry_batches_now"]:
                notes.append({"kind": "prep", "text": f"Fryer: fry {p['fry_batches_now']} batch(es) of chips now", "go": "prep"})
        if lvl >= 1:
            for r in staff_section(d, seed.TODAY)["roles"]:
                if r["short"]:
                    notes.append({"kind": "staff", "text": f"Tonight short {r['short']} {r['role']}", "go": "staff"})
        if lvl >= 2:
            n = d.execute("SELECT COUNT(*) FROM hr_requests h WHERE NOT EXISTS (SELECT 1 FROM hr_request_events e WHERE e.request_id=h.id)").fetchone()[0]
            if n:
                notes.append({"kind": "hr", "text": f"{n} HR request{'s' if n > 1 else ''} waiting", "go": "staff"})
            for r in portions_left(d):
                if r["portions"] < 30:
                    notes.append({"kind": "86", "text": f"{r['dish']}: {r['portions']} portions left (limited by {r['limited_by']})", "go": "stew"})
            for t in my_day(d, m["id"], seed.TODAY):
                if t["status"] != "done":
                    notes.append({"kind": "task", "text": t["title"], "go": "tasks", "id": t["id"]})
        for t in d.execute("SELECT * FROM tickets WHERE status!='closed' ORDER BY priority"):
            if lvl >= 1:
                notes.append({"kind": "ticket", "text": f"{t['id']} {t['status']}: {t['subject']}", "go": "stew"})
        if lvl >= 3:
            for a in d.execute("SELECT * FROM money_actions"):
                st = money_action_status(d, a["id"])
                if st.startswith("PENDING"):
                    notes.append({"kind": "money", "text": f"{a['scope'].title()} ${a['amount_cents'] / 100:,.2f} {st.lower()}", "go": "accounting"})
        return {"role_view": "owner" if lvl >= 4 else "manager" if lvl >= 2 else "employee", "stats": stats, "notifications": notes}


@app.get("/api/tasks")
def tasks(x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        return my_day(db(), m["id"], seed.TODAY)


# ---------------- Staff ----------------

@app.get("/api/staff")
def staff(x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        d = db()
        if m["level"] < 1:
            # Staff level: own shift and own requests only
            shift = d.execute("SELECT start_hour, end_hour FROM staff_availability WHERE member=? AND day=?",
                              (m["id"], seed.TODAY)).fetchone()
            mine = [dict(r) for r in d.execute("SELECT kind, from_day, to_day, detail FROM hr_requests WHERE member=?", (m["id"],))]
            return {"view": "own", "day": seed.TODAY, "my_shift": dict(shift) if shift else None, "my_requests": mine}
        v = staff_section(d, seed.TODAY)
        hr = [dict(r) for r in d.execute(
            "SELECT * FROM hr_requests h WHERE NOT EXISTS (SELECT 1 FROM hr_request_events e WHERE e.request_id=h.id) ORDER BY at")]
        callouts = [dict(r) for r in d.execute(
            "SELECT h.member, h.detail, e.actor, e.at FROM hr_requests h JOIN hr_request_events e ON e.request_id=h.id "
            "WHERE h.kind='time_off' AND h.from_day<=? AND h.to_day>=? AND e.status='approved'", (seed.TODAY, seed.TODAY))]
        offers = [dict(r) for r in d.execute(
            "SELECT c.name, o.role, o.sent_at, o.hold_until, o.answer FROM shift_offers o JOIN candidates c ON c.id=o.candidate_id")]
        return {"view": "team", **v, "hr_requests": hr, "callouts": callouts, "offers": offers}


@app.get("/api/staff/fill")
def staff_fill(role: str, x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        need(m, 2, "Finding cover")
        rule = db().execute("SELECT * FROM staffing_rules WHERE role=?", (role,)).fetchone()
        if not rule:
            raise HTTPException(404, f"No staffing rule for {role}")
        starts, ends = f"{seed.TODAY}T{int(rule['start_hour']):02d}:00", f"{seed.TODAY}T{int(rule['end_hour']):02d}:00"
        cands = find_same_night(db(), role, starts, ends, now_iso())
        # phone numbers stay on the server; the chef sees names, experience, reliability and language
        return {"role": role, "starts_at": starts, "ends_at": ends,
                "candidates": [{k: c[k] for k in ("candidate_id", "name", "months", "reliability", "language")} for c in cands]}


# ---------------- Prep (station) ----------------

@app.get("/api/prep")
def prep(x_member: str = Header(None)):
    with LOCK:
        member(x_member)
        d = db()
        p = prep_plan(d, PREP_ITEM, now_iso(), seed.RESERVATIONS, seed.USUAL_RESERVATIONS)
        item = dict(d.execute("SELECT * FROM prep_items WHERE item=?", (PREP_ITEM,)).fetchone())
        batches = [dict(r) for r in d.execute("SELECT made_at, qty, member FROM prep_batches WHERE item=? ORDER BY made_at DESC LIMIT 8", (PREP_ITEM,))]
        left = d.execute("SELECT on_hand FROM materials WHERE material=?", (item["input_material"],)).fetchone()[0]
        return {**p, "station": item["station"], "clock": STATE["clock"], "hold_minutes": item["hold_minutes"],
                "batch_qty": item["batch_qty"], "batch_minutes": item["batch_minutes"], "recent_batches": batches,
                "input_left": left, "input_material": item["input_material"], "input_per_batch": item["input_qty"],
                "reservations": seed.RESERVATIONS, "usual_reservations": seed.USUAL_RESERVATIONS}


# ---------------- Inventory ----------------

@app.get("/api/inventory/stew")
def stew(x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        d = db()
        out = {"portions": portions_left(d), "shows_costs": can_see(d, m["id"], "order_costs")}
        if out["shows_costs"]:
            out["cost_cards"] = [{k: v for k, v in cost_card(d, r["name"]).items() if k != "lines"}
                                 | {"dish": r["name"]} for r in d.execute("SELECT name FROM recipes WHERE kind='dish'")]
        out["tickets"] = [{"id": t["id"], "type": t["type"], "status": t["status"], "subject": t["subject"]}
                          for t in d.execute("SELECT * FROM tickets WHERE status!='closed' ORDER BY priority")]
        return out


@app.get("/api/inventory/warehouse")
def warehouse(x_member: str = Header(None)):
    with LOCK:
        member(x_member)
        return {"pull_list": pull_list(db(), seed.TODAY)}


@app.get("/api/inventory/lab")
def lab(x_member: str = Header(None)):
    with LOCK:
        member(x_member)
        return {"jobs": [], "note": "Lab is for long jobs (house builds: phases, inspections, subs). "
                                    "This restaurant has none. The screens come in a later pass, wired to hiwr_lab.py."}


# ---------------- Customer service ----------------

@app.get("/api/cs/dishes")
def cs_dishes(x_member: str = Header(None)):
    with LOCK:
        member(x_member)
        return [r["name"] for r in db().execute("SELECT name FROM recipes WHERE kind='dish' ORDER BY name")]


class NoteCheck(BaseModel):
    dish: str
    note: str


@app.post("/api/cs/check")
def cs_check(c: NoteCheck, x_member: str = Header(None)):
    with LOCK:
        member(x_member)
        text = check_order_note(db(), c.dish, c.note)
        return {"ok": text.startswith("OK"), "text": text}


# ---------------- Accounting ----------------

@app.get("/api/accounting/money-actions")
def money_actions(x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        need(m, 3, "Accounting")
        d = db()
        rows = []
        for a in d.execute("SELECT * FROM money_actions ORDER BY id"):
            appr = [dict(r) for r in d.execute("SELECT approver, decision, at FROM approvals WHERE money_action_id=?", (a["id"],))]
            rows.append({**dict(a), "status": money_action_status(d, a["id"]), "approvals": appr,
                         "i_can_approve": is_certified(d, m["id"], a["scope"]) and m["id"] != a["requested_by"]
                                          and all(x["approver"] != m["id"] for x in appr)})
        return rows


# ---------------- Confirm cards ----------------

class Propose(BaseModel):
    kind: str
    params: dict


def _card(m, kind, params):
    """Validate a proposed change and describe it. Nothing is written here."""
    d = db()
    if kind == "fry":
        n = int(params.get("batches", 0))
        if not 1 <= n <= 10:
            raise HTTPException(400, "Batches must be between 1 and 10")
        p = d.execute("SELECT * FROM prep_items WHERE item=?", (PREP_ITEM,)).fetchone()
        left = d.execute("SELECT on_hand FROM materials WHERE material=?", (p["input_material"],)).fetchone()[0]
        use = n * p["input_qty"]
        if use > left:
            raise HTTPException(400, f"Only {left:g} {p['input_material']} left, {n} batches need {use:g}")
        return {"title": f"Fry {n} batch{'es' if n > 1 else ''} of chips",
                "lines": [f"Makes {n * p['batch_qty']:g} {p['unit']}, ready in {p['batch_minutes']} min",
                          f"Takes {use:g} {p['input_material']} out of Stew inventory ({left - use:g} left after)",
                          f"Good until {p['hold_minutes']} min after frying, then counted as waste"],
                "fix": [{"field": "batches", "label": "Batches", "type": "number", "min": 1, "max": 10}]}
    if kind == "hr_decide":
        need(m, 2, "Deciding HR requests")
        r = d.execute("SELECT * FROM hr_requests WHERE id=?", (params.get("request_id"),)).fetchone()
        if not r:
            raise HTTPException(404, "No such request")
        status = params.get("status")
        if status not in ("approved", "denied", "answered"):
            raise HTTPException(400, "Status must be approved, denied or answered")
        when = f" {r['from_day']} to {r['to_day']}" if r["from_day"] else ""
        lines = [f"{r['member']}: {r['kind'].replace('_', ' ')}{when}", f"Marked {status} by {m['id']}, logged, never overwritten"]
        if r["kind"] == "time_off" and status == "approved":
            lines.append(f"Removes {r['member']} from the schedule on those days")
        verb = {"approved": "Approve", "denied": "Deny", "answered": "Close as answered:"}[status]
        return {"title": f"{verb} {r['kind'].replace('_', ' ')} for {r['member']}",
                "lines": lines, "fix": [{"field": "note", "label": "Note to them", "type": "text"}]}
    if kind == "send_offers":
        need(m, 2, "Finding cover")
        role = params.get("role")
        rule = d.execute("SELECT * FROM staffing_rules WHERE role=?", (role,)).fetchone()
        starts = f"{seed.TODAY}T{int(rule['start_hour']):02d}:00"
        ends = f"{seed.TODAY}T{int(rule['end_hour']):02d}:00"
        cands = find_same_night(d, role, starts, ends, now_iso())[: int(params.get("top", 3))]
        rate = int(params.get("hourly_cents", 1600))
        return {"title": f"Text {len(cands)} people for tonight's {role} shift",
                "lines": [f"To: {', '.join(c['name'] for c in cands)} (only those with text consent)",
                          f"{int(rule['start_hour'])}:00 to {int(rule['end_hour'])}:00 at ${rate / 100:.2f}/h, held 30 min",
                          "First yes takes the shift; the others get 'already filled, thanks'",
                          "Sample: no real texts are sent"],
                "fix": [{"field": "top", "label": "How many to text", "type": "number", "min": 1, "max": 5},
                        {"field": "hourly_cents", "label": "Rate (cents per hour)", "type": "number", "min": 725, "max": 6000}]}
    if kind == "pull":
        p = d.execute("SELECT * FROM products WHERE sku=?", (params.get("sku"),)).fetchone()
        qty = int(params.get("qty", 0))
        if not p or qty < 1 or qty > p["back_stock"]:
            raise HTTPException(400, f"Pull must be 1 to {p['back_stock'] if p else 0}")
        return {"title": f"Pull {qty} {p['name']} to the floor",
                "lines": [f"Floor {p['shelf']} -> {p['shelf'] + qty}, back stock {p['back_stock']} -> {p['back_stock'] - qty}",
                          "Logged as a stock move under your name"],
                "fix": [{"field": "qty", "label": "Quantity", "type": "number", "min": 1, "max": p["back_stock"]}]}
    if kind == "money_decide":
        need(m, 3, "Money approvals")
        a = d.execute("SELECT * FROM money_actions WHERE id=?", (params.get("money_action_id"),)).fetchone()
        if not a:
            raise HTTPException(404, "No such money action")
        decision = params.get("decision")
        if decision not in ("approve", "reject"):
            raise HTTPException(400, "Decision must be approve or reject")
        if not is_certified(d, m["id"], a["scope"]):
            raise HTTPException(403, f"You are not certified for {a['scope']} approvals")
        if a["requested_by"] == m["id"]:
            raise HTTPException(403, "You asked for this one, so you cannot approve it")
        n = d.execute("SELECT COUNT(*) FROM approvals WHERE money_action_id=? AND decision='approve'", (a["id"],)).fetchone()[0]
        after = ("APPROVED, money can move" if n + 1 >= 2 else "still PENDING, needs one more approver") if decision == "approve" else "REJECTED, nothing moves"
        return {"title": f"{decision.title()} {a['scope']} ${a['amount_cents'] / 100:,.2f}",
                "lines": [a["description"], f"Requested by {a['requested_by']}", f"After this: {after}"],
                "money": True, "fix": []}
    if kind == "task_done":
        t = d.execute("SELECT * FROM assignments WHERE id=? AND assignee=?", (params.get("assignment_id"), m["id"])).fetchone()
        if not t:
            raise HTTPException(404, "That task is not on your list")
        return {"title": f"Mark done: {t['title']}", "lines": [f"Assigned by {t['assigned_by']}", "They see it as done, with your name and the time"],
                "fix": [{"field": "note", "label": "Note (optional)", "type": "text"}]}
    raise HTTPException(400, f"Unknown change: {kind}")


def _apply(m, kind, params):
    d = db()
    if kind == "fry":
        make_batches(d, PREP_ITEM, int(params["batches"]), now_iso(2), m["id"])
        return f"Fried {params['batches']} batch(es) at {STATE['clock']}:02"
    if kind == "hr_decide":
        decide_hr(d, params["request_id"], params["status"], m["id"], now_iso(), params.get("note"))
        return f"Request {params['status']}"
    if kind == "send_offers":
        rule = d.execute("SELECT * FROM staffing_rules WHERE role=?", (params["role"],)).fetchone()
        starts, ends = f"{seed.TODAY}T{int(rule['start_hour']):02d}:00", f"{seed.TODAY}T{int(rule['end_hour']):02d}:00"
        cands = find_same_night(d, params["role"], starts, ends, now_iso())
        ids, hold = send_offers(d, cands, params["role"], starts, ends, int(params.get("hourly_cents", 1600)), now_iso(),
                                top=int(params.get("top", 3)))
        return f"{len(ids)} offers recorded, held until {hold[11:]}"
    if kind == "pull":
        record_pull(d, params["sku"], int(params["qty"]), m["id"], now_iso())
        return "Pull logged"
    if kind == "task_done":
        update_assignment(d, params["assignment_id"], "done", m["id"], params.get("note"))
        return "Marked done"
    if kind == "money_decide":
        return approve(d, params["money_action_id"], m["id"], params["decision"])


@app.post("/api/proposals")
def propose(p: Propose, x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        card = _card(m, p.kind, p.params)
        pid = next(_ids)
        PROPOSALS[pid] = {"id": pid, "kind": p.kind, "params": p.params, "actor": m["id"], "state": "open", **card}
        return PROPOSALS[pid]


@app.post("/api/proposals/{pid}/confirm")
def confirm(pid: int, x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        p = PROPOSALS.get(pid)
        if not p or p["actor"] != m["id"] or p["state"] != "open":
            raise HTTPException(404, "This card is no longer open")
        _card(m, p["kind"], p["params"])          # re-check: data may have changed since the card was made
        result = _apply(m, p["kind"], p["params"])
        db().commit()
        p.update(state="confirmed", result=result)
        return p


@app.post("/api/proposals/{pid}/cancel")
def cancel(pid: int, x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        p = PROPOSALS.get(pid)
        if p and p["actor"] == m["id"] and p["state"] == "open":
            p["state"] = "cancelled"
        return {"ok": True}


class CheckIn(BaseModel):
    works: bool
    intended: bool
    note: str | None = None


@app.post("/api/proposals/{pid}/checkin")
def checkin(pid: int, c: CheckIn, x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        p = PROPOSALS.get(pid)
        if not p or p["state"] != "confirmed":
            raise HTTPException(404, "Only confirmed changes get a check-in")
        status = check_in(db(), f"{p['kind']}#{pid}: {p['title']}", m["id"], c.works, c.intended, c.note)
        db().commit()
        p["checkin"] = status
        return {"status": status}


# ---------------- the built web app ----------------

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")

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

Sign-in: your own account needs a real sign-in (an authenticator app, see hiwr_auth.py). The session is an
HttpOnly cookie, and every POST must carry the X-Human header so another site can't post with that cookie.
The test restaurant (sample data) keeps the X-Member switcher so demos and tests can jump between people.
"""
import contextvars
import itertools
import os
import threading
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import seed
from hiwr_access import level, can_see, LEVEL_NAMES
from hiwr_contacts import approve, money_action_status, is_certified, check_in, my_day, update_assignment
from hiwr_staff import staff_section, decide_hr, find_same_night, send_offers
from hiwr_prep import prep_plan, make_batches
from hiwr_stew import portions_left, cost_card, check_order_note
from hiwr_warehouse import pull_list, record_pull
from hiwr_roster import add_member, check_new_member, get_member, roster
import hiwr_auth as auth
import hiwr_idintake as idin
from hiwr_vault import Vault

app = FastAPI(title="Human (HiWR) sample UI")
LOCK = threading.Lock()
STATE = {}
PROPOSALS = {}
_ids = itertools.count(1)
PREP_ITEM = "fried tortilla chips"
PENDING_ENROLL = {}      # (member, setup code id) -> secret shown on the phone, until the first 6-digit code matches
COOKIE = "human_session"
REQ = contextvars.ContextVar("req", default={})


class RequestInfo:
    """Plain ASGI middleware: keeps the session cookie, device and address for this request, and blocks
    cross-site POSTs (a POST must carry the X-Human header, which another site can't add)."""
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        h = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        cookies = {}
        for part in h.get("cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k:
                cookies[k] = v
        bearer = h.get("authorization", "")
        token = bearer[7:] if bearer.lower().startswith("bearer ") else cookies.get(COOKIE)
        info = {"token": token, "device": auth.device_label(h.get("user-agent")),
                "ip": (scope.get("client") or ("", 0))[0], "https": scope.get("scheme") == "https"}
        if (scope["method"] == "POST" and scope["path"].startswith("/api/") and STATE.get("auth")
                and h.get("x-human") != "1"):
            return await JSONResponse({"detail": "Missing X-Human header"}, 403)(scope, receive, send)
        tok = REQ.set(info)
        try:
            await self.inner(scope, receive, send)
        finally:
            REQ.reset(tok)


# Your own data (no test data) is kept in this file; the test restaurant lives in memory only
DB_PATH = os.environ.get("HUMAN_DB", str(Path(__file__).resolve().parents[1] / "human.db"))


def reset(sample=True):
    """sample=True loads the test restaurant (tests and demos), in memory. sample=False opens your own
    account from DB_PATH: empty the first time, and whatever you added since then after that."""
    with LOCK:
        if STATE.get("db") is not None:
            STATE["db"].close()
        STATE.clear()
        PROPOSALS.clear()
        PENDING_ENROLL.clear()
        STATE["db"] = seed.build() if sample else seed.build_blank(DB_PATH)
        STATE["sample"] = sample
        STATE["clock"] = 16          # prep clock hour, sample
        STATE["auth"] = not sample          # your own account always needs a real sign-in
        STATE["vault"] = (Vault.ephemeral() if sample or DB_PATH == ":memory:"
                          else Vault.from_file(str(Path(DB_PATH).with_suffix(".key"))))
        if not sample:
            _first_owner_code()


def _first_owner_code():
    """A new account: the account holder has no authenticator yet, so a setup code is printed in the server
    window. Only someone at the server computer sees it. A new one is printed on every start until it's used."""
    d = STATE["db"]
    if auth.factor(d, seed.OWNER):
        return
    code = auth.issue_setup_code(d, "console", seed.OWNER)
    d.commit()
    STATE["owner_code"] = code          # kept for tests on an in-memory account; never sent to a browser
    print(f"\n  Human: first sign-in for the account holder ({seed.OWNER})\n"
          f"  Setup code: {code[:4]}-{code[4:]}  (good for {auth.SETUP_CODE_HOURS} hours)\n"
          f"  Open the app on your phone, pick your name, choose 'First time', and type this code.\n", flush=True)


# Starts empty (no test data) unless HUMAN_DATA=sample is set before starting the server
reset(sample=os.environ.get("HUMAN_DATA", "blank").lower() == "sample")


def db():
    return STATE["db"]


def now_iso(minute=0):
    return f"{seed.TODAY}T{STATE['clock']:02d}:{minute:02d}"


def member(x_member):
    """Who is asking. A session cookie (authenticator sign-in) always works. The X-Member switcher only
    works on the test restaurant."""
    mid = auth.session_member(db(), REQ.get().get("token"))
    if not mid and not STATE["auth"]:
        mid = x_member
    m = get_member(db(), mid) if mid else None    # active people on the roster only
    if not m:
        raise HTTPException(401, "Sign in first" if STATE["auth"] else "Pick who is signed in")
    return {**m, "level": level(db(), m["id"]), "level_name": LEVEL_NAMES[level(db(), m["id"])]}


def need(m, lvl, what):
    if m["level"] < lvl:
        raise HTTPException(403, f"{what} needs level {lvl} ({LEVEL_NAMES[lvl]}). You are {m['level_name']}.")


# ---------------- session ----------------

@app.get("/api/members")
def members():
    with LOCK:
        return [{k: m[k] for k in ("id", "name", "title", "language")} for m in roster(db())]


@app.get("/api/me")
def me(x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        menu = [{"id": "team", "label": "Team"}] if m["level"] >= 2 else []   # adding people is where it starts
        menu += [
            {"id": "staff", "label": "Staff"},
            {"id": "inventory", "label": "Inventory", "children": ["warehouse", "stew", "lab"]},
            {"id": "prep", "label": "My station"},
            {"id": "cs", "label": "Customer service"},
        ]
        if m["level"] >= 3:
            menu.append({"id": "accounting", "label": "Accounting"})
        open_cards = sum(1 for p in PROPOSALS.values() if p["actor"] == m["id"] and p["state"] == "open")
        return {**m, "menu": menu, "today": seed.TODAY, "clock": STATE["clock"], "open_cards": open_cards,
                "sample": STATE["sample"], "auth_required": STATE["auth"]}


@app.post("/api/demo/reset")
def demo_reset(sample: bool | None = None, x_member: str = Header(None)):
    """Account holder only. ?sample=true switches to the test restaurant, ?sample=false to your own (saved) account.
    Nothing is deleted: your own account file is only opened, never wiped."""
    with LOCK:
        need(member(x_member), 4, "Resetting the data")
        keep = STATE["sample"]
    reset(keep if sample is None else sample)
    return {"ok": True, "sample": STATE["sample"]}


class Clock(BaseModel):
    hour: int


@app.post("/api/demo/clock")
def demo_clock(c: Clock, x_member: str = Header(None)):
    """Move the sample clock forward (16 to 22). POS sales up to that hour are added."""
    with LOCK:
        member(x_member)
        if not (STATE["clock"] <= c.hour <= 22):
            raise HTTPException(400, f"The clock only moves forward, from {STATE['clock']}:00 to 22:00")
        STATE["clock"] = c.hour
        if STATE["sample"]:            # sample POS sales only exist in the test restaurant
            seed.add_today_sales(db(), c.hour)
        return {"clock": c.hour}


# ---------------- Sign-in (authenticator app) ----------------

class SetupStart(BaseModel):
    member_id: str
    setup_code: str


class SetupFinish(SetupStart):
    code: str


class SignIn(BaseModel):
    member_id: str
    code: str


def _req():
    r = REQ.get()
    return {"device": r.get("device"), "ip": r.get("ip")}


def _on_roster(mid):
    if not get_member(db(), mid):
        raise HTTPException(401, "That code doesn't work")


def _set_cookie(resp, token, exp):
    resp.set_cookie(COOKIE, token, max_age=auth.SESSION_HOURS * 3600, httponly=True, samesite="strict",
                    secure=REQ.get().get("https", False), path="/")


@app.get("/api/auth/status")
def auth_status():
    with LOCK:
        mid = auth.session_member(db(), REQ.get().get("token"))
        return {"auth_required": STATE["auth"], "signed_in": mid, "sample": STATE["sample"]}


@app.post("/api/auth/setup/start")
def auth_setup_start(s: SetupStart):
    """First time on a phone: check the setup code, then hand back a fresh secret to scan.
    Nothing is linked until the first 6-digit code from the app matches."""
    with LOCK:
        _on_roster(s.member_id)
        try:
            r = auth.check_setup_code(db(), s.member_id, s.setup_code, **_req())
        except PermissionError as e:
            raise HTTPException(403, str(e))
        secret = auth.new_secret()
        PENDING_ENROLL[(s.member_id, r["id"])] = secret
        uri = auth.otpauth_uri(secret, s.member_id)
        out = {"secret": secret, "otpauth": uri, "member_id": s.member_id}
        try:
            import segno
            out["qr_svg"] = segno.make(uri, error="m").svg_inline(scale=5, dark="#000", light="#fff")
        except ImportError:
            out["qr_svg"] = None          # pip install segno for the QR; the key can be typed in by hand
        return out


@app.post("/api/auth/setup/finish")
def auth_setup_finish(s: SetupFinish, response: Response):
    with LOCK:
        _on_roster(s.member_id)
        d = db()
        try:
            r = auth.check_setup_code(d, s.member_id, s.setup_code, **_req())
            secret = PENDING_ENROLL.get((s.member_id, r["id"]))
            if not secret:
                raise PermissionError("Start again: type your setup code to get a new QR code")
            fid = auth.enroll(d, STATE["vault"], s.member_id, s.setup_code, secret, s.code, **_req())
        except PermissionError as e:
            raise HTTPException(403, str(e))
        PENDING_ENROLL.pop((s.member_id, r["id"]), None)
        token, exp = auth.start_session_after_enroll(d, s.member_id, fid, **_req())
        d.commit()
        _set_cookie(response, token, exp)
        return {"ok": True, "member_id": s.member_id, "expires_at": exp}


@app.post("/api/auth/signin")
def auth_signin(s: SignIn, response: Response):
    with LOCK:
        _on_roster(s.member_id)
        d = db()
        try:
            token, exp = auth.sign_in(d, STATE["vault"], s.member_id, s.code, **_req())
        except PermissionError as e:
            raise HTTPException(403, str(e))
        d.commit()
        _set_cookie(response, token, exp)
        return {"ok": True, "member_id": s.member_id, "expires_at": exp}


@app.post("/api/auth/signout")
def auth_signout(response: Response):
    with LOCK:
        auth.sign_out(db(), REQ.get().get("token"), **_req())
        db().commit()
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}


@app.get("/api/auth/history")
def auth_history(member_id: str | None = None, x_member: str = Header(None)):
    """Enrollment and sign-in history (method, device, time). Your own always; someone else's if you could
    issue them a setup code (a level above theirs, or the account holder). For the settings page."""
    with LOCK:
        m = member(x_member)
        target = member_id or m["id"]
        if target != m["id"] and not auth.can_issue(db(), m["id"], target):
            raise HTTPException(403, "You can see your own sign-in history, and people below your level")
        return {"member_id": target, **auth.status(db(), target), "events": auth.history(db(), target)}


# ---------------- Top bar: notifications + one key number that depends on the level ----------------

def _price(d, dish):
    r = d.execute("SELECT price_cents FROM recipes WHERE name=?", (dish,)).fetchone()
    return (r["price_cents"] if r and r["price_cents"] else seed.SIDE_PRICES.get(dish, 0))


def _revenue(d, day, upto=24):
    return sum(r["qty"] * _price(d, r["dish"]) for r in d.execute("SELECT dish, qty FROM dish_sales WHERE day=? AND hour<?", (day, upto)))


def _forecast_revenue(d, upto=24):
    """Same weekday, last 4 weeks, dinner scaled by tonight's reservations vs usual."""
    from datetime import date, timedelta
    scale = seed.RESERVATIONS / seed.USUAL_RESERVATIONS if STATE["sample"] else 1.0
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
                     {"label": "Forecast tonight", "value": fc_day, "format": "usd",
                      "detail": f"{seed.RESERVATIONS} reservations vs usual {seed.USUAL_RESERVATIONS}" if STATE["sample"]
                      else ("same day, last 4 weeks" if fc_day else "no sales history yet")}]
            if not fc_now:
                stats[0]["detail"] = "no sales yet" if not rev else "no forecast yet"
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
        if _has_prep(d) and (d.execute("SELECT 1 FROM staff WHERE member=? AND role='cook'", (m["id"],)).fetchone() or lvl >= 2):
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
            for x in idin.expiring(d):
                notes.append({"kind": "id", "text": f"{x['member']}: ID {'expired' if x['days'] < 0 else 'expires in ' + str(x['days']) + ' days'}",
                              "go": "team"})
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


# ---------------- Team (roster) ----------------

@app.get("/api/team")
def team(x_member: str = Header(None)):
    """Who works here. Managers and up see the roster; pay only at Finance level and up."""
    with LOCK:
        m = member(x_member)
        need(m, 2, "The team roster")
        d = db()
        roles = sorted({r[0] for r in d.execute("SELECT role FROM staffing_rules UNION SELECT role FROM staff")}
                       | {"cook", "dishwasher", "server", "host", "bartender", "prep"})
        rows = roster(d, show_pay=m["level"] >= 3)
        for r in rows:
            st = auth.status(d, r["id"])
            r["sign_in"] = {k: st[k] for k in ("enrolled", "last_sign_in", "setup_code_open")}
            r["can_issue_code"] = auth.can_issue(d, m["id"], r["id"])
            r["id_card"] = idin.id_status(d, r["id"])
            r["can_enter_id"] = idin.can_enter(d, m["id"], r["id"])
        return {"members": rows, "can_set_level": m["level"] >= 4,
                "can_set_pay": m["level"] >= 3, "can_view_id": idin.can_view(d, m["id"]), "roles": roles,
                "doc_types": idin.DOC_TYPES}


@app.get("/api/team/{member_id}/id")
def team_id(member_id: str, x_member: str = Header(None)):
    """ID details: Finance and the account holder only. Every opening is logged."""
    with LOCK:
        m = member(x_member)
        try:
            out = idin.view_details(db(), STATE["vault"], m["id"], member_id)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except LookupError as e:
            raise HTTPException(404, str(e))
        db().commit()
        return {**out, "access_log": idin.access_log(db(), member_id)}


@app.get("/api/team/{member_id}/id/photo")
def team_id_photo(member_id: str, x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        try:
            data, mime = idin.view_photo(db(), STATE["vault"], m["id"], member_id)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except LookupError as e:
            raise HTTPException(404, str(e))
        db().commit()
        return Response(data, media_type=mime, headers={"Cache-Control": "no-store"})


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

def _has_prep(d):
    return d.execute("SELECT 1 FROM prep_items WHERE item=?", (PREP_ITEM,)).fetchone() is not None


@app.get("/api/prep")
def prep(x_member: str = Header(None)):
    with LOCK:
        member(x_member)
        d = db()
        if not _has_prep(d):
            return {"empty": True}
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


def _new_member(m, params):
    try:
        return check_new_member(db(), params.get("first_name"), params.get("title"), m["id"], params.get("language", "en"),
                                params.get("level") or 0, params.get("role"), params.get("hourly_cents"),
                                params.get("min_call_in_hours") or 0)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e))


SENSITIVE_KINDS = {"id_intake"}       # their params never go back to the browser and are dropped after use


def _card(m, kind, params):
    """Validate a proposed change and describe it. Nothing is written here."""
    d = db()
    if kind == "setup_code":
        target = str(params.get("member_id") or "")
        try:
            auth.check_issue(d, m["id"], target)
        except LookupError as e:
            raise HTTPException(404, str(e))
        except PermissionError as e:
            raise HTTPException(403, str(e))
        enrolled = auth.factor(d, target) is not None
        lines = [f"Makes a one-time setup code for {target}, good for {auth.SETUP_CODE_HOURS} hours",
                 "They type it on their phone and link an authenticator app (Google or Microsoft Authenticator)",
                 "The code shows once, to you. Give it to them in person"]
        if enrolled:
            lines.append("They already have an authenticator: using this code replaces it and signs out their open sessions")
        return {"title": f"{'Reset sign-in' if enrolled else 'Set up sign-in'} for {target}", "lines": lines, "fix": []}
    if kind == "id_intake":
        target = str(params.get("member_id") or "")
        try:
            v, photo = idin.check_intake(d, m["id"], target, params.get("fields"), params.get("photo"))
        except LookupError as e:
            raise HTTPException(404, str(e))
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except (TypeError, ValueError) as e:
            raise HTTPException(400, str(e))
        prev = idin.id_status(d, target)
        lines = [f"{idin.DOC_TYPES[v['doc_type']]} {idin.mask(v['doc_number'])}, issued by {v['issuer']}"
                 + (f", expires {v['expires_on']}" if v["expires_on"] else ""),
                 "Legal name, birth date, address and emergency contact are sealed (encrypted) on this computer",
                 "Photo of the ID attached, sealed too" if photo else "No photo attached",
                 "Work authorization checked in person" if v["work_auth_verified"] else "Work authorization NOT checked yet",
                 "Managers see only: on file, kind, expiry. Finance and the account holder open the details, and each opening is logged"]
        if prev["on_file"]:
            lines.append(f"Adds version {prev['versions'] + 1}; the earlier version is kept, not overwritten")
        return {"title": f"Put ID on file for {target}", "lines": lines, "fix": []}
    if kind == "add_member":
        v = _new_member(m, params)
        lines = [f"Adds {v['first_name']} as {v['title']}, level {v['level']} {LEVEL_NAMES[v['level']]}",
                 f"Signs in as {v['id']} and opens in {'Spanish' if v['language'] == 'es' else 'English'}",
                 "Sees: " + {0: "own schedule, own tasks, ticket status", 1: "team schedule, inventory counts, roll call",
                             2: "client records, order costs, vendor prices, purchase drafts",
                             3: "payroll, money actions, ticket financial blocks"}[v["level"]]]
        if v["role"]:
            lines.append(f"On the schedule as {v['role']} at ${v['hourly_cents'] / 100:.2f}/h")
        lines.append("Logged under your name; never edited or deleted")
        fix = [{"field": "first_name", "label": "First name", "type": "text"},
               {"field": "title", "label": "Job title", "type": "text"}]
        if m["level"] >= 4:
            fix.append({"field": "level", "label": "Level (0-3)", "type": "number", "min": 0, "max": 3})
        if m["level"] >= 3:
            fix.append({"field": "hourly_cents", "label": "Rate (cents per hour)", "type": "number", "min": 213, "max": 20000})
        return {"title": f"Add {v['first_name']} to the team", "lines": lines, "fix": fix}
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
        if not rule:
            raise HTTPException(404, f"No staffing rule for {role}")
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
        st = money_action_status(d, a["id"])
        if not st.startswith("PENDING"):
            raise HTTPException(409, f"This one is already {st}; decisions are final")
        if d.execute("SELECT 1 FROM approvals WHERE money_action_id=? AND approver=?", (a["id"], m["id"])).fetchone():
            raise HTTPException(409, "You already decided on this one; the other approval has to come from someone else")
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
    if kind == "setup_code":
        code = auth.issue_setup_code(d, m["id"], params["member_id"])
        return f"Setup code for {params['member_id']}: {code[:4]}-{code[4:]} (shows once, good for {auth.SETUP_CODE_HOURS} h)"
    if kind == "id_intake":
        idin.record_intake(d, STATE["vault"], m["id"], params["member_id"], params.get("fields"), params.get("photo"))
        return f"ID on file for {params['member_id']}"
    if kind == "add_member":
        v = _new_member(m, params)
        mid = add_member(d, v["first_name"], v["title"], m["id"], v["language"], v["level"], v["role"],
                         v["hourly_cents"], v["min_call_in_hours"])
        return f"{v['first_name']} added. They sign in as {mid}"
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
        return _public(PROPOSALS[pid])


def _public(p):
    """What goes back to the browser. ID details are not echoed, and a setup code shows only once."""
    out = dict(p)
    if p["kind"] in SENSITIVE_KINDS:
        out["params"] = {"member_id": p["params"].get("member_id")}
    return out


def _forget(p):
    if p["kind"] in SENSITIVE_KINDS:
        p["params"] = {"member_id": p["params"].get("member_id")}
    if p["kind"] == "setup_code" and p.get("result"):
        p["result"] = "Setup code issued (shown once)"


@app.post("/api/proposals/{pid}/confirm")
def confirm(pid: int, x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        p = PROPOSALS.get(pid)
        if not p or p["actor"] != m["id"] or p["state"] != "open":
            raise HTTPException(404, "This card is no longer open")
        _card(m, p["kind"], p["params"])          # re-check: data may have changed since the card was made
        try:
            result = _apply(m, p["kind"], p["params"])
        except PermissionError as e:
            db().rollback()
            raise HTTPException(403, str(e))
        except (LookupError, ValueError) as e:
            db().rollback()
            raise HTTPException(400, str(e))
        db().commit()
        p.update(state="confirmed", result=result)
        out = _public(p)
        _forget(p)
        return out


@app.post("/api/proposals/{pid}/cancel")
def cancel(pid: int, x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        p = PROPOSALS.get(pid)
        if p and p["actor"] == m["id"] and p["state"] == "open":
            p["state"] = "cancelled"
            _forget(p)
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
        if not p or p["state"] != "confirmed" or p["actor"] != m["id"]:
            raise HTTPException(404, "Only the person who confirmed a change gives its check-in")
        status = check_in(db(), f"{p['kind']}#{pid}: {p['title']}", m["id"], c.works, c.intended, c.note)
        db().commit()
        p["checkin"] = status
        return {"status": status}


app.add_middleware(RequestInfo)


# ---------------- the built web app ----------------

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        # Only files inside dist. '/%2e%2e/%2e%2e/human.key' used to walk out and download the account key.
        f = (DIST / path).resolve()
        inside = f.is_relative_to(DIST.resolve())
        return FileResponse(f if path and inside and f.is_file() else DIST / "index.html")

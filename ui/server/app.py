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
import re
import sqlite3
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
from hiwr_stew import portions_left, cost_card, check_order_note, explode, sell, unit_cost
from hiwr_warehouse import pull_list, record_pull
from hiwr_roster import ADD_LEVEL, add_member, check_new_member, get_member, job_roles, roster
import hiwr_auth as auth
import hiwr_notices as notices_mod
import hiwr_taxdocs as taxdocs
import hiwr_idintake as idin
from hiwr_vault import Vault

app = FastAPI(title="Human (HiWR) sample UI")
LOCK = threading.Lock()
STATE = {}
PROPOSALS = {}
CHATS = {}                     # assistant: member id -> {"agent": Agent, "busy": bool}
CHATS_LOCK = threading.Lock()
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


def _ensure_confirm_log(d):
    """Every confirm-card outcome (confirmed, refused on re-check, cancelled), kept for the Business profile.
    Append-only: history is never overwritten."""
    d.executescript("""
        CREATE TABLE IF NOT EXISTS confirm_log (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('confirmed', 'refused', 'cancelled')),
            detail TEXT
        );
        CREATE TRIGGER IF NOT EXISTS confirm_log_no_update BEFORE UPDATE ON confirm_log
            BEGIN SELECT RAISE(ABORT, 'confirm_log is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS confirm_log_no_delete BEFORE DELETE ON confirm_log
            BEGIN SELECT RAISE(ABORT, 'confirm_log is append-only'); END;
    """)
    d.commit()


def _ensure_upc(d):
    """Products can carry the UPC/EAN printed on their label, so Scan finds them."""
    cols = [r[1] for r in d.execute("PRAGMA table_info(products)")]
    if cols and "upc" not in cols:
        d.execute("ALTER TABLE products ADD COLUMN upc TEXT")
        d.commit()


def _ensure_team_chat(d):
    """Team chat under Mesh: one team channel plus one-to-one threads. Append-only: never edited or deleted."""
    d.executescript("""
        CREATE TABLE IF NOT EXISTS team_messages (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL DEFAULT (datetime('now')),
            sender TEXT NOT NULL,
            channel TEXT NOT NULL,          -- 'team' or 'dm:<a>|<b>' (ids sorted)
            text TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS team_messages_channel ON team_messages(channel, id);
        CREATE TRIGGER IF NOT EXISTS team_messages_no_update BEFORE UPDATE ON team_messages
            BEGIN SELECT RAISE(ABORT, 'team_messages is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS team_messages_no_delete BEFORE DELETE ON team_messages
            BEGIN SELECT RAISE(ABORT, 'team_messages is append-only'); END;
    """)
    d.commit()


def _log_confirm(p, outcome, detail=None):
    db().execute("INSERT INTO confirm_log(actor, kind, title, outcome, detail) VALUES (?,?,?,?,?)",
                 (p["actor"], p["kind"], p["title"], outcome, detail))
    db().commit()


def reset(sample=True, demo=None):
    """sample=True loads the test restaurant (tests and demos), in memory. sample=False opens your own
    account from DB_PATH: empty the first time, and whatever you added since then after that.
    demo="stew" | "stock" | "lab" loads that business type's trial demo (in memory) instead."""
    with LOCK:
        if STATE.get("db") is not None:
            STATE["db"].close()
        STATE.clear()
        PROPOSALS.clear()
        PENDING_ENROLL.clear()
        CHATS.clear()                # conversations belong to the data they were about
        if demo:
            STATE["db"], sample = seed.build_demo(demo), True
        else:
            STATE["db"] = seed.build() if sample else seed.build_blank(DB_PATH)
        _ensure_confirm_log(STATE["db"])
        _ensure_upc(STATE["db"])
        _ensure_team_chat(STATE["db"])
        notices_mod.ensure(STATE["db"])
        taxdocs.ensure(STATE["db"])
        if sample:
            for sku, upc in seed.SAMPLE_UPCS.items():
                STATE["db"].execute("UPDATE products SET upc=? WHERE sku=?", (upc, sku))
            STATE["db"].commit()
        STATE["sample"] = sample
        STATE["demo"] = demo
        STATE["clock"] = 16          # prep clock hour, sample
        # Your own account needs a real sign-in, unless HUMAN_AUTH=off (this PC only: keep the server on 127.0.0.1)
        # HUMAN_AUTH=force keeps sign-in on even for sample data: a test copy shared with someone off this PC.
        mode = os.environ.get("HUMAN_AUTH", "on").lower()
        STATE["auth"] = mode == "force" or (not sample and mode != "off")
        STATE["vault"] = (Vault.ephemeral() if sample or DB_PATH == ":memory:"
                          else Vault.from_file(str(Path(DB_PATH).with_suffix(".key"))))
        if STATE["auth"]:
            _first_owner_code()
            _guest_code()


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


def _guest_code():
    """HUMAN_GUEST=<first name> on a test copy: add that person at Finance level (sees everything but can't switch
    the business) and print their one-time setup code in the server window, like the owner's."""
    name = (os.environ.get("HUMAN_GUEST") or "").strip()
    if not name:
        return
    d = STATE["db"]
    r = d.execute("SELECT id FROM members WHERE first_name=?", (name,)).fetchone()
    mid = r["id"] if r else add_member(d, name, "Manager", seed.OWNER, "en", 3, reason="test copy guest")
    if auth.factor(d, mid):
        return
    code = auth.issue_setup_code(d, "console", mid)
    d.commit()
    STATE["guest_code"] = code
    print(f"\n  Human: setup code for {name} ({mid})\n"
          f"  Setup code: {code[:4]}-{code[4:]}  (good for {auth.SETUP_CODE_HOURS} hours)\n", flush=True)


# Starts empty (no test data) unless HUMAN_DATA is set before starting the server:
# sample = the full test restaurant; stew, stock or lab = that trial demo.
_start = os.environ.get("HUMAN_DATA", "blank").lower()
reset(sample=_start == "sample", demo=_start if _start in seed.DEMOS else None)


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
        d = db()
        # set_up tells the sign-in screen to go straight to the first-time QR step for someone with no authenticator yet
        return [{**{k: m[k] for k in ("id", "name", "title", "language")}, "set_up": bool(auth.factor(d, m["id"]))}
                for m in roster(d)]


def _any(d, sql, args=()):
    try:
        return d.execute(sql, args).fetchone() is not None
    except sqlite3.OperationalError:      # table not there yet: nothing to work on
        return False


def sections(d, m):
    """The menu for this member: a section shows only when their level can open it AND there is something in it
    to work on. Team (managers and up) and the Assistant always show: they are how you start."""
    lvl, me_id = m["level"], m["id"]
    menu = []
    if lvl >= 2:
        menu.append({"id": "team", "label": "Team"})   # adding people is where it starts
    if lvl >= 1:
        staff_work = _any(d, "SELECT 1 FROM staffing_rules") or _any(d, "SELECT 1 FROM staff") \
            or _any(d, "SELECT 1 FROM hr_requests")
    else:
        staff_work = _any(d, "SELECT 1 FROM staff_availability WHERE member=? AND day=?", (me_id, seed.TODAY)) \
            or _any(d, "SELECT 1 FROM hr_requests WHERE member=?", (me_id,)) \
            or _any(d, "SELECT 1 FROM order_stage_events WHERE actor=? AND stage='cooking'", (me_id,))
    if staff_work:
        menu.append({"id": "staff", "label": "Staff"})
    inv = []
    if _any(d, "SELECT 1 FROM products"):
        inv.append("warehouse")
    if _any(d, "SELECT 1 FROM recipes") or _any(d, "SELECT 1 FROM materials"):
        inv.append("stew")
    if _any(d, "SELECT 1 FROM lab_projects"):
        inv.append("lab")
    if inv:
        menu.append({"id": "inventory", "label": "Inventory", "children": inv})
    if _has_prep(d) and (lvl >= 2 or _any(d, "SELECT 1 FROM staff WHERE member=? AND role='cook'", (me_id,))):
        menu.append({"id": "prep", "label": "My station"})
    if _any(d, "SELECT 1 FROM recipes WHERE kind='dish'") or _support_requests(d, m):
        menu.append({"id": "cs", "label": "Customer service"})
    menu.append({"id": "assistant", "label": "Assistant"})   # local model; it sees only what this level may see
    if lvl >= 3 and _any(d, "SELECT 1 FROM money_actions"):
        menu.append({"id": "accounting", "label": "Accounting"})
    if lvl < 2 and (my_day(d, me_id, seed.TODAY) or _any(d, "SELECT 1 FROM notice_tasks WHERE member=?", (me_id,))):
        menu.append({"id": "tasks", "label": "My tasks"})
    return menu


@app.get("/api/me")
def me(x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        menu = sections(db(), m)
        open_cards = sum(1 for p in PROPOSALS.values() if p["actor"] == m["id"] and p["state"] == "open")
        return {**m, "menu": menu, "today": seed.TODAY, "clock": STATE["clock"], "open_cards": open_cards,
                "sample": STATE["sample"], "demo": STATE.get("demo"), "auth_required": STATE["auth"]}


@app.post("/api/demo/reset")
def demo_reset(sample: bool | None = None, demo: str | None = None, x_member: str = Header(None)):
    """Account holder only. ?sample=true switches to the test restaurant, ?sample=false to your own (saved) account,
    ?demo=stew|stock|lab to that business type's trial demo (offered before a plan is bought).
    Nothing is deleted: your own account file is only opened, never wiped."""
    with LOCK:
        need(member(x_member), 4, "Switching business")
        if demo is not None and demo not in seed.DEMOS:
            raise HTTPException(400, f"No demo called {demo}. Pick one of: {', '.join(seed.DEMOS)}")
        keep, keep_demo = STATE["sample"], STATE.get("demo")
    if demo:
        reset(demo=demo)
    elif sample is None:
        reset(keep, keep_demo)
    else:
        reset(sample)
    return {"ok": True, "sample": STATE["sample"], "demo": STATE["demo"]}


@app.get("/api/demos")
def demos():
    """The trial demos a new business can try before buying a plan."""
    return [{"id": k, **v} for k, v in seed.DEMOS.items()]


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
        if STATE["sample"] and STATE.get("demo") in (None, "stew"):   # sample POS sales: restaurant only
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

def _restaurant_sample():
    return STATE["sample"] and STATE.get("demo") in (None, "stew")


def _price(d, dish):
    r = d.execute("SELECT price_cents FROM recipes WHERE name=?", (dish,)).fetchone()
    return (r["price_cents"] if r and r["price_cents"] else seed.SIDE_PRICES.get(dish, 0))


def _revenue(d, day, upto=24):
    return sum(r["qty"] * _price(d, r["dish"]) for r in d.execute("SELECT dish, qty FROM dish_sales WHERE day=? AND hour<?", (day, upto)))


def _forecast_revenue(d, upto=24):
    """Same weekday, last 4 weeks, dinner scaled by tonight's reservations vs usual."""
    from datetime import date, timedelta
    scale = seed.RESERVATIONS / seed.USUAL_RESERVATIONS if _restaurant_sample() else 1.0
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
                      "detail": f"{seed.RESERVATIONS} reservations vs usual {seed.USUAL_RESERVATIONS}" if _restaurant_sample()
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
        d = db()
        rows = my_day(d, m["id"], seed.TODAY)
        rows += [{"id": f"n{t['id']}", "kind": "notice", "title": f"{t['area']} ({t['title']}, {t['due_on']})",
                  "due_on": t["due_on"], "status": t["status"]} for t in notices_mod.tasks_for(d, m["id"])]
        return rows


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
                "doc_types": idin.DOC_TYPES, "job_roles": job_roles(d)}


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


# ---------------- Mesh: staff organizer from the history between employees and sales ----------------

def _first_name(d, mid):
    r = d.execute("SELECT first_name FROM members WHERE id=?", (mid,)).fetchone()
    return r["first_name"] if r else mid.split("_")[-1].capitalize()


@app.get("/api/mesh")
def mesh_view(x_member: str = Header(None)):
    """Who makes what, how fast and how often it's remade, refreshed from order history. Leads and up see the
    whole team; staff see only their own items."""
    from hiwr_mesh import mesh
    with LOCK:
        m = member(x_member)
        d = db()
        own = m["level"] < 1
        try:
            r = mesh(d, member=m["id"] if own else None)
        except sqlite3.OperationalError:
            r = {"by_item": {}, "by_member": {}, "best": {}, "lines": 0}
        name = lambda x: {**x, "name": _first_name(d, x["member"])}
        return {"own": own, "lines": r["lines"],
                "by_item": {k: [name(x) for x in v] for k, v in r["by_item"].items()},
                "by_member": [{"member": k, "name": _first_name(d, k), "items": v} for k, v in r["by_member"].items()],
                "best": {k: name(v) for k, v in r["best"].items()}}


# ---------------- Demo scene: prior invoices settle the numbers ----------------
# A guided walk-through on demo data: what was billed, what arrived, what was actually paid, and how that settles
# the unit costs, food cost and margin the algorithms use. Short or disputed money stays "anticipated" until two
# certified people approve a resolution. Read only: nothing here changes anything.

@app.get("/api/scene")
def scene(x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        if not STATE["sample"]:
            raise HTTPException(404, "The demo scene runs on the demos only")
        d = db()
        costs_ok = can_see(d, m["id"], "order_costs")
        inv = []
        settled = {}     # material -> (paid cents, qty received) from settled invoices
        anticipated = 0
        for x in seed.PRIOR_INVOICES:
            row = dict(x)
            if x["status"] in ("paid", "paid_short"):
                p, q = settled.get(x["material"], (0, 0))
                settled[x["material"]] = (p + x["paid_cents"], q + x["received_qty"])
                row["settles"] = True
            else:
                owed = round(x["billed_cents"] * x["received_qty"] / x["billed_qty"])
                anticipated += x["billed_cents"] - owed
                row["settles"] = False
                row["anticipated_cents"] = x["billed_cents"] - owed
            inv.append(row)
        unit_before, unit_after = {}, {}
        for mat, (paid, qty) in settled.items():
            unit_before[mat] = unit_cost(d, mat)
            unit_after[mat] = round(paid / qty, 2) if qty else unit_before[mat]
        dishes = []
        for r in d.execute("SELECT name, price_cents FROM recipes WHERE kind='dish' ORDER BY name"):
            before = after = 0.0
            for mat, (q, unit, via) in explode(d, r["name"]).items():
                before += q * unit_cost(d, mat)
                after += q * unit_after.get(mat, unit_cost(d, mat))
            dishes.append({"dish": r["name"], "price": r["price_cents"], "cost_before": round(before), "cost_after": round(after),
                           "margin_before": round(100 * (1 - before / r["price_cents"]), 1),
                           "margin_after": round(100 * (1 - after / r["price_cents"]), 1)})
        approvals = [{"scope": a["scope"], "amount": a["amount_cents"], "description": a["description"],
                      "status": money_action_status(d, a["id"])} for a in d.execute("SELECT * FROM money_actions ORDER BY id")]
        if not costs_ok:      # below Manager: the story without the dollars
            for x in inv:
                for k in ("billed_cents", "paid_cents", "anticipated_cents"):
                    x.pop(k, None)
            dishes = [{"dish": x["dish"]} for x in dishes]
            approvals = [{"scope": a["scope"], "status": a["status"]} for a in approvals]
        return {"shows_costs": costs_ok, "invoices": inv, "anticipated_cents": anticipated if costs_ok else None,
                "units": [{"material": k, "before": unit_before[k], "after": unit_after[k]} for k in sorted(settled)] if costs_ok else [],
                "dishes": dishes, "approvals": approvals}


# ---------------- Schedule, notices -> tasks, tax forms ----------------

def _week():
    return seed.week_schedule(seed.TODAY) if STATE["sample"] else {}


@app.get("/api/schedule")
def schedule(x_member: str = Header(None)):
    """The week's shifts (sample schedule on the demos). Leads and up see the team; staff see their own."""
    with LOCK:
        m = member(x_member)
        d = db()
        out = []
        for day, shifts in sorted(_week().items()):
            rows = [{"member": w, "name": _first_name(d, w), "start": a, "end": b, "closes": b >= notices_mod.CLOSING_FROM}
                    for w, a, b in shifts if m["level"] >= 1 or w == m["id"]]
            out.append({"day": day, "shifts": rows})
        return {"week": out}


@app.get("/api/notices")
def notices_view(x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        need(m, 1, "Notices")
        d = db()
        rows = notices_mod.notices(d)
        for n in rows:
            n["posted_by_name"] = _first_name(d, n["posted_by"])
            for t in n["tasks"]:
                t["name"] = _first_name(d, t["member"])
        return {"notices": rows, "can_post": m["level"] >= notices_mod.POST_LEVEL,
                "areas": notices_mod.DEEP_CLEAN_AREAS, "has_schedule": bool(_week())}


@app.get("/api/docs")
def docs(x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        d = db()
        out = {"mine": taxdocs.my_documents(d, m["id"]), "can_post": m["level"] >= taxdocs.POST_LEVEL}
        if out["can_post"]:
            out["posted"] = [{**x, "name": _first_name(d, x["member"])} for x in taxdocs.posted(d, m["level"])]
        return out


@app.get("/api/docs/{doc_id}")
def doc_open(doc_id: int, x_member: str = Header(None)):
    """Opening a tax form is logged, every time. Your own, or anyone's at Finance level."""
    with LOCK:
        m = member(x_member)
        try:
            out = taxdocs.open_document(db(), m["id"], m["level"], doc_id)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except LookupError as e:
            raise HTTPException(404, str(e))
        db().commit()
        out["name"] = _first_name(db(), out["member"])
        return out


def _wages(d, year):
    if STATE["sample"]:
        return {k: v for k, v in seed.SAMPLE_WAGES_2025.items() if get_member(d, k)}
    # your own account: hourly rate x 1,560 hours as a placeholder until payroll is connected
    return {r["member"]: r["hourly_cents"] * 1560 for r in d.execute("SELECT member, hourly_cents FROM staff")
            if r["hourly_cents"] and get_member(d, r["member"])}


# ---------------- Team chat (HiWR -> Mesh) ----------------
# Everyone on the roster can use the team channel and message one person directly. Card numbers and SSNs are
# refused (the rules say no full card numbers anywhere); pay, money and approvals stay in their own screens.

_CARD = re.compile(r"(?:\d[ -]?){13,19}")
_SSN = re.compile(r"\d{3}-\d{2}-\d{4}")


class ChatPost(BaseModel):
    to: str
    text: str


def _channel(me_id, to):
    if to == "team":
        return "team"
    if not get_member(db(), to) or to == me_id:
        raise HTTPException(404, "Pick someone on the team")
    a, b = sorted([me_id, to])
    return f"dm:{a}|{b}"


@app.get("/api/chat")
def chat_read(to: str = "team", after: int = 0, x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        d = db()
        ch = _channel(m["id"], to)
        rows = [{"id": r["id"], "at": r["at"], "from": r["sender"], "name": _first_name(d, r["sender"]), "text": r["text"],
                 "mine": r["sender"] == m["id"]}
                for r in d.execute("SELECT * FROM team_messages WHERE channel=? AND id>? ORDER BY id DESC LIMIT 200", (ch, after))]
        people = [{"id": x["id"], "name": x["name"], "title": x["title"]} for x in roster(d) if x["id"] != m["id"]]
        return {"channel": ch, "messages": rows[::-1], "people": people}


@app.post("/api/chat")
def chat_post(c: ChatPost, x_member: str = Header(None)):
    text = (c.text or "").strip()
    if not text:
        raise HTTPException(400, "Type a message first")
    if len(text) > 1000:
        raise HTTPException(400, "That's too long for one message (1000 characters at most)")
    if _CARD.search(text) or _SSN.search(text):
        raise HTTPException(400, "Card numbers and Social Security numbers can't go in chat")
    with LOCK:
        m = member(x_member)
        ch = _channel(m["id"], c.to)
        mid = db().execute("INSERT INTO team_messages(sender, channel, text) VALUES (?,?,?)", (m["id"], ch, text)).lastrowid
        db().commit()
        return {"id": mid}


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
    """Long jobs (house builds): phases with inspection gates, subs, and open tickets. Dollars (contract,
    labor, sub amounts) only for those who may see order costs."""
    from hiwr_lab import job_cost
    with LOCK:
        m = member(x_member)
        d = db()
        costs = can_see(d, m["id"], "order_costs")
        jobs = []
        for p in d.execute("SELECT * FROM lab_projects ORDER BY id"):
            phases = []
            for row, ph in zip(job_cost(d, p["id"]), d.execute("SELECT * FROM lab_phases WHERE project_id=? ORDER BY seq", (p["id"],))):
                started = d.execute("SELECT 1 FROM lab_phase_events WHERE phase_id=? AND event='started'", (ph["id"],)).fetchone()
                status = {"pass": "passed", "fail": "failed"}.get(row["status"], "in progress" if started else "not started")
                item = {"seq": ph["seq"], "phase": row["phase"], "inspection": row["inspection"], "status": status}
                if costs:
                    item.update(labor=row["labor"], labor_budget=row["labor_budget"], subs=row["subs"], over=row["over"])
                phases.append(item)
            subs = [{"sub": r["sub"], "scope": r["scope"], **({"amount": r["amount_cents"]} if costs else {})}
                    for r in d.execute("SELECT * FROM lab_subcontracts WHERE project_id=? ORDER BY id", (p["id"],))]
            job = {"id": p["id"], "name": p["name"], "permit": p["permit_number"], "starts_on": p["starts_on"],
                   "target_end": p["target_end"], "phases": phases, "subs": subs}
            if costs:
                job["contract"] = p["contract_cents"]
            jobs.append(job)
        tickets = [{"id": t["id"], "type": t["type"], "status": t["status"], "subject": t["subject"]}
                   for t in d.execute("SELECT * FROM tickets WHERE status!='closed' ORDER BY priority")]
        return {"jobs": jobs, "shows_costs": costs, "tickets": tickets,
                "note": "" if jobs else "Lab is for long jobs (house builds: phases, inspections, subs). None yet."}


# ---------------- Scan (HiWR button) ----------------
# The browser reads the QR code, barcode or text itself; only the decoded text comes here, to look it up in
# this business's records. Prices and costs are left out below Manager level.

class ScanLookup(BaseModel):
    code: str
    kind: str | None = None


@app.post("/api/scan/lookup")
def scan_lookup(q: ScanLookup, x_member: str = Header(None)):
    code = (q.code or "").strip()[:500]
    with LOCK:
        m = member(x_member)
        d = db()
        if not code:
            return {"matches": []}
        like = f"%{code}%"
        out = []
        digits = "".join(ch for ch in code if ch.isdigit())
        try:
            for r in d.execute("SELECT * FROM products WHERE sku=? COLLATE NOCASE OR name LIKE ? "
                               "OR (? != '' AND ltrim(upc, '0') = ltrim(?, '0')) LIMIT 10", (code, like, digits, digits)):
                out.append({"type": "Product", "id": r["sku"], "title": r["name"],
                            "detail": f"shelf {r['shelf']}, back stock {r['back_stock']}"
                                      + (f", dated {r['expires_on']}" if "expires_on" in r.keys() and r["expires_on"] else "")})
        except sqlite3.OperationalError:
            pass
        for r in d.execute("SELECT id, type, status, subject FROM tickets WHERE id=? COLLATE NOCASE OR subject LIKE ? LIMIT 10",
                           (code, like)):
            out.append({"type": "Ticket", "id": r["id"], "title": r["subject"], "detail": f"{r['type']} · {r['status']}"})
        for r in d.execute("SELECT name, kind FROM recipes WHERE name LIKE ? OR (kind='dish' AND ? LIKE '%' || name || '%') "
                           "LIMIT 10", (like, code)):
            out.append({"type": "Dish" if r["kind"] == "dish" else "Recipe", "id": r["name"], "title": r["name"],
                        "detail": r["kind"]})
        return {"matches": out}


# ---------------- Customer service ----------------

def _support_requests(d, m):
    """Requests for a person that the assistant queued (agent_handoffs). Managers and up see the queue;
    everyone else sees only their own. Nothing here sends anything: a person follows up."""
    sql = "SELECT id, at, requested_for, channel, route_to, reason, best_time, ticket_id, status FROM agent_handoffs"
    args = ()
    if m["level"] < 2:
        sql, args = sql + " WHERE requested_for=?", (m["id"],)
    try:
        return [dict(r) for r in d.execute(sql + " ORDER BY id DESC LIMIT 100", args)]
    except sqlite3.OperationalError:          # the assistant hasn't run on this data yet
        return []


@app.get("/api/cs/support")
def cs_support(x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        return {"requests": _support_requests(db(), m), "queue": m["level"] >= 2}


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


LEVEL_SEES = {0: "own schedule, own tasks, ticket status", 1: "team schedule, inventory counts, roll call",
              2: "client records, order costs, vendor prices, purchase drafts",
              3: "payroll, money actions, ticket financial blocks",
              4: "everything, and sets everyone's level"}


def _new_member(m, params):
    # The job role comes from the pick list, set by a manager or the account holder only.
    need(m, ADD_LEVEL, "Setting someone's job role")
    if " ".join((params.get("title") or "").split()) not in job_roles(db()):
        raise HTTPException(400, "Pick the job role from the list")
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
                 "Sees: " + LEVEL_SEES[v["level"]]]
        if v["role"]:
            lines.append(f"On the schedule as {v['role']} at ${v['hourly_cents'] / 100:.2f}/h")
        lines.append("Logged under your name; never edited or deleted")
        fix = [{"field": "first_name", "label": "First name", "type": "text"}]
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
    if kind == "post_notice":
        need(m, notices_mod.POST_LEVEL, "Posting a notice")
        week = _week()
        if not week:
            raise HTTPException(400, "There's no schedule yet to spread the tasks over")
        try:
            plan = notices_mod.plan(week, notices_mod.DEEP_CLEAN_AREAS)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"title": f"Post the deep-clean notice for the week of {min(week)}",
                "lines": [f"{t['due_on']}: {_first_name(d, t['member'])}, {t['area']}" for t in plan]
                         + ["Each one lands in that person's tasks; done marks are kept, never edited"],
                "fix": []}
    if kind == "notice_task_done":
        tid = int(str(params.get("task_id", "")).lstrip("n") or 0)
        r = d.execute("SELECT * FROM notice_tasks WHERE id=? AND member=?", (tid, m["id"])).fetchone()
        if not r:
            raise HTTPException(404, "That task is not on your list")
        return {"title": f"Mark done: {r['area']}", "lines": ["Your manager sees it as done, with your name and the time"],
                "fix": [{"field": "note", "label": "Note (optional)", "type": "text"}]}
    if kind == "post_w2s":
        try:
            year, new = taxdocs.check_post(d, m["level"], int(params.get("year", 2025)), _wages(d, int(params.get("year", 2025))))
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"title": f"Post {len(new)} W-2s for {year}",
                "lines": [f"For: {', '.join(_first_name(d, k) for k in new)}",
                          f"Total wages ${sum(new.values()) / 100:,.2f}" + (" (sample data)" if STATE["sample"] else ""),
                          "Each person sees only their own; every opening is logged",
                          "No Social Security numbers or last names on the forms"],
                "fix": []}
    if kind == "import_sales":
        need(m, 2, "Importing sales")
        v = _resolve_sales(d, params.get("rows"))
        if not v["dishes"] and not v["products"]:
            raise HTTPException(400, "Nothing in this file matches your dishes or products"
                                     + (f" (not found: {', '.join(v['unknown'][:5])})" if v["unknown"] else ""))
        lines = [f"Registers {v['plates']} plates and {v['units']} store units from {v['count']} sales lines"]
        for mat, (before, used, unit) in sorted(v["materials"].items(), key=lambda x: -x[1][1])[:8]:
            lines.append(f"{mat}: {before:g} -> {before - used:g} {unit}" + ("  (more sold than on hand: count it)" if used > before else ""))
        for sku, (name, before, qty) in v["shelves"].items():
            lines.append(f"{name}: shelf {before} -> {max(0, before - qty)}" + ("  (more sold than on the shelf: count it)" if qty > before else ""))
        if v["unknown"]:
            lines.append(f"Left out, not found: {', '.join(v['unknown'][:6])}" + (" ..." if len(v["unknown"]) > 6 else ""))
        lines.append("Logged as sales moves under your name; nothing is paid or sent")
        return {"title": f"Import {v['count']} sales lines into inventory", "lines": lines, "fix": []}
    raise HTTPException(400, f"Unknown change: {kind}")


def _resolve_sales(d, rows):
    """Match sales-register lines (item name, SKU or UPC, and a quantity) to dishes and store products, and work
    out what each one takes out of inventory. Reads only."""
    if not isinstance(rows, list) or not rows:
        raise HTTPException(400, "No sales lines in the file")
    if len(rows) > 5000:
        raise HTTPException(400, "That's more than 5000 lines; split the file")
    dishes, products, unknown = {}, {}, []
    for r in rows:
        name = str((r or {}).get("item") or "").strip()
        try:
            qty = int(float(r.get("qty", 1)))
        except (TypeError, ValueError):
            raise HTTPException(400, f"Quantity for {name or 'a line'} isn't a number")
        if not name or qty <= 0:
            continue
        hour = r.get("hour")
        dish = d.execute("SELECT name FROM recipes WHERE kind='dish' AND name=? COLLATE NOCASE", (name,)).fetchone()
        if dish:
            dishes.setdefault((dish["name"], hour), 0)
            dishes[(dish["name"], hour)] += qty
            continue
        digits = "".join(ch for ch in name if ch.isdigit())
        try:
            prod = d.execute("SELECT * FROM products WHERE sku=? COLLATE NOCASE OR name=? COLLATE NOCASE "
                             "OR (? != '' AND ltrim(upc,'0') = ltrim(?,'0'))", (name, name, digits, digits)).fetchone()
        except sqlite3.OperationalError:
            prod = None
        if prod:
            products[prod["sku"]] = products.get(prod["sku"], 0) + qty
        elif name not in unknown:
            unknown.append(name)
    materials = {}
    for (dish, _), qty in dishes.items():
        for mat, (q, unit, via) in explode(d, dish).items():
            on = d.execute("SELECT on_hand FROM materials WHERE material=?", (mat,)).fetchone()["on_hand"]
            before, used, _u = materials.get(mat, (on, 0.0, unit))
            materials[mat] = (before, round(used + q * qty, 3), unit)
    shelves = {}
    for sku, qty in products.items():
        p = d.execute("SELECT name, shelf FROM products WHERE sku=?", (sku,)).fetchone()
        shelves[sku] = (p["name"], p["shelf"], qty)
    return {"dishes": dishes, "products": products, "unknown": unknown, "materials": materials, "shelves": shelves,
            "plates": sum(dishes.values()), "units": sum(products.values()), "count": len(rows)}


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
    if kind == "post_notice":
        nid = notices_mod.post(d, m["level"], m["id"], "deep_clean", "Deep clean", min(_week()),
                               notices_mod.DEEP_CLEAN_AREAS, _week())
        return f"Deep-clean notice posted; {len(notices_mod.DEEP_CLEAN_AREAS)} tasks scheduled (notice {nid})"
    if kind == "notice_task_done":
        notices_mod.mark_done(d, int(str(params["task_id"]).lstrip("n")), m["id"], params.get("note"))
        return "Marked done"
    if kind == "post_w2s":
        n = taxdocs.post_w2s(d, m["id"], m["level"], int(params.get("year", 2025)), _wages(d, int(params.get("year", 2025))))
        return f"{n} W-2s posted"
    if kind == "import_sales":
        v = _resolve_sales(d, params.get("rows"))
        for (dish, hour), qty in v["dishes"].items():
            sell(d, dish, qty, now_iso(), f"import by {m['id']}")
            h = int(hour) if str(hour or "").isdigit() and 0 <= int(hour) <= 23 else STATE["clock"]
            d.execute("INSERT INTO dish_sales VALUES (?,?,?,?)", (seed.TODAY, h, dish, qty))
        for sku, qty in v["products"].items():
            d.execute("UPDATE products SET shelf = MAX(0, shelf - ?) WHERE sku=?", (qty, sku))
            d.execute("INSERT INTO daily_sales(sku, day, units) VALUES (?,?,?) "
                      "ON CONFLICT(sku, day) DO UPDATE SET units = units + excluded.units", (sku, seed.TODAY, qty))
        return f"Imported {v['plates']} plates and {v['units']} store units; inventory updated"


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
        try:
            _card(m, p["kind"], p["params"])      # re-check: data may have changed since the card was made
            result = _apply(m, p["kind"], p["params"])
        except HTTPException as e:
            db().rollback()
            _log_confirm(p, "refused", str(e.detail))
            raise
        except PermissionError as e:
            db().rollback()
            _log_confirm(p, "refused", str(e))
            raise HTTPException(403, str(e))
        except (LookupError, ValueError) as e:
            db().rollback()
            _log_confirm(p, "refused", str(e))
            raise HTTPException(400, str(e))
        db().commit()
        _log_confirm(p, "confirmed", str(result) if result is not None else None)
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
            _log_confirm(p, "cancelled")
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


# ---------------- Business profile (the Human logo; account holder only) ----------------

def _file_levels():
    """Which business files each level may open, from the assistant's config (read only here)."""
    import tomllib
    agent_dir = Path(__file__).resolve().parents[2] / "agent"
    for name in ("config.toml", "config.example.toml"):
        f = agent_dir / name
        if f.exists():
            try:
                with open(f, "rb") as fh:
                    cfg = tomllib.load(fh)
                return {"mode": cfg.get("mode", "local"), "business": cfg.get("business", {}),
                        "files": sorted(([k, int(v)] for k, v in cfg.get("files", {}).get("levels", {}).items()),
                                        key=lambda x: (-x[1], x[0]))}
            except (OSError, ValueError):
                break
    return {"mode": "local", "files": [], "business": {}}


def _suggestions(d, team, files, log):
    """Tips for getting more out of the app, from what is (and isn't) set up yet. Most useful first."""
    tips = []
    if STATE.get("sample"):
        tips.append(["demo", "You're in a demo. Try a confirm card (fry a batch, pull stock, approve a refund) "
                             "and watch it land in Confirmations below. Switch back to your business from the logo."])
    if len(team) < 2:
        tips.append(["team", "Add the people who work with you on the Team screen. Most sections stay hidden "
                             "until there's someone and something to work on."])
    if sum(1 for x in team if x["level"] >= 3) < 2:
        tips.append(["approvers", "Money needs two certified approvers. Raise two trusted people to Finance level "
                                  "so refunds, payroll and purchases can be approved."])
    missing = [k.replace("_", " ") for k in ("phone", "text_number", "hours", "on_duty_manager")
               if not (files.get("business") or {}).get(k)]
    if missing:
        tips.append(["contacts", "Add your business " + ", ".join(missing) + " (agent/config.toml, [business]) so the "
                                 "assistant can hand people to a real person instead of saying they're missing."])
    tips.append(["assistant", "Ask the assistant on Home for what's open, who's short or which tickets are oldest. "
                              "Staff can ask it for a person, and it goes to Customer service."])
    tips.append(["hiwr", "The HiWR button next to the logo opens quick actions: Scan reads codes and text, Mesh "
                         "organizes your staff, and Settings is last. Import, Recall and Add are on the way."])
    if not log:
        tips.append(["confirm", "Every change goes through a confirm card: Confirm, Fix or Cancel. Nothing changes "
                                "on one tap, and each one is kept in Confirmations."])
    if any(not auth.factor(d, x["id"]) for x in team):
        tips.append(["signin", "Give everyone authenticator sign-in: on the Team screen, issue each person a setup "
                               "code. Their sign-ins and where they came from show in Authenticator below."])
    return [{"id": i, "text": txt} for i, txt in tips]


@app.get("/api/settings")
def settings(x_member: str = Header(None)):
    """Read-only view for the account holder: who sees what (sensitivity), every confirm-card outcome and
    check-in, and the authenticator history. Nothing here changes a setting."""
    with LOCK:
        m = member(x_member)
        need(m, 4, "The business profile")
        d = db()
        team = roster(d)
        counts = {i: sum(1 for x in team if x["level"] == i) for i in range(5)}
        files = _file_levels()
        log = [dict(r) for r in d.execute("SELECT at, actor, kind, title, outcome, detail FROM confirm_log ORDER BY id DESC LIMIT 200")]
        checkins = [{"at": r["at"] if "at" in r.keys() else None, "subject": r["subject"], "by": r["checked_by"],
                     "works": bool(r["works"]), "intended": bool(r["intended_consequence"]), "note": r["note"]}
                    for r in d.execute("SELECT * FROM result_checkins ORDER BY rowid DESC LIMIT 200")]
        return {
            "business": {"owner": m["name"], "data": seed.DEMOS[STATE["demo"]]["name"] if STATE.get("demo")
                         else "Test restaurant (sample)" if STATE["sample"] else "Your account",
                         "people": len(team)},
            "sensitivity": {
                "levels": [{"level": i, "name": LEVEL_NAMES[i], "sees": LEVEL_SEES[i], "people": counts[i]} for i in range(5)],
                "files": files["files"],
                "assistant": "Runs on this computer only (local model). Nothing leaves the PC from the app."
                             if files["mode"] == "local" else f"Assistant mode in config: {files['mode']}. "
                             "The web app still runs it locally only.",
                "money": "Refunds, fees, payroll and purchases always need two certified approvers.",
            },
            "suggestions": _suggestions(d, team, files, log),
            "confirmations": log,
            "checkins": checkins,
            # Sign-in history and where each sign-in came from (method · device · ip · who issued it), append-only
            "authenticator": auth.settings_block(d, [x["id"] for x in team]),
        }


# ---------------- Assistant (the agent, on the local model) ----------------
# The web app runs the assistant on Ollama on this computer only: no cloud backend is built here, so a
# "/cloud" prefix or a hybrid config can't send anything out. The agent's own tools keep the access,
# privacy and approval rules; it never approves, pays or sends.

def _agent_cfg():
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from agent import config as agent_config
    cfg = agent_config.load(os.environ.get("HUMAN_AGENT_CONFIG"), {"mode": "local"})
    folder = Path(cfg["data"]["folder"])
    if not folder.is_absolute():
        cfg["data"]["folder"] = str(root / folder)
    return cfg


def _new_agent(m):
    """Called with LOCK held. Tests swap this for a scripted fake model."""
    from agent.runner import Agent
    from agent.backends import OllamaBackend
    cfg = _agent_cfg()
    return Agent(db(), cfg, m["id"], backends={"local": OllamaBackend(cfg)}, clock=lambda: now_iso(),
                 db_path=None if STATE["sample"] else DB_PATH, lock=LOCK)


class Ask(BaseModel):
    text: str


@app.get("/api/assistant")
def assistant_history(x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        chat = CHATS.get(m["id"])
    model = None
    try:
        model = _agent_cfg()["local"]["model"]
    except Exception:  # noqa: BLE001 - a bad config shows up on the first question instead
        pass
    hist = chat["agent"].history if chat else []
    return {"model": model, "busy": bool(chat and chat["busy"]),
            "messages": [x for u, r in hist for x in ({"role": "user", "text": u}, {"role": "assistant", "text": r})]}


@app.post("/api/assistant")
def assistant_ask(a: Ask, x_member: str = Header(None)):
    from agent.backends import BackendUnavailable
    text = a.text.strip()
    if not text:
        raise HTTPException(400, "Type a question first")
    if len(text) > 4000:
        raise HTTPException(400, "That's too long for one question (4000 characters at most)")
    if text.lower().startswith("/cloud"):
        raise HTTPException(400, "This app only uses the local model on this computer")
    with LOCK:
        m = member(x_member)
        with CHATS_LOCK:
            chat = CHATS.get(m["id"])
            if chat and chat["busy"]:
                raise HTTPException(409, "Still answering your last question")
            if chat is None:
                chat = CHATS[m["id"]] = {"agent": _new_agent(m), "busy": False}
            chat["busy"] = True
    try:
        reply = chat["agent"].ask(text)          # LOCK is taken only around database work, not model time
    except BackendUnavailable as e:
        raise HTTPException(503, f"Couldn't reach the local model: {e}")
    finally:
        chat["busy"] = False
    if not reply.strip():
        chat["agent"].history.pop()               # don't keep a blank turn in the conversation
        raise HTTPException(502, "The local model gave an empty answer. Ask again.")
    return {"role": "assistant", "text": reply, "backend": chat["agent"].last_route["backend"]}


@app.post("/api/assistant/clear")
def assistant_clear(x_member: str = Header(None)):
    with LOCK:
        m = member(x_member)
        with CHATS_LOCK:
            chat = CHATS.get(m["id"])
            if chat and chat["busy"]:
                raise HTTPException(409, "Still answering your last question")
            CHATS.pop(m["id"], None)
    return {"ok": True}


app.add_middleware(RequestInfo)


# ---------------- the built web app ----------------

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")

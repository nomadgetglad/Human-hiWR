"""Sign-in for the people on the roster, with an authenticator app (TOTP, RFC 6238).

How someone gets in:
  1. A manager (or the account holder) gives them a one-time setup code (8 characters, good for 24 hours).
     The account holder's first code is printed in the server window on a new account.
  2. On their phone they open Human, pick their name, type the setup code, and scan the QR code with
     Google Authenticator, Microsoft Authenticator, 1Password or any TOTP app.
  3. They type the 6-digit code the app shows. That links the app. After that, sign-in is name + 6-digit code.

Rules:
  - A setup code is for one person, works once, and only the person who issued it and the code itself know it.
    Issuing a code needs Manager level, and a manager only issues codes for people below their own level.
    Only the account holder issues codes for managers; the account holder's own code comes from the server
    machine (console), so a lost phone is fixed by someone standing at the office computer.
  - Using a new setup code replaces the old authenticator and ends every open session for that person (reset).
  - 5 wrong codes in 15 minutes locks sign-in for that person for 15 minutes.
  - A 6-digit code works once: the same code can't be replayed inside its 30 seconds.
  - Sessions last 12 hours (a shift) and are stored as hashes; signing out ends them.
  - Every step is an auth_events row (method, device, ip, time) for the history in settings.

Passkeys (Face ID / fingerprint) can be added later as a second method; they need the app served over
https with a real name, which a desktop on the shop network does not have yet.
"""
import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from hiwr_access import level, LEVEL_NAMES
from hiwr_contacts import _is_owner

ISSUER = "Human"
STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1                      # accept one step either side for phone clock drift
SETUP_CODE_HOURS = 24
SESSION_HOURS = 12
MAX_FAILS = 5
LOCK_MINUTES = 15
ISSUE_LEVEL = 2
SETUP_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"      # no 0/O, 1/I/L mix-ups when read aloud


def _now(now=None):
    return now or datetime.now(timezone.utc)


def _iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _h(text):
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------- TOTP ----------------

def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = mac[-1] & 0x0F
    n = (struct.unpack(">I", mac[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** DIGITS
    return f"{n:0{DIGITS}d}"


def _step(now):
    return int(now.timestamp()) // STEP_SECONDS


def match_step(secret, code, now=None):
    """The time step the code belongs to, or None."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    s = _step(_now(now))
    for d in range(-WINDOW, WINDOW + 1):
        if hmac.compare_digest(totp(secret, s + d), code):
            return s + d
    return None


def otpauth_uri(secret, member, business="Human"):
    label = quote(f"{business}:{member}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"


# ---------------- events ----------------

def _event(db, member, kind, now, method=None, factor=None, step=None, device=None, ip=None, actor=None, detail=None):
    db.execute("INSERT INTO auth_events(member, kind, method, factor, step, device, ip, actor, detail, at) "
               "VALUES (?,?,?,?,?,?,?,?,?,?)", (member, kind, method, factor, step, device, ip, actor, detail, _iso(now)))


def history(db, member, limit=50):
    return [dict(r) for r in db.execute(
        "SELECT kind, method, device, ip, actor, detail, at FROM auth_events WHERE member=? ORDER BY id DESC LIMIT ?",
        (member, limit))]


def settings_block(db, members, limit=100):
    """The 'authenticator' block for the Business profile / settings page:
    {status, note, events: [{at, member, event, origin}]}. `members` is who the viewer may see."""
    members = list(members)
    enrolled = [m for m in members if factor(db, m)]
    q = ",".join("?" * len(members)) or "''"
    events = [{"at": r["at"], "member": r["member"], "event": r["kind"],
               "origin": " · ".join(x for x in (r["method"], r["device"], r["ip"], r["actor"] and f"by {r['actor']}") if x)}
              for r in db.execute(f"SELECT * FROM auth_events WHERE member IN ({q}) ORDER BY id DESC LIMIT ?", (*members, limit))]
    st = "active" if enrolled and len(enrolled) == len(members) else "partial" if enrolled else "not_set_up"
    return {"status": st, "note": f"Authenticator app (TOTP) for {len(enrolled)} of {len(members)} people", "events": events}


def factor(db, member):
    """The current authenticator (newest), or None."""
    r = db.execute("SELECT * FROM auth_factors WHERE member=? ORDER BY id DESC LIMIT 1", (member,)).fetchone()
    return dict(r) if r else None


def status(db, member):
    f = factor(db, member)
    last = db.execute("SELECT at, device FROM auth_events WHERE member=? AND kind='signed_in' ORDER BY id DESC LIMIT 1",
                      (member,)).fetchone()
    pending = _open_code(db, member, _now())
    return {"enrolled": bool(f), "method": f["method"] if f else None, "enrolled_at": f["at"] if f else None,
            "enrolled_device": f["device"] if f else None, "last_sign_in": last["at"] if last else None,
            "last_device": last["device"] if last else None, "setup_code_open": bool(pending)}


def locked_until(db, member, now=None):
    now = _now(now)
    since = _iso(now - timedelta(minutes=LOCK_MINUTES))
    last_ok = db.execute("SELECT MAX(id) FROM auth_events WHERE member=? AND kind IN ('signed_in','enrolled')",
                         (member,)).fetchone()[0] or 0
    fails = db.execute("SELECT at FROM auth_events WHERE member=? AND kind='failed' AND id>? AND at>=? ORDER BY id",
                       (member, last_ok, since)).fetchall()
    if len(fails) >= MAX_FAILS:
        return _iso(_parse(fails[MAX_FAILS - 1]["at"]) + timedelta(minutes=LOCK_MINUTES))
    return None


def _fail(db, member, now, method, device, ip, detail):
    _event(db, member, "failed", now, method=method, device=device, ip=ip, detail=detail)
    if locked_until(db, member, now):
        _event(db, member, "locked", now, method=method, device=device, ip=ip, detail=f"{MAX_FAILS} wrong codes")
    db.commit()       # failures count even though the caller rejects the request


# ---------------- setup codes ----------------

def can_issue(db, issuer, member):
    if issuer == "console":
        return True
    if issuer == member:
        return False            # a stolen session must not be able to link a new phone
    if _is_owner(db, issuer):
        return True
    lv = level(db, issuer)
    return lv >= ISSUE_LEVEL and level(db, member) < lv and not _is_owner(db, member)


def check_issue(db, issuer, member):
    if not db.execute("SELECT 1 FROM members WHERE id=?", (member,)).fetchone():
        raise LookupError(f"{member} is not on the roster")
    if not can_issue(db, issuer, member):
        if _is_owner(db, member) or issuer == member:
            raise PermissionError("Your own setup code only comes from the server computer: python -m server.signin_code "
                                  + member if issuer == member else
                                  "The account holder's setup code only comes from the server computer (console)")
        raise PermissionError(f"A setup code for {member} (level {level(db, member)}) needs someone above that level; "
                              f"only the account holder issues codes for managers")


def issue_setup_code(db, issuer, member, now=None):
    """Returns the code. It is shown once and only its hash is kept."""
    now = _now(now)
    check_issue(db, issuer, member)
    code = "".join(secrets.choice(SETUP_ALPHABET) for _ in range(8))
    db.execute("INSERT INTO auth_setup_codes(member, code_hash, issued_by, issued_at, expires_at) VALUES (?,?,?,?,?)",
               (member, _h(code), issuer, _iso(now), _iso(now + timedelta(hours=SETUP_CODE_HOURS))))
    _event(db, member, "setup_code_issued", now, method="setup_code", actor=issuer,
           detail=f"good until {_iso(now + timedelta(hours=SETUP_CODE_HOURS))}")
    return code


def _open_code(db, member, now):
    """Newest setup code for the member that is not expired and not used. Older ones stop working."""
    r = db.execute("SELECT * FROM auth_setup_codes WHERE member=? ORDER BY id DESC LIMIT 1", (member,)).fetchone()
    if not r or _parse(r["expires_at"]) <= now:
        return None
    if db.execute("SELECT 1 FROM auth_factors WHERE setup_code=?", (r["id"],)).fetchone():
        return None
    return dict(r)


def _norm_code(code):
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def check_setup_code(db, member, code, now=None, device=None, ip=None):
    """Step 2: the person typed their setup code. Returns the code row. Wrong codes count toward the lock."""
    now = _now(now)
    if locked_until(db, member, now):
        raise PermissionError(f"Too many wrong codes. Try again after {locked_until(db, member, now)[11:16]} UTC")
    r = _open_code(db, member, now)
    if not r or not hmac.compare_digest(r["code_hash"], _h(_norm_code(code))):
        _fail(db, member, now, "setup_code", device, ip, "wrong or expired setup code")
        raise PermissionError("That setup code is wrong, used or expired. Ask your manager for a new one.")
    return r


def enroll(db, vault, member, code, secret, first_totp, now=None, device=None, ip=None):
    """Step 3: link the authenticator. Needs the setup code again and the first 6-digit code from the app."""
    now = _now(now)
    r = check_setup_code(db, member, code, now, device, ip)
    step = match_step(secret, first_totp, now)
    if step is None:
        _fail(db, member, now, "totp", device, ip, "first code from the app did not match")
        raise PermissionError("That 6-digit code doesn't match. Check the phone's clock is set automatically and try again.")
    old = factor(db, member)
    cur = db.execute("INSERT INTO auth_factors(member, method, secret_enc, setup_code, device, at) VALUES (?,?,?,?,?,?)",
                     (member, "totp", vault.seal(secret), r["id"], device, _iso(now)))
    fid = cur.lastrowid
    if old:
        end_sessions(db, member, "authenticator reset", now)
        _event(db, member, "reset", now, method="totp", factor=fid, device=device, ip=ip, actor=r["issued_by"],
               detail="new authenticator replaced the old one; open sessions ended")
    _event(db, member, "enrolled", now, method="totp", factor=fid, step=step, device=device, ip=ip, actor=r["issued_by"],
           detail=f"setup code from {r['issued_by']}")
    return fid


# ---------------- sign-in and sessions ----------------

def sign_in(db, vault, member, code, now=None, device=None, ip=None):
    """Returns (token, expires_at). The token goes in an HttpOnly cookie; only its hash is stored."""
    now = _now(now)
    f = factor(db, member)
    if not f:
        raise PermissionError("No authenticator set up yet. Ask your manager for a setup code.")
    if locked_until(db, member, now):
        raise PermissionError(f"Too many wrong codes. Try again after {locked_until(db, member, now)[11:16]} UTC")
    secret = vault.open(f["secret_enc"]).decode()
    step = match_step(secret, code, now)
    last = db.execute("SELECT MAX(step) FROM auth_events WHERE factor=? AND kind IN ('signed_in','enrolled')",
                      (f["id"],)).fetchone()[0]
    if step is None or (last is not None and step <= last):
        _fail(db, member, now, "totp", device, ip, "wrong code" if step is None else "code already used")
        raise PermissionError("That code doesn't work. Wait for the next one in your authenticator app.")
    return _session(db, member, f["id"], step, now, device, ip)


def _session(db, member, fid, step, now, device, ip, kind="signed_in"):
    token = secrets.token_urlsafe(32)
    exp = now + timedelta(hours=SESSION_HOURS)
    db.execute("INSERT INTO auth_sessions(token_hash, member, factor, device, at, expires_at) VALUES (?,?,?,?,?,?)",
               (_h(token), member, fid, device, _iso(now), _iso(exp)))
    if kind == "signed_in":
        _event(db, member, "signed_in", now, method="totp", factor=fid, step=step, device=device, ip=ip)
    return token, _iso(exp)


def start_session_after_enroll(db, member, fid, now=None, device=None, ip=None):
    return _session(db, member, fid, None, _now(now), device, ip, kind="enrolled")


def session_member(db, token, now=None):
    """Who this session token belongs to, or None (unknown, expired, ended, or replaced authenticator)."""
    if not token:
        return None
    now = _now(now)
    th = _h(token)
    r = db.execute("SELECT * FROM auth_sessions WHERE token_hash=?", (th,)).fetchone()
    if not r or _parse(r["expires_at"]) <= now:
        return None
    if db.execute("SELECT 1 FROM auth_session_ends WHERE token_hash=?", (th,)).fetchone():
        return None
    f = factor(db, r["member"])
    if not f or f["id"] != r["factor"]:
        return None
    return r["member"]


def sign_out(db, token, now=None, device=None, ip=None):
    now = _now(now)
    m = session_member(db, token, now)
    if m:
        db.execute("INSERT INTO auth_session_ends(token_hash, reason, at) VALUES (?,?,?)", (_h(token), "signed out", _iso(now)))
        _event(db, m, "signed_out", now, device=device, ip=ip)
    return m


def end_sessions(db, member, reason, now=None):
    now = _now(now)
    for r in db.execute("SELECT token_hash FROM auth_sessions s WHERE member=? AND NOT EXISTS "
                        "(SELECT 1 FROM auth_session_ends e WHERE e.token_hash=s.token_hash)", (member,)).fetchall():
        db.execute("INSERT INTO auth_session_ends(token_hash, reason, at) VALUES (?,?,?)", (r["token_hash"], reason, _iso(now)))


def device_label(user_agent):
    """A short, readable device name from the browser's User-Agent, e.g. 'Chrome on Android'."""
    ua = user_agent or ""
    os_ = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
                               ("Mac OS X", "Mac"), ("CrOS", "Chromebook"), ("Linux", "Linux")) if k in ua), "unknown device")
    br = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser", "Samsung Internet"), ("CriOS", "Chrome"),
                              ("FxiOS", "Firefox"), ("Chrome/", "Chrome"), ("Firefox/", "Firefox"), ("Safari/", "Safari"),
                              ("python-httpx", "test client"), ("testclient", "test client")) if k in ua), "browser")
    return f"{br} on {os_}"


__all__ = ["issue_setup_code", "check_setup_code", "enroll", "sign_in", "sign_out", "session_member", "history", "status",
           "new_secret", "otpauth_uri", "totp", "device_label", "LEVEL_NAMES"]

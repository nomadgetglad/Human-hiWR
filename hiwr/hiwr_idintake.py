"""Employee ID intake: the identity details taken when someone is hired (I-9 style).

What is kept, sealed with hiwr_vault (encrypted at rest):
  legal name, date of birth, ID document type + number + issuer, home address, emergency contact,
  and an optional photo of the ID (JPEG or PNG, up to 5 MB).
What stays readable (for reminders and the roster): document type, expiry date, whether work
authorization was checked in person, who entered it and when.

Who does what:
  - Managers (level 2) and up enter ID for people below their level. The account holder enters for anyone.
    People can't enter their own (someone else has to look at the document).
  - Only Finance (level 3) and the account holder open the details or the photo, and every opening is logged.
    Managers see the status only: on file, document type, expires, verified.
  - Task grants never unlock this: it reads the person's level, nothing else.
  - The app still shows first names only. The legal name lives here, sealed.
  - Nothing is overwritten. A correction is a new version; the newest one counts.
  - Social Security numbers are not taken here. They belong with the payroll provider.
"""
import base64
import json
import re
from datetime import date, datetime, timedelta, timezone

from hiwr_access import level, LEVEL_NAMES
from hiwr_contacts import _is_owner

ENTER_LEVEL = 2
VIEW_LEVEL = 3
DOC_TYPES = {
    "drivers_license": "Driver's license",
    "state_id": "State ID card",
    "passport": "Passport",
    "passport_card": "Passport card",
    "permanent_resident_card": "Permanent resident card",
    "employment_authorization": "Employment authorization card",
    "other": "Other ID",
}
MIN_AGE = 14
MAX_PHOTO_BYTES = 5 * 1024 * 1024
DOC_NUMBER = re.compile(r"^[A-Z0-9-]{4,20}$")
PHONE = re.compile(r"^\+?[0-9 ().-]{7,20}$")
EXPIRY_WARN_DAYS = 30


def _now(now=None):
    return now or datetime.now(timezone.utc)


def _iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(value, what):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"{what}: use a date like 1990-05-31")


def _text(value, what, max_len, required=True):
    v = " ".join(str(value or "").split())
    if required and not v:
        raise ValueError(f"{what} is required")
    if len(v) > max_len:
        raise ValueError(f"{what}: up to {max_len} characters")
    return v


def can_enter(db, actor, member):
    if actor == member:
        return False
    if _is_owner(db, actor):
        return True
    lv = level(db, actor)
    return lv >= ENTER_LEVEL and level(db, member) < lv and not _is_owner(db, member)


def can_view(db, actor):
    return _is_owner(db, actor) or level(db, actor) >= VIEW_LEVEL


def check_intake(db, actor, member, fields, photo_b64=None, today=None):
    """Validate without writing. Returns (clean_fields, photo_bytes). PermissionError / ValueError."""
    today = today or _now().date()
    if not db.execute("SELECT 1 FROM members WHERE id=?", (member,)).fetchone():
        raise LookupError(f"{member} is not on the roster")
    if actor == member:
        raise PermissionError("Someone else has to look at your ID and enter it")
    if not can_enter(db, actor, member):
        raise PermissionError(f"Entering ID for {member} needs Manager level and a level above theirs "
                              f"({actor} is {LEVEL_NAMES[level(db, actor)]})")
    f = fields or {}
    doc_type = f.get("doc_type")
    if doc_type not in DOC_TYPES:
        raise ValueError("Pick the kind of ID: " + ", ".join(DOC_TYPES.values()))
    birth = _date(f.get("birth_date"), "Date of birth")
    age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    if not MIN_AGE <= age <= 100:
        raise ValueError(f"Date of birth gives age {age}; workers must be {MIN_AGE} or older")
    expires = None
    if f.get("expires_on") not in (None, ""):
        expires = _date(f.get("expires_on"), "Expiry date")
        if expires <= today:
            raise ValueError("This ID is expired. Ask for a current one")
    elif doc_type != "other":
        raise ValueError("Expiry date is required for this kind of ID")
    number = re.sub(r"\s+", "", str(f.get("doc_number") or "")).upper()
    if not DOC_NUMBER.match(number):
        raise ValueError("Document number: 4 to 20 letters, digits or dashes")
    clean = {
        "legal_name": _text(f.get("legal_name"), "Legal name (as on the ID)", 80),
        "birth_date": birth.isoformat(),
        "doc_type": doc_type,
        "doc_number": number,
        "issuer": _text(f.get("issuer"), "Issued by (state or country)", 40),
        "expires_on": expires.isoformat() if expires else None,
        "address": _text(f.get("address"), "Home address", 160, required=False),
        "emergency_name": _text(f.get("emergency_name"), "Emergency contact name", 60, required=False),
        "emergency_phone": _text(f.get("emergency_phone"), "Emergency contact phone", 20, required=False),
        "work_auth_verified": bool(f.get("work_auth_verified")),
    }
    if clean["emergency_phone"] and not PHONE.match(clean["emergency_phone"]):
        raise ValueError("Emergency contact phone: digits, spaces, + ( ) - only")
    photo = None
    if photo_b64:
        if "," in photo_b64[:80] and photo_b64.startswith("data:"):
            photo_b64 = photo_b64.split(",", 1)[1]
        if len(photo_b64) > MAX_PHOTO_BYTES * 4 // 3 + 4:
            raise ValueError("Photo is over 5 MB")
        try:
            photo = base64.b64decode(photo_b64, validate=True)
        except ValueError:
            raise ValueError("Photo could not be read")
        if len(photo) > MAX_PHOTO_BYTES:
            raise ValueError("Photo is over 5 MB")
        if not (photo[:3] == b"\xff\xd8\xff" or photo[:8] == b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Photo must be a JPEG or PNG")
    return clean, photo


def record_intake(db, vault, actor, member, fields, photo_b64=None, now=None):
    now = _now(now)
    clean, photo = check_intake(db, actor, member, fields, photo_b64, now.date())
    sealed = {k: v for k, v in clean.items() if k not in ("doc_type", "expires_on", "work_auth_verified")}
    cur = db.execute("INSERT INTO id_records(member, doc_type, expires_on, work_auth_verified, data_enc, photo_enc, entered_by, at) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     (member, clean["doc_type"], clean["expires_on"], int(clean["work_auth_verified"]),
                      vault.seal(json.dumps(sealed)), vault.seal(photo) if photo else None, actor, _iso(now)))
    db.execute("INSERT INTO id_access_log(member, viewer, what, at) VALUES (?,?,?,?)", (member, actor, "entered", _iso(now)))
    return cur.lastrowid


def _latest(db, member):
    return db.execute("SELECT * FROM id_records WHERE member=? ORDER BY id DESC LIMIT 1", (member,)).fetchone()


def id_status(db, member, today=None):
    """What a manager may see: on file or not, kind, expiry, verified. No numbers, no names."""
    today = today or _now().date()
    r = _latest(db, member)
    if not r:
        return {"on_file": False}
    out = {"on_file": True, "doc_type": r["doc_type"], "doc_label": DOC_TYPES[r["doc_type"]], "expires_on": r["expires_on"],
           "work_auth_verified": bool(r["work_auth_verified"]), "entered_by": r["entered_by"], "entered_at": r["at"],
           "has_photo": r["photo_enc"] is not None, "versions": db.execute(
               "SELECT COUNT(*) FROM id_records WHERE member=?", (member,)).fetchone()[0]}
    if r["expires_on"]:
        days = (date.fromisoformat(r["expires_on"]) - today).days
        out["expires_in_days"] = days
        out["expiring"] = days <= EXPIRY_WARN_DAYS
    return out


def view_details(db, vault, viewer, member, now=None):
    """Finance and the account holder only. Logged every time."""
    now = _now(now)
    if not can_view(db, viewer):
        raise PermissionError(f"ID details need {LEVEL_NAMES[VIEW_LEVEL]} level or the account holder")
    r = _latest(db, member)
    if not r:
        raise LookupError(f"No ID on file for {member}")
    db.execute("INSERT INTO id_access_log(member, viewer, what, at) VALUES (?,?,?,?)", (member, viewer, "details", _iso(now)))
    return {**json.loads(vault.open(r["data_enc"])), **id_status(db, member, now.date())}


def view_photo(db, vault, viewer, member, now=None):
    now = _now(now)
    if not can_view(db, viewer):
        raise PermissionError(f"The ID photo needs {LEVEL_NAMES[VIEW_LEVEL]} level or the account holder")
    r = _latest(db, member)
    if not r or not r["photo_enc"]:
        raise LookupError(f"No ID photo on file for {member}")
    db.execute("INSERT INTO id_access_log(member, viewer, what, at) VALUES (?,?,?,?)", (member, viewer, "photo", _iso(now)))
    data = vault.open(r["photo_enc"])
    return data, "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"


def access_log(db, member):
    return [dict(r) for r in db.execute("SELECT viewer, what, at FROM id_access_log WHERE member=? ORDER BY id DESC", (member,))]


def expiring(db, today=None):
    """People whose newest ID expires within 30 days (or has expired): a reminder for managers."""
    today = today or _now().date()
    out = []
    for (m,) in db.execute("SELECT DISTINCT member FROM id_records"):
        s = id_status(db, m, today)
        if s.get("expiring"):
            out.append({"member": m, "expires_on": s["expires_on"], "days": s["expires_in_days"]})
    return out


def mask(number):
    return "•" * max(len(number) - 4, 0) + number[-4:]

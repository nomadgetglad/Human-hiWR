"""Roster: adding the people who work here. This is where an account starts.

Rules:
  - Managers (level 2) and up add people. New people start at level 0 (Staff).
  - Only the account holder gives a level above 0 (hiwr_access.set_level enforces it).
  - Pay is payroll data, so only Finance level (3) and up put someone on the schedule roster with a rate.
  - First names only, no last names. The id is made from the title and first name (cook_maria).
  - Nothing is edited or deleted. Adding someone writes a member row and an 'active' status event.
"""
import re
import unicodedata

from hiwr_access import level, set_level, LEVEL_NAMES
from hiwr_contacts import _is_owner
from hiwr_roles import role_for

ADD_LEVEL = 2                 # Manager and up add people
PAY_LEVEL = 3                 # Finance and up set pay (payroll data)
LANGUAGES = ("en", "es")
MIN_HOURLY_CENTS = 213        # federal tipped minimum; untipped staff need 725 or more
MAX_HOURLY_CENTS = 20000
FIRST_NAME = re.compile(r"^[^\W\d_]+(?:['’-][^\W\d_]+)*$")


def _slug(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def _new_id(db, title, first_name):
    base = f"{_slug(title.split()[0].split(',')[0]) or 'staff'}_{_slug(first_name) or 'member'}"
    mid, n = base, 1
    while db.execute("SELECT 1 FROM members WHERE id=?", (mid,)).fetchone():
        n += 1
        mid = f"{base}_{n}"
    return mid


def check_new_member(db, first_name, title, added_by, language="en", lvl=0, role=None, hourly_cents=None,
                     min_call_in_hours=0, member_id=None):
    """Check a proposed new member without writing anything. Returns the cleaned values.
    Raises PermissionError (not allowed) or ValueError (bad input)."""
    adder_level = level(db, added_by)
    if not _is_owner(db, added_by) and adder_level < ADD_LEVEL:
        raise PermissionError(f"Adding staff needs level {ADD_LEVEL} ({LEVEL_NAMES[ADD_LEVEL]}); "
                              f"{added_by} is level {adder_level}")
    first_name = (first_name or "").strip()
    if not first_name:
        raise ValueError("First name is required")
    if " " in first_name:
        raise ValueError("First name only: no last names in the app")
    if len(first_name) > 30 or not FIRST_NAME.match(first_name):
        raise ValueError("First name: letters only (a hyphen or apostrophe is fine), up to 30")
    title = " ".join((title or "").split())
    if not title or len(title) > 40:
        raise ValueError("Job title is required, up to 40 characters")
    if language not in LANGUAGES:
        raise ValueError("Language must be en or es")
    lvl = int(lvl or 0)
    if not 0 <= lvl <= 3:
        raise ValueError("Level must be 0 to 3; level 4 is the account holder (add a co-owner instead)")
    if lvl > 0 and not _is_owner(db, added_by):
        raise PermissionError("Only the account holder gives a level above 0 (Staff)")
    role = " ".join((role or "").split()).lower() or None
    if role:
        role = role_for(role, db) or role     # "Line Cook" is saved as cook, so staffing rules find them
    if role or hourly_cents not in (None, "", 0):
        if adder_level < PAY_LEVEL:
            raise PermissionError(f"Pay is payroll data: putting someone on the schedule with a rate needs level "
                                  f"{PAY_LEVEL} ({LEVEL_NAMES[PAY_LEVEL]})")
        if not role:
            raise ValueError("Pick the schedule role (cook, dishwasher...) that goes with the pay rate")
        if hourly_cents in (None, ""):
            raise ValueError("A schedule role needs an hourly rate")
        hourly_cents = int(hourly_cents)
        if not MIN_HOURLY_CENTS <= hourly_cents <= MAX_HOURLY_CENTS:
            raise ValueError(f"Hourly rate must be ${MIN_HOURLY_CENTS / 100:.2f} to ${MAX_HOURLY_CENTS / 100:.2f}")
        if not 0 <= float(min_call_in_hours or 0) <= 8:
            raise ValueError("Call-in pay hours must be 0 to 8")
    else:
        role, hourly_cents = None, None
    if member_id is not None and db.execute("SELECT 1 FROM members WHERE id=?", (member_id,)).fetchone():
        raise ValueError(f"{member_id} is already on the roster")
    return {"id": member_id or _new_id(db, title, first_name), "first_name": first_name, "title": title,
            "language": language, "level": lvl, "role": role, "hourly_cents": hourly_cents,
            "min_call_in_hours": float(min_call_in_hours or 0)}


def add_member(db, first_name, title, added_by, language="en", lvl=0, role=None, hourly_cents=None,
               min_call_in_hours=0, member_id=None, reason=None):
    """Add someone to the roster. Returns their id."""
    v = check_new_member(db, first_name, title, added_by, language, lvl, role, hourly_cents, min_call_in_hours, member_id)
    db.execute("INSERT INTO members(id, first_name, title, language, added_by) VALUES (?,?,?,?,?)",
               (v["id"], v["first_name"], v["title"], v["language"], added_by))
    db.execute("INSERT INTO member_status_events(member, status, actor, reason) VALUES (?, 'active', ?, ?)",
               (v["id"], added_by, reason or "added"))
    if v["level"] > 0:
        set_level(db, v["id"], v["level"], added_by)
    if v["role"]:
        db.execute("INSERT INTO staff(member, role, hourly_cents, min_call_in_hours) VALUES (?,?,?,?)",
                   (v["id"], v["role"], v["hourly_cents"], v["min_call_in_hours"]))
    return v["id"]


def is_active(db, member):
    r = db.execute("SELECT status FROM member_status_events WHERE member=? ORDER BY id DESC LIMIT 1", (member,)).fetchone()
    return bool(r and r["status"] == "active")


def get_member(db, member):
    r = db.execute("SELECT * FROM members WHERE id=?", (member,)).fetchone()
    if not r or not is_active(db, member):
        return None
    return {"id": r["id"], "name": r["first_name"], "title": r["title"], "language": r["language"]}


def roster(db, show_pay=False):
    """Everyone active, in the order they were added. Pay only when show_pay (Finance level and up)."""
    out = []
    for r in db.execute("SELECT m.*, s.role, s.hourly_cents FROM members m LEFT JOIN staff s ON s.member=m.id ORDER BY m.rowid"):
        if not is_active(db, r["id"]):
            continue
        lvl = level(db, r["id"])
        row = {"id": r["id"], "name": r["first_name"], "title": r["title"], "language": r["language"],
               "level": lvl, "level_name": LEVEL_NAMES[lvl], "role": r["role"], "added_by": r["added_by"],
               "added_at": r["added_at"]}
        if show_pay:
            row["hourly_cents"] = r["hourly_cents"]
        out.append(row)
    return out

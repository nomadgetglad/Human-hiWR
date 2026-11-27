"""HiWR client contact data system: core functions over schema.sql (SQLite, stdlib only).

One client record drives appointments, shipments and orders. Each business type
(restaurant live orders, made-to-order boots, doctor's office check-ins) is a
workflow: an ordered list of stages. Every change is appended, never overwritten.
"""
import json
import re
import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")

WORKFLOWS = {
    "restaurant_live": [
        ("received", None), ("fired", None), ("cooking", "line"),
        ("ready", "pass"), ("served_or_picked_up", None),
    ],
    "boots_made_to_order": [
        ("measured", "fitting"), ("pattern", "pattern"), ("cut", "cutting"),
        ("stitched", "stitching"), ("lasted", "lasting"), ("soled", "bottoming"),
        ("finished", "finishing"), ("qc_passed", "qc"), ("ready", None), ("delivered_or_shipped", None),
    ],
    "clinic_checkin": [
        ("booked", None), ("confirmed", None), ("arrived", "front_desk"),
        ("checked_in", "front_desk"), ("roomed", None), ("seen", None), ("checked_out", "front_desk"),
    ],
}
# Stages allowed at any point, outside the normal order.
EXIT_STAGES = {"no_show", "cancelled", "remade", "rejected_qc"}


def connect(path=":memory:", **sqlite_kwargs):
    db = sqlite3.connect(path, **sqlite_kwargs)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA.read_text())
    for name, stages in WORKFLOWS.items():
        db.execute("INSERT OR IGNORE INTO workflows(name) VALUES (?)", (name,))
        wid = db.execute("SELECT id FROM workflows WHERE name=?", (name,)).fetchone()[0]
        for seq, (stage, station) in enumerate(stages, 1):
            db.execute("INSERT OR IGNORE INTO workflow_stages VALUES (?,?,?,?)", (wid, seq, stage, station))
    return db


# ---------- phones and contacts ----------

def normalize_phone(raw, default_country="1"):
    """'(910) 555-1234' -> '+19105551234'. Raises ValueError if it can't be a phone number."""
    digits = re.sub(r"\D", "", raw or "")
    if raw and raw.strip().startswith("+"):
        e164 = "+" + digits
    elif len(digits) == 10:
        e164 = "+" + default_country + digits
    elif len(digits) == 11 and digits.startswith(default_country):
        e164 = "+" + digits
    else:
        raise ValueError(f"not a valid phone number: {raw!r}")
    if not 8 <= len(e164) <= 16:
        raise ValueError(f"not a valid phone number: {raw!r}")
    return e164


def _event(db, contact_id, actor, event, detail=None, ticket_id=None):
    db.execute(
        "INSERT INTO contact_events(contact_id, actor, event, detail, ticket_id) VALUES (?,?,?,?,?)",
        (contact_id, actor, event, json.dumps(detail) if detail else None, ticket_id),
    )


def find_duplicates(db, name, phone=None):
    """Possible existing records: same phone, or same name ignoring case/spaces."""
    hits = {}
    if phone:
        for r in db.execute(
            "SELECT c.* FROM contacts c JOIN contact_phones p ON p.contact_id=c.id "
            "WHERE p.e164=? AND c.merged_into IS NULL", (normalize_phone(phone),)):
            hits[r["id"]] = ("same phone", r)
    key = re.sub(r"\s+", " ", name).strip().lower()
    for r in db.execute("SELECT * FROM contacts WHERE merged_into IS NULL"):
        if re.sub(r"\s+", " ", r["display_name"]).strip().lower() == key and r["id"] not in hits:
            hits[r["id"]] = ("same name", r)
    return [{"id": cid, "reason": why, "name": r["display_name"]} for cid, (why, r) in hits.items()]


def add_contact(db, name, actor, phone=None, kind="client", language="en", notes=None, force=False):
    """Create a contact. Returns {'id':..} or {'duplicates':[..]} for a person to confirm first."""
    if not force:
        dups = find_duplicates(db, name, phone)
        if dups:
            return {"duplicates": dups}
    cur = db.execute(
        "INSERT INTO contacts(kind, display_name, preferred_language, notes, created_by) VALUES (?,?,?,?,?)",
        (kind, name.strip(), language, notes, actor))
    cid = cur.lastrowid
    _event(db, cid, actor, "created", {"name": name})
    if phone:
        add_phone(db, cid, phone, actor, primary=True)
    return {"id": cid}


def add_phone(db, contact_id, raw, actor, label="mobile", primary=False):
    e164 = normalize_phone(raw)
    if primary:
        db.execute("UPDATE contact_phones SET is_primary=0 WHERE contact_id=?", (contact_id,))
    db.execute("INSERT OR IGNORE INTO contact_phones(contact_id, e164, label, is_primary) VALUES (?,?,?,?)",
               (contact_id, e164, label, int(primary)))
    _event(db, contact_id, actor, "phone_added", {"phone": e164, "label": label})
    return e164


def merge_contacts(db, keep_id, dup_id, actor):
    """Point the duplicate at the kept record and move its links. Nothing is deleted."""
    for table in ("contact_phones", "appointments", "shipments", "orders"):
        # OR IGNORE: a phone both records already share stays on the duplicate row, which is marked merged below
        db.execute(f"UPDATE OR IGNORE {table} SET contact_id=? WHERE contact_id=?", (keep_id, dup_id))
    db.execute("UPDATE contacts SET merged_into=? WHERE id=?", (keep_id, dup_id))
    _event(db, keep_id, actor, "merged", {"from": dup_id})
    _event(db, dup_id, actor, "merged_into", {"into": keep_id})


# ---------- SMS consent ----------

def record_consent(db, phone, purpose, status, source, actor):
    db.execute("INSERT INTO sms_consent(e164, purpose, status, source, recorded_by) VALUES (?,?,?,?,?)",
               (normalize_phone(phone), purpose, status, source, actor))


def handle_inbound_sms(db, phone, body, actor="sms_gateway"):
    """STOP-type replies revoke every purpose at once."""
    if body.strip().upper() in {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "ALTO", "PARAR"}:
        for purpose in ("transactional", "marketing"):
            record_consent(db, phone, purpose, "revoked", "STOP reply", actor)
        return "revoked"
    return "logged"


def can_text(db, phone, purpose="transactional"):
    row = db.execute("SELECT status FROM v_current_consent WHERE e164=? AND purpose=?",
                     (normalize_phone(phone), purpose)).fetchone()
    return bool(row and row["status"] == "granted")


# ---------- workflows: orders and appointments ----------

def _stages(db, workflow):
    return [r["stage"] for r in db.execute(
        "SELECT ws.stage FROM workflow_stages ws JOIN workflows w ON w.id=ws.workflow_id "
        "WHERE w.name=? ORDER BY ws.seq", (workflow,))]


def _check_move(stages, current, new):
    if new in EXIT_STAGES:
        return
    if new not in stages:
        raise ValueError(f"unknown stage {new!r}; expected one of {stages}")
    if current in stages and stages.index(new) < stages.index(current):
        raise ValueError(f"cannot move back from {current!r} to {new!r}; log 'remade' instead")


def create_order(db, workflow, fulfillment, items, actor, contact_id=None, due_at=None, source_ref=None):
    wid = db.execute("SELECT id FROM workflows WHERE name=?", (workflow,)).fetchone()["id"]
    oid = db.execute(
        "INSERT INTO orders(contact_id, workflow_id, due_at, fulfillment, source_ref) VALUES (?,?,?,?,?)",
        (contact_id, wid, due_at, fulfillment, source_ref)).lastrowid
    for it in items:
        db.execute("INSERT INTO order_items(order_id, item, qty, specs) VALUES (?,?,?,?)",
                   (oid, it["item"], it.get("qty", 1), json.dumps(it.get("specs")) if it.get("specs") else None))
    advance_order(db, oid, _stages(db, workflow)[0], actor)
    return oid


def advance_order(db, order_id, stage, actor, item_id=None, note=None):
    wf = db.execute("SELECT w.name FROM orders o JOIN workflows w ON w.id=o.workflow_id WHERE o.id=?",
                    (order_id,)).fetchone()["name"]
    cur = db.execute(
        "SELECT stage FROM order_stage_events WHERE order_id=? AND (item_id IS ? OR item_id IS NULL) "
        "ORDER BY at DESC, id DESC LIMIT 1", (order_id, item_id)).fetchone()
    _check_move(_stages(db, wf), cur["stage"] if cur else None, stage)
    db.execute("INSERT INTO order_stage_events(order_id, item_id, stage, actor, note) VALUES (?,?,?,?,?)",
               (order_id, item_id, stage, actor, note))


def book_appointment(db, contact_id, starts_at, actor, kind="reservation", workflow=None, party_size=None, details=None):
    if workflow == "clinic_checkin" and details:
        raise ValueError("clinic appointments must not store visit reasons or clinical details")
    aid = db.execute(
        "INSERT INTO appointments(contact_id, starts_at, kind, workflow, party_size, details) VALUES (?,?,?,?,?,?)",
        (contact_id, starts_at, kind, workflow, party_size, details)).lastrowid
    if workflow:
        advance_appointment(db, aid, _stages(db, workflow)[0], actor)
    return aid


def advance_appointment(db, appointment_id, stage, actor, note=None):
    wf = db.execute("SELECT workflow FROM appointments WHERE id=?", (appointment_id,)).fetchone()["workflow"]
    cur = db.execute("SELECT stage FROM appointment_events WHERE appointment_id=? ORDER BY at DESC, id DESC LIMIT 1",
                     (appointment_id,)).fetchone()
    _check_move(_stages(db, wf), cur["stage"] if cur else None, stage)
    db.execute("INSERT INTO appointment_events(appointment_id, stage, actor, note) VALUES (?,?,?,?)",
               (appointment_id, stage, actor, note))


# ---------- role-based reads ----------

FULL_PHONE_ROLES = {"manager", "owner", "front_desk", "accounting"}


def get_contact(db, contact_id, actor, role):
    """Every read is logged. Roles outside FULL_PHONE_ROLES see the masked staff view."""
    db.execute("INSERT INTO access_log(actor, role, contact_id, action) VALUES (?,?,?,?)",
               (actor, role, contact_id, "view"))
    if role in FULL_PHONE_ROLES:
        c = dict(db.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone())
        c["phones"] = [dict(r) for r in db.execute(
            "SELECT e164, label, is_primary FROM contact_phones WHERE contact_id=?", (contact_id,))]
        return c
    return dict(db.execute("SELECT * FROM v_staff_contacts WHERE id=?", (contact_id,)).fetchone())


# ---------- authority: certified approval, check-ins, assignments ----------

APPROVALS_NEEDED = 2
MONEY_APPROVER_LEVEL = 3      # Finance; matches hiwr_access OWNER_ONLY_TASKS


def add_plan_owner(db, member, added_by):
    """The first owner is set up once (empty table). After that only an existing owner adds another."""
    has_owner = db.execute("SELECT 1 FROM plan_owners LIMIT 1").fetchone() is not None
    if has_owner and not _is_owner(db, added_by):
        raise PermissionError(f"{added_by} is not a plan owner and cannot add owners")
    db.execute("INSERT OR IGNORE INTO plan_owners(member, added_by) VALUES (?,?)", (member, added_by))


def _is_owner(db, member):
    return db.execute("SELECT 1 FROM plan_owners WHERE member=?", (member,)).fetchone() is not None


def certify(db, member, scope, granted_by, status="granted"):
    if not _is_owner(db, granted_by):
        raise PermissionError(f"{granted_by} is not a plan owner and cannot certify approvers")
    from hiwr_access import level
    if status == "granted" and level(db, member) < MONEY_APPROVER_LEVEL:
        raise PermissionError(f"{member} is level {level(db, member)}; money approvers need Finance level "
                              f"({MONEY_APPROVER_LEVEL}) or higher, so they can see what they approve")
    db.execute("INSERT INTO approver_certifications(member, scope, status, granted_by) VALUES (?,?,?,?)",
               (member, scope, status, granted_by))


def is_certified(db, member, scope):
    """Certified if granted here, or holding '<scope>_approval' in the access system (hiwr_access).
    Either way the member must be at Finance level right now: a demotion switches approval off."""
    from hiwr_access import level
    if level(db, member) < MONEY_APPROVER_LEVEL:
        return False
    r = db.execute("SELECT status FROM approver_certifications WHERE member=? AND scope=? "
                   "ORDER BY at DESC, id DESC LIMIT 1", (member, scope)).fetchone()
    if r:
        return r["status"] == "granted"
    from hiwr_access import active_grant
    from datetime import date
    return active_grant(db, member, f"{scope}_approval", date.today().isoformat()) is not None


def request_money_action(db, scope, amount_cents, description, requested_by, ticket_id=None):
    # 0 is allowed: a waiver (e.g. a late fee waived) still needs two approvers. Negative never is.
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents < 0:
        raise ValueError("a money action is a whole number of cents, 0 or more; the scope says which way it moves")
    return db.execute(
        "INSERT INTO money_actions(scope, amount_cents, description, requested_by, ticket_id) VALUES (?,?,?,?,?)",
        (scope, amount_cents, description, requested_by, ticket_id)).lastrowid


def approve(db, money_action_id, approver, decision="approve"):
    act = db.execute("SELECT * FROM money_actions WHERE id=?", (money_action_id,)).fetchone()
    if act is None:
        raise LookupError(f"no money action {money_action_id}")
    if decision not in ("approve", "reject"):
        raise ValueError("decision is approve or reject")
    if not is_certified(db, approver, act["scope"]):
        raise PermissionError(f"{approver} is not certified for {act['scope']} approvals")
    if approver == act["requested_by"]:
        raise PermissionError("the person who requested a money action cannot approve it")
    status = money_action_status(db, money_action_id)
    if not status.startswith("PENDING"):
        raise PermissionError(f"money action {money_action_id} is already {status}; decisions are final")
    if db.execute("SELECT 1 FROM approvals WHERE money_action_id=? AND approver=?",
                  (money_action_id, approver)).fetchone():
        raise PermissionError(f"{approver} already decided on money action {money_action_id}; "
                              "the second approval has to come from someone else")
    db.execute("INSERT INTO approvals(money_action_id, approver, decision) VALUES (?,?,?)",
               (money_action_id, approver, decision))
    return money_action_status(db, money_action_id)


def money_action_status(db, money_action_id):
    rows = db.execute("SELECT approver, decision FROM approvals WHERE money_action_id=?",
                      (money_action_id,)).fetchall()
    if any(r["decision"] == "reject" for r in rows):
        return "REJECTED"
    n = sum(r["decision"] == "approve" for r in rows)
    if n >= APPROVALS_NEEDED:
        return "APPROVED"
    return f"PENDING - requires double approval ({n}/{APPROVALS_NEEDED})"


def check_in(db, subject, checked_by, works, intended_consequence, note=None):
    db.execute("INSERT INTO result_checkins(subject, checked_by, works, intended_consequence, note) VALUES (?,?,?,?,?)",
               (subject, checked_by, int(works), int(intended_consequence), note))
    return "OK" if works and intended_consequence else "NEEDS REVIEW"


def assign(db, assigned_by, assignee, kind, title, due_on=None, goal=None, contact_id=None, appointment_id=None):
    if not _is_owner(db, assigned_by):
        raise PermissionError(f"{assigned_by} is not a plan owner and cannot assign work")
    aid = db.execute(
        "INSERT INTO assignments(assigned_by, assignee, kind, title, due_on, goal, contact_id, appointment_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (assigned_by, assignee, kind, title, due_on, goal, contact_id, appointment_id)).lastrowid
    db.execute("INSERT INTO assignment_events(assignment_id, status, actor) VALUES (?, 'assigned', ?)", (aid, assigned_by))
    return aid


def update_assignment(db, assignment_id, status, actor, note=None):
    db.execute("INSERT INTO assignment_events(assignment_id, status, actor, note) VALUES (?,?,?,?)",
               (assignment_id, status, actor, note))


def my_day(db, member, day):
    """A member's list for the day with each item's latest status."""
    return [dict(r) for r in db.execute(
        "SELECT a.id, a.kind, a.title, a.goal, a.due_on, "
        " (SELECT status FROM assignment_events e WHERE e.assignment_id=a.id ORDER BY e.at DESC, e.id DESC LIMIT 1) AS status "
        "FROM assignments a WHERE a.assignee=? AND (a.due_on=? OR a.due_on IS NULL) ORDER BY a.kind", (member, day))]

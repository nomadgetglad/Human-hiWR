"""Transparency levels and task certification for HiWR.

Levels (set only by the account holder):
  0 Staff        own schedule, own tasks, ticket status, client first names for today's work
  1 Lead         team schedule, inventory counts, roll call sheets
  2 Manager      full client records, order costs, vendor prices, purchase drafts
  3 Finance      payroll, ticket financial blocks, money actions
  4 Account holder  everything, including setting levels

Tasks (inventory audit, roll call, receiving check, ...) are certified separately from levels.
  - The account holder can certify anyone for any task, and may let them certify others.
  - A certified person can pass on only a task they hold right now, never the right to pass it on
    again, and never past their own expiry date.
  - Money approvals (refund, payroll, fee) and levels are granted by the account holder only,
    and money approvals only to people at Finance level or above.
  - Revoking a certifier switches off every grant that came through them.
Every grant, revocation and sensitive read is logged; nothing is deleted.
"""
from hiwr_contacts import _is_owner

LEVEL_NAMES = {0: "Staff", 1: "Lead", 2: "Manager", 3: "Finance", 4: "Account holder"}
DATA_LEVEL = {"own_schedule": 0, "ticket_status": 0, "team_schedule": 1, "inventory_counts": 1, "roll_call": 1,
              "client_records": 2, "order_costs": 2, "vendor_prices": 2, "purchase_drafts": 2,
              "payroll": 3, "financial_block": 3, "money_actions": 3, "permissions": 4}
OWNER_ONLY_TASKS = {"refund_approval", "payroll_approval", "fee_approval", "subcontract_approval"}


def level(db, member):
    if _is_owner(db, member):
        return 4
    r = db.execute("SELECT level FROM member_levels WHERE member=? ORDER BY id DESC LIMIT 1", (member,)).fetchone()
    return r["level"] if r else 0


def set_level(db, member, lvl, set_by):
    if not _is_owner(db, set_by):
        raise PermissionError(f"only the account holder sets levels ({set_by} is not)")
    if lvl >= 4:
        raise PermissionError("level 4 is the account holder; add a co-owner instead")
    db.execute("INSERT INTO member_levels(member, level, set_by) VALUES (?,?,?)", (member, lvl, set_by))


def _grant_valid(db, g, on_day, seen=None):
    """A grant counts if it isn't revoked or expired and whoever gave it still holds the task (or owns the account)."""
    if db.execute("SELECT 1 FROM task_revocations WHERE grant_id=?", (g["id"],)).fetchone():
        return False
    if g["expires_on"] and g["expires_on"] < on_day:
        return False
    if _is_owner(db, g["granted_by"]):
        return True
    seen = (seen or set()) | {g["id"]}
    for parent in db.execute("SELECT * FROM task_grants WHERE member=? AND task=? AND can_delegate=1",
                             (g["granted_by"], g["task"])).fetchall():
        if parent["id"] not in seen and parent["at"] <= g["at"] and _grant_valid(db, parent, on_day, seen):
            return True
    return False


def active_grant(db, member, task, on_day):
    for g in db.execute("SELECT * FROM task_grants WHERE member=? AND task=? ORDER BY id DESC", (member, task)):
        if _grant_valid(db, g, on_day):
            return g
    return None


def can_do(db, member, task, on_day):
    return _is_owner(db, member) or active_grant(db, member, task, on_day) is not None


def grant_task(db, member, task, granted_by, on_day, can_delegate=False, expires_on=None):
    if task in OWNER_ONLY_TASKS and level(db, member) < 3:
        raise PermissionError(f"{task} needs Finance level (3) or higher, so the approver can see what they approve")
    if not _is_owner(db, granted_by):
        if task in OWNER_ONLY_TASKS:
            raise PermissionError(f"{task} can only be granted by the account holder")
        mine = active_grant(db, granted_by, task, on_day)
        if not mine:
            raise PermissionError(f"{granted_by} does not hold {task}, so cannot certify others for it")
        if not mine["can_delegate"]:
            raise PermissionError(f"{granted_by} holds {task} but was not allowed to certify others")
        if can_delegate:
            raise PermissionError("only the account holder can let someone certify others")
        if mine["expires_on"] and (not expires_on or expires_on > mine["expires_on"]):
            expires_on = mine["expires_on"]          # never outlasts the certifier's own grant
    return db.execute("INSERT INTO task_grants(member, task, granted_by, can_delegate, expires_on) VALUES (?,?,?,?,?)",
                      (member, task, granted_by, int(can_delegate), expires_on)).lastrowid


def revoke(db, grant_id, revoked_by, reason=None):
    g = db.execute("SELECT * FROM task_grants WHERE id=?", (grant_id,)).fetchone()
    if not (_is_owner(db, revoked_by) or revoked_by == g["granted_by"]):
        raise PermissionError("only the account holder or the person who gave it can revoke a grant")
    db.execute("INSERT INTO task_revocations(grant_id, revoked_by, reason) VALUES (?,?,?)", (grant_id, revoked_by, reason))


def can_see(db, member, data):
    return level(db, member) >= DATA_LEVEL[data]


def read(db, member, data, fetch, mask=None):
    """Return fetch() if the member's level allows, mask() if a reduced view exists, else refuse. Logged."""
    allowed = can_see(db, member, data)
    db.execute("INSERT INTO access_log(actor, role, contact_id, action) VALUES (?,?,NULL,?)",
               (member, LEVEL_NAMES[level(db, member)], f"{'view' if allowed else 'denied'}:{data}"))
    if allowed:
        return fetch()
    if mask:
        return mask()
    raise PermissionError(f"{data} needs level {DATA_LEVEL[data]} ({LEVEL_NAMES[DATA_LEVEL[data]]}); "
                          f"{member} is level {level(db, member)}")


def permissions_report(db, on_day):
    rows = []
    members = {r[0] for r in db.execute("SELECT member FROM member_levels UNION SELECT member FROM task_grants")}
    for m in sorted(members):
        tasks = sorted({g["task"] for g in db.execute("SELECT * FROM task_grants WHERE member=?", (m,))
                        if _grant_valid(db, g, on_day)})
        rows.append({"member": m, "level": f"{level(db, m)} {LEVEL_NAMES[level(db, m)]}", "tasks": tasks})
    return rows

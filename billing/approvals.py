"""Refunds wait for two people. Same rule as the Human app (hiwr_contacts.approve): the person who asks
can't approve, each approver decides once, one rejection stops it, and decisions are never changed."""
from .store import now

APPROVALS_NEEDED = 2


def request_refund(db, requested_by, payment_intent, amount_cents, reason, invoice=None):
    if not payment_intent or not payment_intent.startswith("pi_"):
        raise ValueError("A refund needs the payment (pi_...) or an invoice that was paid")
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise ValueError("Refund amount must be more than $0")
    if not reason or not reason.strip():
        raise ValueError("Say why (it goes on the record)")
    cur = db.execute("""INSERT INTO money_actions(kind, payment_intent, invoice, amount_cents, reason, requested_by, requested_at)
                        VALUES ('refund',?,?,?,?,?,?)""",
                     (payment_intent, invoice, amount_cents, reason.strip(), requested_by, now()))
    return cur.lastrowid


def status(db, action_id):
    a = db.execute("SELECT executed_ref FROM money_actions WHERE id=?", (action_id,)).fetchone()
    if a is None:
        raise LookupError(f"No money action {action_id}")
    rows = db.execute("SELECT decision FROM approvals WHERE money_action_id=?", (action_id,)).fetchall()
    if any(r["decision"] == "reject" for r in rows):
        return "REJECTED"
    if a["executed_ref"]:
        return "DONE"
    n = sum(r["decision"] == "approve" for r in rows)
    if n >= APPROVALS_NEEDED:
        return "APPROVED"
    return f"PENDING - requires double approval ({n}/{APPROVALS_NEEDED})"


def decide(db, action_id, approver, decision):
    a = db.execute("SELECT * FROM money_actions WHERE id=?", (action_id,)).fetchone()
    if a is None:
        raise LookupError(f"No money action {action_id}")
    if decision not in ("approve", "reject"):
        raise ValueError("Decision is approve or reject")
    if approver == a["requested_by"]:
        raise PermissionError("You asked for this refund, so you can't approve it")
    st = status(db, action_id)
    if not st.startswith("PENDING"):
        raise PermissionError(f"This refund is already {st}; decisions are final")
    if db.execute("SELECT 1 FROM approvals WHERE money_action_id=? AND approver=?", (action_id, approver)).fetchone():
        raise PermissionError("You already decided on this one; the other approval has to come from someone else")
    db.execute("INSERT INTO approvals VALUES (?,?,?,?)", (action_id, approver, decision, now()))
    return status(db, action_id)


def mark_executed(db, action_id, stripe_ref):
    if status(db, action_id) != "APPROVED":
        raise PermissionError("Only an approved refund can run")
    db.execute("UPDATE money_actions SET executed_ref=?, executed_at=? WHERE id=? AND executed_ref IS NULL",
               (stripe_ref, now(), action_id))


def listing(db):
    out = []
    for a in db.execute("SELECT * FROM money_actions ORDER BY id DESC"):
        appr = [dict(r) for r in db.execute("SELECT approver, decision, at FROM approvals WHERE money_action_id=?", (a["id"],))]
        out.append({**dict(a), "status": status(db, a["id"]), "approvals": appr})
    return out

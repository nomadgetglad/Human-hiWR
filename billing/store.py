"""Local billing records (SQLite). Stripe is the source of truth for money; this keeps what Human Software
needs to see at a glance, and the approval trail. Rows are appended or updated from webhooks; approvals and
ticket events are never overwritten or deleted."""
import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    stripe_id TEXT PRIMARY KEY,
    business_name TEXT NOT NULL,
    email TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    stripe_id TEXT PRIMARY KEY,
    customer TEXT NOT NULL,
    status TEXT NOT NULL,
    items TEXT NOT NULL,              -- JSON list of {lookup_key, quantity}
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
    stripe_id TEXT PRIMARY KEY,
    customer TEXT NOT NULL,
    subscription TEXT,
    status TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    tax_cents INTEGER NOT NULL DEFAULT 0,
    amount_paid_cents INTEGER NOT NULL DEFAULT 0,
    hosted_url TEXT,
    updated_at TEXT NOT NULL
);
-- Every webhook event once (Stripe can deliver the same event more than once)
CREATE TABLE IF NOT EXISTS events (
    stripe_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    received_at TEXT NOT NULL
);
-- Refunds and credits: PENDING until two people (not the requester) approve, then executed once
CREATE TABLE IF NOT EXISTS money_actions (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('refund')),
    payment_intent TEXT NOT NULL,
    invoice TEXT,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    reason TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    executed_ref TEXT,                -- the Stripe refund id once it ran
    executed_at TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
    money_action_id INTEGER NOT NULL REFERENCES money_actions(id),
    approver TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approve','reject')),
    at TEXT NOT NULL,
    PRIMARY KEY (money_action_id, approver)
);
-- DISPUTE tickets from Stripe (chargebacks, fraud warnings), with their financial block
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('DISPUTE')),
    stripe_ref TEXT NOT NULL UNIQUE,
    customer TEXT,
    invoice TEXT,
    charge TEXT,
    amount_cents INTEGER NOT NULL,
    reason TEXT,
    status TEXT NOT NULL,
    lands_in TEXT NOT NULL DEFAULT 'anticipated costs',
    opened_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket_events (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    status TEXT NOT NULL,
    note TEXT,
    at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS approvals_no_update BEFORE UPDATE ON approvals
BEGIN SELECT RAISE(ABORT, 'approvals are never changed'); END;
CREATE TRIGGER IF NOT EXISTS approvals_no_delete BEFORE DELETE ON approvals
BEGIN SELECT RAISE(ABORT, 'approvals are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS ticket_events_no_update BEFORE UPDATE ON ticket_events
BEGIN SELECT RAISE(ABORT, 'ticket history is never changed'); END;
CREATE TRIGGER IF NOT EXISTS ticket_events_no_delete BEFORE DELETE ON ticket_events
BEGIN SELECT RAISE(ABORT, 'ticket history is never deleted'); END;
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=":memory:"):
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA)
    return db


def seen_event(db, event_id, event_type):
    """True if this event was already handled; otherwise records it."""
    try:
        db.execute("INSERT INTO events VALUES (?,?,?)", (event_id, event_type, now()))
        return False
    except sqlite3.IntegrityError:
        return True


def upsert_subscription(db, sub_id, customer, status, items, cancel_at_period_end=False):
    db.execute("""INSERT INTO subscriptions VALUES (?,?,?,?,?,?)
                  ON CONFLICT(stripe_id) DO UPDATE SET status=excluded.status, items=excluded.items,
                  cancel_at_period_end=excluded.cancel_at_period_end, updated_at=excluded.updated_at""",
               (sub_id, customer, status, json.dumps(items), int(bool(cancel_at_period_end)), now()))


def upsert_invoice(db, inv_id, customer, subscription, status, total, tax, paid, hosted_url):
    db.execute("""INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(stripe_id) DO UPDATE SET status=excluded.status, total_cents=excluded.total_cents,
                  tax_cents=excluded.tax_cents, amount_paid_cents=excluded.amount_paid_cents,
                  hosted_url=COALESCE(excluded.hosted_url, invoices.hosted_url), updated_at=excluded.updated_at""",
               (inv_id, customer, subscription, status, total or 0, tax or 0, paid or 0, hosted_url, now()))


def open_ticket(db, stripe_ref, customer, invoice, charge, amount, reason, status="awaiting manager"):
    row = db.execute("SELECT id FROM tickets WHERE stripe_ref=?", (stripe_ref,)).fetchone()
    if row:
        return row["id"], False
    cur = db.execute("""INSERT INTO tickets(type, stripe_ref, customer, invoice, charge, amount_cents, reason, status, opened_at)
                        VALUES ('DISPUTE',?,?,?,?,?,?,?,?)""",
                     (stripe_ref, customer, invoice, charge, amount, reason, status, now()))
    db.execute("INSERT INTO ticket_events(ticket_id, status, note, at) VALUES (?,?,?,?)",
               (cur.lastrowid, status, f"Opened from Stripe {stripe_ref}", now()))
    return cur.lastrowid, True


def ticket_status(db, stripe_ref, status, note):
    row = db.execute("SELECT id FROM tickets WHERE stripe_ref=?", (stripe_ref,)).fetchone()
    if not row:
        return None
    db.execute("UPDATE tickets SET status=? WHERE id=?", (status, row["id"]))
    db.execute("INSERT INTO ticket_events(ticket_id, status, note, at) VALUES (?,?,?,?)", (row["id"], status, note, now()))
    return row["id"]

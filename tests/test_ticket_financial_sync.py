"""Tickets and accounting: money posts once, only after double approval, and a ticket can't close on open money."""
import json

import pytest

import hiwr_accounting as acct
from hiwr_contacts import request_money_action, approve
from hiwr_processes import open_ticket, close_ticket, ticket_priority
from conftest import TODAY

NOW = TODAY + "T10:00"


def dispute(db):
    fin = {"invoice": "POS-1188", "expected": 3000, "received": 0, "variance": -3000,
           "proposed_resolution": "refund 2 plates", "lands_in": "revenue adjustment"}
    return open_ticket(db, "DISPUTE", "Table 12 fish tacos served cold", ticket_priority(db, "DISPUTE", 30), NOW,
                       "server_kim", financial=fin)


def test_pending_money_never_posts(db):
    a = request_money_action(db, "refund", 3000, "Refund t12", "chef_marco")
    with pytest.raises(PermissionError, match="PENDING"):
        acct.post_money_action(db, a, TODAY, "accounting_sue")
    approve(db, a, "manager_jo")
    with pytest.raises(PermissionError, match="PENDING"):
        acct.post_money_action(db, a, TODAY, "accounting_sue")
    assert db.execute("SELECT COUNT(*) FROM acct_txns").fetchone()[0] == 0


def test_rejected_money_never_posts(db):
    a = request_money_action(db, "refund", 3000, "Refund t12", "chef_marco")
    approve(db, a, "manager_jo", "reject")
    with pytest.raises(PermissionError):
        acct.post_money_action(db, a, TODAY, "accounting_sue")


def test_approved_money_posts_exactly_once(db):
    a = request_money_action(db, "refund", 3000, "Refund t12", "chef_marco")
    approve(db, a, "manager_jo"); approve(db, a, "accounting_sue")
    acct.post_money_action(db, a, TODAY, "accounting_sue")
    with pytest.raises(ValueError, match="double counting"):
        acct.post_money_action(db, a, TODAY, "manager_jo")
    assert db.execute("SELECT COUNT(*) FROM acct_txns WHERE source='money_action'").fetchone()[0] == 1


def test_same_source_record_posts_once(db):
    acct.post(db, TODAY, "expense", 5500, "Sysco produce", "invoice", "INV-8812", "accounting_sue", "Sysco")
    with pytest.raises(ValueError):
        acct.post(db, TODAY, "expense", 5500, "Sysco produce", "invoice", "INV-8812", "accounting_sue", "Sysco")


def test_same_payment_from_another_source_is_flagged_not_counted(db):
    acct.post(db, TODAY, "expense", 5500, "Sysco produce", "invoice", "INV-8812", "accounting_sue", "Sysco")
    t = acct.post(db, TODAY, "expense", 5500, "ACH Sysco", "bank", "ACH-77", "accounting_sue", "Sysco")
    e = acct._latest(db, t)
    assert (e["class"], e["status"]) == ("duplicate?", "needs_person")


def test_unconfirmed_rows_dont_count(db):
    acct.post(db, TODAY, "expense", 5500, "Sysco produce", "invoice", "INV-1", "accounting_sue", "Sysco")
    assert acct.numbers(db, TODAY, TODAY)["by_class"].get("food_cost", 0) == 0


def test_posted_txns_are_append_only(db):
    import sqlite3
    t = acct.post(db, TODAY, "expense", 5500, "Sysco", "invoice", "INV-2", "accounting_sue", "Sysco")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE acct_txns SET amount_cents=1 WHERE id=?", (t,))


def test_ticket_cannot_close_with_pending_money(db):
    tid = dispute(db)
    request_money_action(db, "refund", 3000, "Refund t12", "chef_marco", ticket_id=tid)
    with pytest.raises(PermissionError, match="PENDING"):
        close_ticket(db, tid, "manager_jo", NOW)


def test_ticket_cannot_close_until_approved_money_is_posted(db):
    tid = dispute(db)
    a = request_money_action(db, "refund", 3000, "Refund t12", "chef_marco", ticket_id=tid)
    approve(db, a, "manager_jo"); approve(db, a, "accounting_sue")
    with pytest.raises(PermissionError, match="not posted"):
        close_ticket(db, tid, "manager_jo", NOW)
    acct.post_money_action(db, a, TODAY, "accounting_sue")
    close_ticket(db, tid, "manager_jo", NOW, note="refund on card statement")
    assert db.execute("SELECT status FROM tickets WHERE id=?", (tid,)).fetchone()[0] == "closed"
    assert [r[0] for r in db.execute("SELECT event FROM ticket_events WHERE ticket_id=? ORDER BY id", (tid,))] == ["opened", "closed"]


def test_ticket_with_rejected_money_can_close(db):
    tid = dispute(db)
    a = request_money_action(db, "refund", 3000, "Refund t12", "chef_marco", ticket_id=tid)
    approve(db, a, "manager_jo", "reject")
    close_ticket(db, tid, "manager_jo", NOW)


def test_closed_ticket_cannot_close_again(db):
    tid = dispute(db)
    close_ticket(db, tid, "manager_jo", NOW)
    with pytest.raises(ValueError):
        close_ticket(db, tid, "manager_jo", NOW)


def test_ticket_events_append_only(db):
    import sqlite3
    tid = dispute(db)
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM ticket_events WHERE ticket_id=?", (tid,))


def test_financial_block_is_stored_with_ticket(db):
    tid = dispute(db)
    fin = json.loads(db.execute("SELECT financial FROM tickets WHERE id=?", (tid,)).fetchone()[0])
    assert fin["variance"] == -3000 and fin["lands_in"] == "revenue adjustment"


def test_dispute_outranks_shipment(db):
    assert ticket_priority(db, "DISPUTE") < ticket_priority(db, "SHIPMENT")
    assert ticket_priority(db, "SHIPMENT", dollars=600, perishable=True) < ticket_priority(db, "SHIPMENT")

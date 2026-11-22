"""Double approval on every money action: two different certified Finance-level people, never the requester."""
import sqlite3

import pytest

from hiwr_contacts import (request_money_action, approve, money_action_status, certify, add_plan_owner,
                           is_certified)
from hiwr_access import set_level, grant_task, level
from conftest import OWNER, TODAY


def refund(db, cents=3000, by="chef_marco"):
    return request_money_action(db, "refund", cents, "Refund 2 plates fish tacos, table 12", by)


def test_new_action_is_pending(db):
    assert money_action_status(db, refund(db)) == "PENDING - requires double approval (0/2)"


def test_two_different_approvers_approve(db):
    a = refund(db)
    assert approve(db, a, "manager_jo").startswith("PENDING")
    assert approve(db, a, "accounting_sue") == "APPROVED"


def test_one_approval_is_not_enough(db):
    a = refund(db)
    approve(db, a, "manager_jo")
    assert money_action_status(db, a).startswith("PENDING")


def test_same_person_cannot_count_twice(db):
    a = refund(db)
    approve(db, a, "manager_jo")
    with pytest.raises(PermissionError, match="someone else"):
        approve(db, a, "manager_jo")
    assert money_action_status(db, a) == "PENDING - requires double approval (1/2)"


def test_db_also_refuses_duplicate_approver_rows(db):
    a = refund(db)
    approve(db, a, "manager_jo")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO approvals(money_action_id, approver, decision) VALUES (?,?,?)", (a, "manager_jo", "approve"))


def test_requester_cannot_approve_own_action(db):
    a = refund(db, by="manager_jo")
    with pytest.raises(PermissionError, match="requested"):
        approve(db, a, "manager_jo")


def test_uncertified_cannot_approve(db):
    a = refund(db)
    for who in ("chef_marco", "cook_luis", "nobody"):
        with pytest.raises(PermissionError, match="not certified"):
            approve(db, a, who)


def test_certified_for_one_scope_only(db):
    set_level(db, "fee_only", 3, OWNER)
    certify(db, "fee_only", "fee", OWNER)
    with pytest.raises(PermissionError):
        approve(db, refund(db), "fee_only")


def test_owner_is_not_an_approver_unless_certified(db):
    a = refund(db)
    with pytest.raises(PermissionError):
        approve(db, a, OWNER)


def test_one_reject_blocks(db):
    a = refund(db)
    approve(db, a, "manager_jo")
    assert approve(db, a, "accounting_sue", "reject") == "REJECTED"


def test_decisions_are_final_after_approved(db):
    """Bug found: a third approver could reject after APPROVED (and after the money posted)."""
    a = refund(db)
    approve(db, a, "manager_jo")
    approve(db, a, "accounting_sue")
    with pytest.raises(PermissionError, match="final"):
        approve(db, a, "finance_kim", "reject")
    assert money_action_status(db, a) == "APPROVED"


def test_decisions_are_final_after_rejected(db):
    a = refund(db)
    approve(db, a, "manager_jo", "reject")
    with pytest.raises(PermissionError, match="final"):
        approve(db, a, "accounting_sue")


def test_unknown_decision_word(db):
    with pytest.raises(ValueError):
        approve(db, refund(db), "manager_jo", "yes")


def test_missing_action(db):
    """Bug found: approving an id that doesn't exist crashed with TypeError."""
    with pytest.raises(LookupError):
        approve(db, 999, "manager_jo")


def test_amount_cannot_change_after_approval(db):
    """Bug found: money_actions had no append-only trigger, so a $30 approval could become $5,000."""
    a = refund(db)
    approve(db, a, "manager_jo"); approve(db, a, "accounting_sue")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("UPDATE money_actions SET amount_cents=500000 WHERE id=?", (a,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("DELETE FROM money_actions WHERE id=?", (a,))


def test_approvals_cannot_be_edited_or_deleted(db):
    a = refund(db)
    approve(db, a, "manager_jo")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE approvals SET approver='accounting_sue'")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM approvals")


@pytest.mark.parametrize("bad", [-3000, 12.5, "3000", True])
def test_amount_must_be_whole_cents_not_negative(db, bad):
    """Bug found: a negative refund could be requested and approved."""
    with pytest.raises(ValueError):
        request_money_action(db, "refund", bad, "x", "chef_marco")


def test_zero_is_allowed_for_waivers(db):
    a = request_money_action(db, "fee", 0, "Late fee waived, rebooked", "chef_marco")
    assert money_action_status(db, a).startswith("PENDING")


def test_cannot_certify_below_finance_level(db):
    """Bug found: certify() let the owner make a level-0 cook a refund approver."""
    with pytest.raises(PermissionError, match="Finance level"):
        certify(db, "cook_luis", "refund", OWNER)


def test_demotion_switches_approval_off(db):
    """Bug found: dropping an approver to Staff level left their approval power on."""
    set_level(db, "manager_jo", 0, OWNER)
    assert not is_certified(db, "manager_jo", "refund")
    with pytest.raises(PermissionError):
        approve(db, refund(db), "manager_jo")


def test_demotion_also_covers_task_grant_approvers(db):
    set_level(db, "pat", 3, OWNER)
    grant_task(db, "pat", "refund_approval", OWNER, TODAY)
    assert is_certified(db, "pat", "refund")
    set_level(db, "pat", 1, OWNER)
    assert not is_certified(db, "pat", "refund")


def test_revoked_certification(db):
    certify(db, "manager_jo", "refund", OWNER, status="revoked")
    with pytest.raises(PermissionError):
        approve(db, refund(db), "manager_jo")


def test_non_owner_cannot_certify(db):
    with pytest.raises(PermissionError, match="plan owner"):
        certify(db, "finance_kim", "refund", "manager_jo")


def test_cannot_make_yourself_plan_owner(db):
    """Bug found: add_plan_owner never checked who was adding, so anyone could become level 4."""
    with pytest.raises(PermissionError):
        add_plan_owner(db, "cook_luis", "cook_luis")
    assert level(db, "cook_luis") == 0


def test_owner_can_add_co_owner(db):
    add_plan_owner(db, "owner_dad", OWNER)
    assert level(db, "owner_dad") == 4


def test_known_gap_requester_name_is_trusted(db):
    """Not fixed in the plain tables (identity is a string the caller passes). jo files a refund in sue's
    name and approves it herself. hiwr_signing.py closes this: the requester must sign the request."""
    a = request_money_action(db, "refund", 9999, "x", "accounting_sue")
    assert approve(db, a, "manager_jo").startswith("PENDING")

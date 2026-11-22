"""Access levels: set only by the account holder, and task grants never unlock sensitive data."""
import pytest

from hiwr_access import (set_level, level, grant_task, can_do, revoke, can_see, read, DATA_LEVEL,
                         OWNER_ONLY_TASKS)
from conftest import OWNER, TODAY

SENSITIVE = ("client_records", "order_costs", "vendor_prices", "purchase_drafts", "payroll", "financial_block",
             "money_actions", "permissions")


def test_only_owner_sets_levels(db):
    for who in ("manager_jo", "chef_marco", "cook_luis"):
        with pytest.raises(PermissionError):
            set_level(db, "cook_luis", 3, who)
    assert level(db, "cook_luis") == 0


def test_nobody_set_to_account_holder(db):
    with pytest.raises(PermissionError):
        set_level(db, "manager_jo", 4, OWNER)


def test_cannot_raise_own_level(db):
    with pytest.raises(PermissionError):
        set_level(db, "manager_jo", 3, "manager_jo")


def test_unknown_member_is_staff(db):
    assert level(db, "walk_in") == 0


@pytest.mark.parametrize("task", ["inventory_audit", "roll_call", "receiving_check", "pharmacist_verify",
                                  "hr_manager", "foreman"])
def test_task_grant_never_unlocks_sensitive_data(db, task):
    grant_task(db, "cook_luis", task, OWNER, TODAY)
    assert can_do(db, "cook_luis", task, TODAY)
    for data in SENSITIVE:
        assert not can_see(db, "cook_luis", data), data
        with pytest.raises(PermissionError):
            read(db, "cook_luis", data, lambda: "SECRET")


def test_mask_is_returned_instead_of_data(db):
    assert read(db, "cook_luis", "client_records", lambda: "full phone", mask=lambda: "***-0101") == "***-0101"


def test_every_level_sees_only_up_to_itself(db):
    for who, lvl in (("cook_luis", 0), ("lead_rosa", 1), ("chef_marco", 2), ("manager_jo", 3), (OWNER, 4)):
        for data, need in DATA_LEVEL.items():
            assert can_see(db, who, data) == (lvl >= need), (who, data)


def test_denied_reads_are_logged(db):
    with pytest.raises(PermissionError):
        read(db, "cook_luis", "payroll", lambda: 1)
    row = db.execute("SELECT action FROM access_log WHERE actor='cook_luis' ORDER BY id DESC").fetchone()
    assert row["action"] == "denied:payroll"


@pytest.mark.parametrize("task", sorted(OWNER_ONLY_TASKS))
def test_money_approval_tasks_owner_only_and_finance_only(db, task):
    with pytest.raises(PermissionError):
        grant_task(db, "chef_marco", task, OWNER, TODAY)            # level 2: too low
    grant_task(db, "manager_jo", task, OWNER, TODAY)
    with pytest.raises(PermissionError):
        grant_task(db, "finance_kim", task, "manager_jo", TODAY)    # not the owner


def test_delegation_is_one_hop(db):
    grant_task(db, "chef_marco", "roll_call", OWNER, TODAY, can_delegate=True)
    grant_task(db, "lead_rosa", "roll_call", "chef_marco", TODAY)
    with pytest.raises(PermissionError):
        grant_task(db, "lead_rosa", "roll_call", "chef_marco", TODAY, can_delegate=True)
    with pytest.raises(PermissionError):
        grant_task(db, "cook_luis", "roll_call", "lead_rosa", TODAY)


def test_cannot_pass_on_a_task_you_dont_hold(db):
    grant_task(db, "chef_marco", "roll_call", OWNER, TODAY, can_delegate=True)
    with pytest.raises(PermissionError):
        grant_task(db, "lead_rosa", "inventory_audit", "chef_marco", TODAY)


def test_delegated_grant_capped_to_certifier_expiry(db):
    grant_task(db, "chef_marco", "inventory_audit", OWNER, TODAY, can_delegate=True, expires_on="2027-01-31")
    g = grant_task(db, "lead_rosa", "inventory_audit", "chef_marco", TODAY, expires_on="2030-01-01")
    assert db.execute("SELECT expires_on FROM task_grants WHERE id=?", (g,)).fetchone()[0] == "2027-01-31"
    assert not can_do(db, "lead_rosa", "inventory_audit", "2027-02-01")


def test_revocation_cascades(db):
    g = grant_task(db, "chef_marco", "roll_call", OWNER, TODAY, can_delegate=True)
    grant_task(db, "lead_rosa", "roll_call", "chef_marco", TODAY)
    assert can_do(db, "lead_rosa", "roll_call", TODAY)
    revoke(db, g, OWNER, "left the company")
    assert not can_do(db, "lead_rosa", "roll_call", TODAY)


def test_only_owner_or_grantor_revokes(db):
    g = grant_task(db, "lead_rosa", "roll_call", OWNER, TODAY)
    with pytest.raises(PermissionError):
        revoke(db, g, "manager_jo")


def test_grant_history_is_append_only(db):
    import sqlite3
    g = grant_task(db, "lead_rosa", "roll_call", OWNER, TODAY)
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE task_grants SET can_delegate=1 WHERE id=?", (g,))
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE member_levels SET level=3 WHERE member='cook_luis'")
    with pytest.raises(sqlite3.DatabaseError):
        revoke(db, g, OWNER)
        db.execute("DELETE FROM task_revocations")

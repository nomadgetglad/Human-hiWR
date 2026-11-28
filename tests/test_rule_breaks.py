"""Trying to break the AI rules: the exceptions and edge cases where a rule could be slipped past.

The rules under attack (see CLAUDE.md and the project instructions, agent v7):
  1. AI proposes, a person confirms. Nobody confirms their own request.
  2. Every money movement needs two different human approvers, certified by the account holder.
  3. Access levels are set only by the account holder; tasks never unlock sensitive data.
  4. A ticket closes only when its money is reconciled; one financial truth (show both, don't pick).
  5. History is append-only: nothing is edited or deleted.

How to read the results:
  - A normal (passing) test is an attack the code already stops.
  - A test marked xfail ("expected to fail") is an attack that WORKS today: a real bug. Each one says
    what breaks and why it matters. When the bug is fixed the test starts passing, and because the
    marks are strict=True pytest then reports XPASS as a failure, so you remember to remove the mark.
Nothing in the app code was changed to make these pass.
"""
import importlib
import importlib.util
import shutil
import sqlite3
import sys

import pytest

from conftest import OWNER, ROOT, TODAY
from hiwr_access import can_do, can_see, grant_task, level, read, set_level
from hiwr_accounting import import_qb, post_money_action, reconcile_qb
from hiwr_contacts import approve, certify, connect, is_certified, money_action_status, request_money_action
from hiwr_cs import apply_change
from hiwr_processes import close_ticket, open_ticket
from hiwr_staff import decide_hr, hr_request

NOW = f"{TODAY}T18:00"


def bug(reason):
    """Mark a test that shows a real bug. strict: once fixed, the test must lose this mark."""
    return pytest.mark.xfail(strict=True, reason="BUG: " + reason)


def approved_refund(db, cents=2500, requested_by="chef_marco", ticket_id=None):
    a = request_money_action(db, "refund", cents, "Refund table 12", requested_by, ticket_id)
    approve(db, a, "manager_jo")
    approve(db, a, "accounting_sue")
    assert money_action_status(db, a) == "APPROVED"
    return a


# =====================================================================================
# 1. Two different human approvers for money
# =====================================================================================

@pytest.mark.parametrize("scope", ["Refund", "refund ", "refunds", "REFUND", "credit", "chargeback", ""])
def test_unknown_or_misspelled_scope_refused_at_the_door(db, scope):
    """Fail closed: the database only takes refund, payroll, fee and subcontract, so a made-up scope
    (one nobody's approval rules were written for) can't even be filed."""
    with pytest.raises(sqlite3.IntegrityError):
        request_money_action(db, scope, 1000, "x", "chef_marco")


@pytest.mark.parametrize("amount", [10.5, "1000", None, True, -1, float("nan")])
def test_amount_tricks_refused(db, amount):
    with pytest.raises(ValueError):
        request_money_action(db, "refund", amount, "x", "chef_marco")


def test_two_reject_then_approve_never_flips(db):
    a = request_money_action(db, "payroll", 8000, "call-in pay", "chef_marco")
    approve(db, a, "manager_jo", "reject")
    for m in ("accounting_sue", "finance_kim"):
        with pytest.raises(PermissionError):
            approve(db, a, m)
    assert money_action_status(db, a) == "REJECTED"


def test_owner_cannot_be_the_hidden_second_approver_without_certification(db):
    a = request_money_action(db, "refund", 1000, "x", "chef_marco")
    approve(db, a, "manager_jo")
    with pytest.raises(PermissionError):
        approve(db, a, OWNER)
    assert money_action_status(db, a).startswith("PENDING")


@pytest.mark.parametrize("alias", ["Manager_Jo", "manager_jo ", " manager_jo", "MANAGER_JO"])
@bug("request_money_action() takes any string as the requester. Jo files a refund as 'Manager_Jo' (or with a "
     "space), then approves it herself as 'manager_jo': the 'requester can't approve' check compares exact "
     "strings, so one person counts as requester and approver. Fix: refuse a requester who is not on the "
     "roster, and compare normalized ids.")
def test_requester_spelling_variant_cannot_self_approve(db, alias):
    try:
        a = request_money_action(db, "refund", 4000, "Refund my friend", alias)
    except (ValueError, PermissionError, LookupError):
        return                                   # refused at the door: rule holds
    with pytest.raises(PermissionError):
        approve(db, a, "manager_jo")


@bug("post_money_action() lets anyone post an approved action to the books, even a level 0 cook. The money was "
     "approved by two people, but the posting date and department are chosen by whoever calls it. Fix: "
     "posting needs Finance level (3), like every other accounting write.")
def test_staff_cannot_post_approved_money_to_the_books(db):
    a = approved_refund(db)
    with pytest.raises(PermissionError):
        post_money_action(db, a, TODAY, "cook_luis")


# =====================================================================================
# 2. Levels set only by the account holder; tasks never unlock sensitive data
# =====================================================================================

@pytest.mark.parametrize("task", ["payroll", "financial_block", "money_actions", "permissions", "client_records",
                                  "refund", "view_payroll", "w2", "accounting"])
def test_task_named_like_sensitive_data_unlocks_nothing(db, task):
    grant_task(db, "cook_luis", task, OWNER, TODAY)
    assert can_do(db, "cook_luis", task, TODAY)
    for data in ("payroll", "financial_block", "money_actions", "permissions", "client_records"):
        assert not can_see(db, "cook_luis", data)
        with pytest.raises(PermissionError):
            read(db, "cook_luis", data, lambda: "SECRET")


def test_co_owner_made_by_a_manager_is_refused(db):
    from hiwr_contacts import add_plan_owner
    with pytest.raises(PermissionError):
        add_plan_owner(db, "manager_jo", "manager_jo")
    assert level(db, "manager_jo") == 3


def test_finance_member_cannot_certify_a_money_approver(db):
    set_level(db, "lead_rosa", 3, OWNER)
    with pytest.raises(PermissionError):
        certify(db, "lead_rosa", "refund", "manager_jo")
    with pytest.raises(PermissionError):
        grant_task(db, "lead_rosa", "refund_approval", "manager_jo", TODAY)
    assert not is_certified(db, "lead_rosa", "refund")


def test_unknown_data_name_fails_closed(db):
    with pytest.raises((KeyError, PermissionError)):
        read(db, "cook_luis", "w2_forms", lambda: "SECRET")


def test_negative_level_refused_by_the_database(db):
    with pytest.raises(sqlite3.IntegrityError):
        set_level(db, "cook_luis", -1, OWNER)


@bug("set_level() only refuses 4 and up, and the database only checks 0 to 4, so a level of 2.5 is saved. "
     "That person is then more than a Manager and less than Finance, which no rule was written for. "
     "Low risk (only the owner sets levels), but a bad import would do it silently. Fix: whole numbers 0 to 3.")
def test_level_must_be_a_whole_number(db):
    with pytest.raises((ValueError, PermissionError, sqlite3.IntegrityError)):
        set_level(db, "cook_luis", 2.5, OWNER)


# =====================================================================================
# 3. AI proposes, a person confirms; nobody confirms their own request
# =====================================================================================

@bug("decide_hr() has no check on who decides. A manager approves their own time off, and through the UI any "
     "Manager can approve their own request, because the card only checks the level. Fix: the person who asked "
     "can't decide their own request, and deciding needs Manager level in the data layer too.")
def test_nobody_approves_their_own_time_off(db):
    r = hr_request(db, "chef_marco", "time_off", NOW, "2026-11-28", "2026-11-29")
    with pytest.raises(PermissionError):
        decide_hr(db, r, "approved", "chef_marco", NOW)


@bug("decide_hr() can be called again on a request that was already decided. Approve, then deny: the approval "
     "already deleted the person's availability, the denial doesn't put it back, and the request now has two "
     "answers. Fix: one decision per request (a change goes through a new request).")
def test_hr_decision_is_final(db):
    db.execute("INSERT INTO staff(member, role, hourly_cents) VALUES ('cook_luis', 'cook', 1600)")
    db.execute("INSERT INTO staff_availability(member, day, start_hour, end_hour) VALUES ('cook_luis', '2026-11-28', 16, 23)")
    r = hr_request(db, "cook_luis", "time_off", NOW, "2026-11-28", "2026-11-28")
    decide_hr(db, r, "approved", "chef_marco", NOW)
    with pytest.raises((PermissionError, ValueError)):
        decide_hr(db, r, "denied", "manager_jo", NOW)


@pytest.mark.parametrize("who", ["maker_dee", "chef_marco"])
@bug("hiwr_cs.apply_change() decides who may OK a change to a locked order by three hard-coded names "
     "('owner_alex', 'chef_marco', 'maker_dee'), not by level or role. 'maker_dee' isn't on this roster at all, "
     "and in any real account those names mean nothing (or belong to the wrong person). Fix: check the "
     "confirmer is an active member at Manager level or holds the kitchen task.")
def test_locked_order_change_needs_a_real_kitchen_confirmer(db, who):
    if who == "chef_marco":
        set_level(db, "chef_marco", 0, OWNER)      # same name, but here a level 0 cook
    draft = {"locked": True, "stage": "cooking"}
    with pytest.raises(PermissionError):
        apply_change(db, 1, draft, "server_kim", who, NOW)


# =====================================================================================
# 4. Tickets and one financial truth (agent v7)
# =====================================================================================

SHORT = {"invoice": "INV-2231", "expected_qty": 10, "received_qty": 6, "variance_cents": 4800,
         "proposed_resolution": "vendor credit", "lands_in": "anticipated_costs"}


@bug("close_ticket() only looks at money actions. A SHIPMENT ticket with a $48 short-ship variance and no "
     "resolution at all closes straight away, by anyone. v7 says a ticket closes only once its financial "
     "effects are reconciled. Fix: a ticket whose financial block has a variance needs an approved money action "
     "(or a recorded 'no money moves' decision by Finance) before it closes.")
def test_ticket_with_open_variance_cannot_close(db):
    t = open_ticket(db, "SHIPMENT", "Sysco short 4 cases", 20, NOW, "lead_rosa", financial=SHORT)
    with pytest.raises(PermissionError):
        close_ticket(db, t, "cook_luis", NOW)


def test_ticket_with_rejected_and_pending_money_stays_open(db):
    t = open_ticket(db, "DISPUTE", "Cold tacos", 10, NOW, "chef_marco")
    r = request_money_action(db, "refund", 3000, "x", "chef_marco", t)
    approve(db, r, "manager_jo", "reject")
    request_money_action(db, "refund", 1500, "half refund instead", "chef_marco", t)
    with pytest.raises(PermissionError, match="PENDING"):
        close_ticket(db, t, OWNER, NOW)


def test_approved_but_unposted_money_keeps_ticket_open(db):
    t = open_ticket(db, "DISPUTE", "Wrong order", 10, NOW, "chef_marco")
    approved_refund(db, ticket_id=t)
    with pytest.raises(PermissionError, match="not posted"):
        close_ticket(db, t, OWNER, NOW)


@bug("reconcile_qb() writes 'QuickBooks wins' and only adds a flag. v7 says a mismatch found in accounting "
     "opens or updates the matching ticket, set to awaiting manager, showing both values without picking one. "
     "No ticket is opened. Fix: open (or update) a ticket for each mismatch, status 'awaiting manager'.")
def test_quickbooks_mismatch_opens_a_ticket(db):
    import_qb(db, "manager_jo", "2026-11", [("Food purchases", "food_cost", 900000)])
    out = reconcile_qb(db, "manager_jo", "2026-11", "2026-11-01", "2026-11-30")
    assert "QuickBooks" in out[0] and "HiWR" in out[0]            # both values are shown: that part holds
    assert db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1


# =====================================================================================
# 5. Append-only history
# =====================================================================================

def _guards(db):
    """Which tables refuse UPDATE and DELETE (a BEFORE trigger that raises)."""
    up, de = set(), set()
    for name, tbl, sql in db.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger'"):
        s = " ".join(sql.upper().split())
        if "RAISE(ABORT" in s:
            if "BEFORE UPDATE ON" in s:
                up.add(tbl)
            if "BEFORE DELETE ON" in s:
                de.add(tbl)
    return up, de


# Tables the schema calls append-only (in a comment or a module docstring), and the history they hold.
APPEND_ONLY = ["contact_events", "order_stage_events", "appointment_events", "access_log", "approver_certifications",
               "money_actions", "approvals", "result_checkins", "assignment_events", "ticket_events",
               "process_readings", "table_assignments", "waitlist_events", "shift_events", "conduct_events",
               "member_levels", "task_grants", "task_revocations", "acct_txns", "acct_class_events",
               "stock_moves", "material_moves", "cs_messages", "order_line_changes", "lab_phase_events",
               "lab_inspections", "lab_draws", "unit_checks", "auth_events", "auth_factors", "auth_sessions"]
HALF_GUARDED = {
    "plan_owners": "an owner row can be DELETEd, so an owner can quietly disappear from the record (update is blocked, delete isn't)",
    "sms_consent": "consent rows can be DELETEd, so a STOP reply can be erased and texting starts again (TCPA risk)",
    "prep_batches": "fried batches can be DELETEd, so waste and depletion history can vanish",
    "corrective_actions": "HACCP corrective actions can be UPDATEd (delete is blocked, edit isn't), so an inspector's log can be rewritten",
    "backstock_choices": "comment says append-only, but there are no triggers at all",
    "hr_request_events": "HR decisions have no triggers: an approval can be edited into a denial or deleted",
}


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_history_tables_refuse_edit_and_delete(table):
    db = connect()
    up, de = _guards(db)
    assert table in up and table in de


@pytest.mark.parametrize("table", [pytest.param(t, marks=bug(why)) for t, why in HALF_GUARDED.items()])
def test_history_tables_with_missing_guards(table):
    db = connect()
    up, de = _guards(db)
    assert table in up and table in de


# =====================================================================================
# 6. The UI server
# =====================================================================================

@pytest.fixture
def built_app(tmp_path, monkeypatch):
    """A copy of the UI server with a built web app (ui/web/dist) next to it, like Alexandre runs it, and the
    account key file (human.key) where a real install keeps it. The repo itself is not touched."""
    ui = tmp_path / "ui"
    shutil.copytree(ROOT / "ui" / "server", ui / "server", ignore=shutil.ignore_patterns("__pycache__"))
    (ui / "web" / "dist" / "assets").mkdir(parents=True)
    (ui / "web" / "dist" / "index.html").write_text("<html>app</html>")
    (ui / "human.key").write_text("THE-ACCOUNT-KEY")
    monkeypatch.setenv("HUMAN_DB", ":memory:")
    name = "builtserver"
    spec = importlib.util.spec_from_file_location(name, ui / "server" / "__init__.py",
                                                  submodule_search_locations=[str(ui / "server")])
    pkg = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, pkg)
    app = importlib.import_module(f"{name}.app")
    monkeypatch.setitem(sys.modules, f"{name}.app", app)
    from fastapi.testclient import TestClient
    yield TestClient(app.app, raise_server_exceptions=False)
    for k in [k for k in sys.modules if k.startswith(name)]:
        sys.modules.pop(k, None)


def test_built_app_serves_the_page(built_app):
    assert built_app.get("/").text == "<html>app</html>"
    assert built_app.get("/staff").text == "<html>app</html>"       # app routes fall back to the page


@pytest.mark.parametrize("path", ["/%2e%2e/%2e%2e/human.key", "/..%2f..%2fhuman.key", "/%2e%2e%2f%2e%2e%2fhuman.key"])
# Was CRITICAL, fixed in this PR: spa() joined the address onto ui/web/dist without checking it stayed inside,
# so '/%2e%2e/%2e%2e/human.key' downloaded the account key with no sign-in (checked with curl on a real server).
def test_web_app_route_cannot_read_files_outside_dist(built_app, path):
    r = built_app.get(path)
    assert "THE-ACCOUNT-KEY" not in r.text
    assert r.text == "<html>app</html>"


@bug("Same hole as test_nobody_approves_their_own_time_off, through the app: Marco (Manager) opens an 'Approve time "
     "off for chef_marco' card on his own request and confirms it. The card checks his level, not whose request it is.")
def test_manager_cannot_approve_own_time_off_in_the_app():
    from fastapi.testclient import TestClient
    from server import app as appmod
    appmod.reset()
    r = hr_request(appmod.db(), "chef_marco", "time_off", NOW, "2026-11-28", "2026-11-29")
    appmod.db().commit()
    c = TestClient(appmod.app, raise_server_exceptions=False)
    H = {"X-Member": "chef_marco"}
    p = c.post("/api/proposals", json={"kind": "hr_decide", "params": {"request_id": r, "status": "approved"}}, headers=H)
    if p.status_code == 200:
        p = c.post(f"/api/proposals/{p.json()['id']}/confirm", headers=H)
    assert p.status_code == 403

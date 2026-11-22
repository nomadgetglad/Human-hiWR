"""UI server: the top bar by role, and attacks through the API. The server filters, so these hit the API."""
import json

import pytest
from fastapi.testclient import TestClient

from server import app as appmod
from server import seed
from hiwr_access import set_level


@pytest.fixture
def c():
    appmod.reset()
    return TestClient(appmod.app, raise_server_exceptions=False)


def H(m):
    return {"X-Member": m}


def bar(c, m):
    r = c.get("/api/topbar", headers=H(m))
    assert r.status_code == 200
    return r.json()


def labels(b):
    return [s["label"] for s in b["stats"]]


def kinds(b):
    return {n["kind"] for n in b["notifications"]}


# ---------- top bar by role ----------

def test_owner_sees_revenue(c):
    b = bar(c, "owner_alex")
    assert b["role_view"] == "owner"
    assert labels(b) == ["Revenue today", "Forecast tonight"]
    assert "money" in kinds(b)


def test_finance_manager_sees_labor_dollars(c):
    b = bar(c, "manager_jo")
    assert b["role_view"] == "manager"
    assert labels(b) == ["Kitchen labor tonight", "Labor cost tonight", "On shift"]
    assert "Revenue today" not in labels(b)


def test_level2_manager_sees_percent_not_dollars(c):
    b = bar(c, "chef_marco")
    assert labels(b) == ["Kitchen labor tonight", "On shift"]
    assert "money" not in kinds(b)
    assert not any(s["format"] == "usd" for s in b["stats"])


@pytest.mark.parametrize("m", ["fry_rosa", "stocker_tim"])
def test_employees_see_their_tasks_only(c, m):
    b = bar(c, m)
    assert b["role_view"] == "employee"
    assert labels(b) == ["My tasks"]
    assert kinds(b) <= {"task", "prep"}
    assert not any(s["format"] in ("usd", "pct") for s in b["stats"])


def test_employee_task_count_matches_their_list(c):
    b = bar(c, "fry_rosa")
    assert b["stats"][0]["value"] == 3
    titles = {n["text"] for n in b["notifications"] if n["kind"] == "task"}
    assert "Pull list for aisle A and D before 7 AM" not in titles     # Tim's task, not Rosa's


def test_lead_level_sees_tasks_staff_and_tickets_but_no_money(c, monkeypatch):
    monkeypatch.setattr(seed, "MEMBERS", seed.MEMBERS + [{"id": "lead_ana", "name": "Ana", "title": "Lead", "level": 1, "language": "en"}])
    set_level(appmod.db(), "lead_ana", 1, seed.OWNER)
    b = bar(c, "lead_ana")
    assert b["role_view"] == "employee" and labels(b) == ["My tasks"]
    assert "money" not in kinds(b) and "hr" not in kinds(b)


def test_task_done_lowers_the_count(c):
    t = c.get("/api/tasks", headers=H("stocker_tim")).json()[0]
    p = c.post("/api/proposals", headers=H("stocker_tim"), json={"kind": "task_done", "params": {"assignment_id": t["id"]}}).json()
    assert bar(c, "stocker_tim")["stats"][0]["value"] == 2        # nothing changes on the first tap
    c.post(f"/api/proposals/{p['id']}/confirm", headers=H("stocker_tim"))
    assert bar(c, "stocker_tim")["stats"][0]["value"] == 1


def test_no_member_is_401(c):
    assert c.get("/api/topbar").status_code == 401
    assert c.get("/api/topbar", headers=H("someone_else")).status_code == 401


def test_menu_hides_accounting_below_finance(c):
    for m, has in (("owner_alex", True), ("manager_jo", True), ("chef_marco", False), ("fry_rosa", False)):
        ids = [x["id"] for x in c.get("/api/me", headers=H(m)).json()["menu"]]
        assert ("accounting" in ids) == has, m


# ---------- sensitive data never reaches a lower level ----------

def test_accounting_blocked_below_finance(c):
    for m in ("chef_marco", "fry_rosa", "stocker_tim"):
        assert c.get("/api/accounting/money-actions", headers=H(m)).status_code == 403


def test_costs_hidden_from_staff(c):
    s = c.get("/api/inventory/stew", headers=H("fry_rosa")).json()
    assert s["shows_costs"] is False and "cost_cards" not in s
    assert "cost_cards" in c.get("/api/inventory/stew", headers=H("chef_marco")).json()


def test_ticket_financial_block_never_sent_below_finance(c):
    from hiwr_processes import open_ticket
    open_ticket(appmod.db(), "DISPUTE", "Table 12 cold tacos", 5, seed.TODAY + "T12:00", "server_kim",
                financial={"variance": -3000, "invoice": "SECRET-INV-1"})
    for m in ("fry_rosa", "stocker_tim", "chef_marco"):
        for path in ("/api/topbar", "/api/inventory/stew", "/api/me"):
            body = c.get(path, headers=H(m)).text
            assert "SECRET-INV-1" not in body and "variance" not in body, (m, path)


def test_staff_level_sees_only_own_shift(c):
    s = c.get("/api/staff", headers=H("stocker_tim")).json()
    assert s["view"] == "own" and set(s) == {"view", "day", "my_shift", "my_requests"}


def test_fill_candidates_have_no_phone_numbers(c):
    r = c.get("/api/staff/fill", params={"role": "dishwasher"}, headers=H("chef_marco")).json()
    for cand in r["candidates"]:
        assert set(cand) == {"candidate_id", "name", "months", "reliability", "language"}
    assert "+1" not in json.dumps(r)


def test_staff_cannot_find_cover(c):
    assert c.get("/api/staff/fill", params={"role": "dishwasher"}, headers=H("fry_rosa")).status_code == 403


# ---------- money through the UI ----------

def card(c, m, kind, **params):
    return c.post("/api/proposals", headers=H(m), json={"kind": kind, "params": params})


def decide(c, m, action_id, decision="approve"):
    r = card(c, m, "money_decide", money_action_id=action_id, decision=decision)
    if r.status_code != 200:
        return r
    return c.post(f"/api/proposals/{r.json()['id']}/confirm", headers=H(m))


def test_double_approval_through_ui(c):
    assert "PENDING" in decide(c, "manager_jo", 1).json()["result"]
    assert decide(c, "accounting_sue", 1).json()["result"] == "APPROVED"


def test_money_card_is_marked_pending(c):
    r = card(c, "manager_jo", "money_decide", money_action_id=1, decision="approve").json()
    assert r["money"] is True and "one more approver" in r["lines"][-1]


def test_requester_cannot_approve_in_ui(c):
    assert card(c, "chef_marco", "money_decide", money_action_id=1, decision="approve").status_code == 403


def test_same_approver_twice_via_two_open_cards(c):
    """Bug found: two open cards from one approver; confirming the second crashed with a 500."""
    a = card(c, "manager_jo", "money_decide", money_action_id=1, decision="approve").json()
    b = card(c, "manager_jo", "money_decide", money_action_id=1, decision="approve").json()
    assert c.post(f"/api/proposals/{a['id']}/confirm", headers=H("manager_jo")).status_code == 200
    r = c.post(f"/api/proposals/{b['id']}/confirm", headers=H("manager_jo"))
    assert r.status_code == 409
    assert c.get("/api/accounting/money-actions", headers=H("manager_jo")).json()[0]["status"].endswith("(1/2)")


def test_no_decision_after_approved(c, monkeypatch):
    """Bug found: a third certified approver could reject after APPROVED."""
    from hiwr_contacts import certify
    monkeypatch.setattr(seed, "MEMBERS", seed.MEMBERS + [{"id": "finance_kim", "name": "Kim", "title": "Finance", "level": 3, "language": "en"}])
    set_level(appmod.db(), "finance_kim", 3, seed.OWNER)
    certify(appmod.db(), "finance_kim", "refund", seed.OWNER)
    decide(c, "manager_jo", 1); decide(c, "accounting_sue", 1)
    assert card(c, "finance_kim", "money_decide", money_action_id=1, decision="reject").status_code == 409


def test_cannot_confirm_someone_elses_card(c):
    p = card(c, "manager_jo", "money_decide", money_action_id=1, decision="approve").json()
    assert c.post(f"/api/proposals/{p['id']}/confirm", headers=H("accounting_sue")).status_code == 404


def test_confirmed_card_cannot_be_replayed(c):
    p = card(c, "manager_jo", "money_decide", money_action_id=1, decision="approve").json()
    assert c.post(f"/api/proposals/{p['id']}/confirm", headers=H("manager_jo")).status_code == 200
    assert c.post(f"/api/proposals/{p['id']}/confirm", headers=H("manager_jo")).status_code == 404


def test_cancelled_card_cannot_be_confirmed(c):
    p = card(c, "manager_jo", "money_decide", money_action_id=1, decision="approve").json()
    c.post(f"/api/proposals/{p['id']}/cancel", headers=H("manager_jo"))
    assert c.post(f"/api/proposals/{p['id']}/confirm", headers=H("manager_jo")).status_code == 404


def test_checkin_only_by_who_confirmed(c):
    """Bug found: anyone could file the authority check-in on someone else's change."""
    p = card(c, "manager_jo", "money_decide", money_action_id=1, decision="approve").json()
    c.post(f"/api/proposals/{p['id']}/confirm", headers=H("manager_jo"))
    assert c.post(f"/api/proposals/{p['id']}/checkin", headers=H("stocker_tim"), json={"works": False, "intended": False}).status_code == 404
    assert c.post(f"/api/proposals/{p['id']}/checkin", headers=H("manager_jo"), json={"works": True, "intended": True}).json() == {"status": "OK"}


def test_sql_in_ids_is_just_a_miss(c):
    assert card(c, "manager_jo", "money_decide", money_action_id="1 OR 1=1", decision="approve").status_code == 404


def test_unknown_role_is_404_not_500(c):
    """Bug found: send_offers with an unknown role crashed with a 500."""
    assert card(c, "chef_marco", "send_offers", role="astronaut").status_code == 404


def test_reset_needs_account_holder(c):
    """Bug found: anyone on the shop Wi-Fi could wipe the data with one POST."""
    assert c.post("/api/demo/reset").status_code == 401
    assert c.post("/api/demo/reset", headers=H("chef_marco")).status_code == 403
    assert c.post("/api/demo/reset", headers=H("owner_alex")).status_code == 200


def test_known_gap_sign_in_is_a_header(c):
    """NOT FIXED: X-Member is the whole sign-in. Anyone who types a header becomes the owner.
    This passes today on purpose so it stays visible; real sign-in (passkeys) replaces it."""
    assert c.get("/api/accounting/money-actions", headers=H("owner_alex")).status_code == 200

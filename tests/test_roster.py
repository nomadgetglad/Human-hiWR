"""Adding staff: who may add whom, at what level, with what pay. Unit tests on hiwr_roster and attacks through the UI API."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from conftest import OWNER
from hiwr_access import level
from hiwr_roster import add_member, roster, get_member, is_active
from server import app as appmod


# ---------- unit: hiwr_roster ----------

def test_manager_adds_staff_at_level_0(db):
    mid = add_member(db, "Maria", "Cook, grill", "chef_marco")
    assert mid == "cook_maria" and level(db, mid) == 0 and is_active(db, mid)
    assert get_member(db, mid) == {"id": "cook_maria", "name": "Maria", "title": "Cook, grill", "language": "en"}


def test_staff_and_lead_cannot_add_anyone(db):
    for who in ("cook_luis", "lead_rosa"):
        with pytest.raises(PermissionError):
            add_member(db, "Maria", "Cook", who)


def test_only_account_holder_gives_a_level(db):
    with pytest.raises(PermissionError):
        add_member(db, "Maria", "Cook", "manager_jo", lvl=1)
    assert level(db, add_member(db, "Maria", "Lead cook", OWNER, lvl=1)) == 1


def test_nobody_is_added_at_level_4(db):
    with pytest.raises(ValueError):
        add_member(db, "Maria", "Owner", OWNER, lvl=4)


@pytest.mark.parametrize("name", ["Maria Lopez", "", "   ", "R2D2", "x" * 31, "<script>"])
def test_first_names_only(db, name):
    with pytest.raises(ValueError):
        add_member(db, name, "Cook", OWNER)


@pytest.mark.parametrize("name", ["José", "Mary-Ann", "O'Neil", "Zoë"])
def test_real_first_names_pass(db, name):
    assert add_member(db, name, "Server", OWNER)


def test_pay_needs_finance_level(db):
    with pytest.raises(PermissionError):
        add_member(db, "Maria", "Cook", "chef_marco", role="cook", hourly_cents=1800)
    mid = add_member(db, "Maria", "Cook", "manager_jo", role="cook", hourly_cents=1800)
    assert db.execute("SELECT hourly_cents FROM staff WHERE member=?", (mid,)).fetchone()[0] == 1800


@pytest.mark.parametrize("cents", [0, 100, 212, 20001, -500])
def test_pay_out_of_range_refused(db, cents):
    with pytest.raises(ValueError):
        add_member(db, "Maria", "Cook", OWNER, role="cook", hourly_cents=cents or -1)


def test_role_and_rate_go_together(db):
    with pytest.raises(ValueError):
        add_member(db, "Maria", "Cook", OWNER, role="cook")
    with pytest.raises(ValueError):
        add_member(db, "Maria", "Cook", OWNER, hourly_cents=1800)


def test_same_name_gets_its_own_id(db):
    a = add_member(db, "Maria", "Cook", OWNER)
    b = add_member(db, "Maria", "Cook", OWNER)
    assert (a, b) == ("cook_maria", "cook_maria_2")


def test_roster_hides_pay_unless_asked(db):
    add_member(db, "Maria", "Cook", OWNER, role="cook", hourly_cents=1800)
    assert "hourly_cents" not in roster(db)[0]
    assert roster(db, show_pay=True)[0]["hourly_cents"] == 1800


def test_roster_rows_are_never_edited_or_deleted(db):
    mid = add_member(db, "Maria", "Cook", OWNER)
    for sql in ("UPDATE members SET first_name='Eve' WHERE id=?", "DELETE FROM members WHERE id=?",
                "UPDATE member_status_events SET status='inactive' WHERE member=?",
                "DELETE FROM member_status_events WHERE member=?"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql, (mid,))


# ---------- through the UI API ----------

@pytest.fixture
def c():
    appmod.reset()
    return TestClient(appmod.app, raise_server_exceptions=False)


def H(m):
    return {"X-Member": m}


def add(c, m, **params):
    r = c.post("/api/proposals", json={"kind": "add_member", "params": params}, headers=H(m))
    if r.status_code != 200:
        return r
    return c.post(f"/api/proposals/{r.json()['id']}/confirm", json={}, headers=H(m))


def test_team_is_first_in_the_menu_for_managers(c):
    assert c.get("/api/me", headers=H("chef_marco")).json()["menu"][0]["id"] == "team"
    assert "team" not in [x["id"] for x in c.get("/api/me", headers=H("fry_rosa")).json()["menu"]]


def test_card_first_nothing_written_until_confirm(c):
    r = c.post("/api/proposals", json={"kind": "add_member", "params": {"first_name": "Maria", "title": "Cook"}}, headers=H("chef_marco"))
    assert r.status_code == 200 and r.json()["title"] == "Add Maria to the team"
    assert c.get("/api/me", headers=H("cook_maria")).status_code == 401
    assert c.post(f"/api/proposals/{r.json()['id']}/confirm", json={}, headers=H("chef_marco")).status_code == 200
    me = c.get("/api/me", headers=H("cook_maria")).json()
    assert me["name"] == "Maria" and me["level"] == 0
    assert "Maria" in [m["name"] for m in c.get("/api/members").json()]


def test_staff_cannot_add_or_see_the_roster(c):
    assert add(c, "fry_rosa", first_name="Maria", title="Cook").status_code == 403
    assert c.get("/api/team", headers=H("fry_rosa")).status_code == 403


def test_manager_cannot_raise_a_level_through_the_ui(c):
    assert add(c, "chef_marco", first_name="Maria", title="Cook", level=3).status_code == 403
    assert add(c, "owner_alex", first_name="Maria", title="Sous chef", level=2).status_code == 200
    assert c.get("/api/me", headers=H("sous_maria")).json()["level"] == 2


def test_level_2_cannot_set_pay_or_see_it(c):
    assert add(c, "chef_marco", first_name="Maria", title="Cook", role="cook", hourly_cents=1800).status_code == 403
    assert all("hourly_cents" not in m for m in c.get("/api/team", headers=H("chef_marco")).json()["members"])
    assert add(c, "manager_jo", first_name="Maria", title="Cook", role="cook", hourly_cents=1800).status_code == 200
    rows = c.get("/api/team", headers=H("manager_jo")).json()["members"]
    assert next(m for m in rows if m["id"] == "cook_maria")["hourly_cents"] == 1800


def test_last_name_refused_through_the_ui(c):
    r = add(c, "owner_alex", first_name="Maria Lopez", title="Cook")
    assert r.status_code == 400 and "last name" in r.json()["detail"]


def test_bad_level_type_is_400_not_500(c):
    assert add(c, "owner_alex", first_name="Maria", title="Cook", level="boss").status_code == 400

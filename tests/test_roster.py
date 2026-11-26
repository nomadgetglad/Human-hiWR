"""Adding staff: who may add whom, at what level, with what pay. Unit tests on hiwr_roster and attacks through the UI API."""
import json
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


# ---------- a new account starts empty (no test data) ----------

def sign_in_first_time(c, member, setup_code):
    """What a person does on their phone: setup code, scan, type the first 6-digit code."""
    from hiwr_auth import totp, _step
    from datetime import datetime, timezone
    r = c.post("/api/auth/setup/start", json={"member_id": member, "setup_code": setup_code})
    assert r.status_code == 200, r.text
    code = totp(r.json()["secret"], _step(datetime.now(timezone.utc)))
    r = c.post("/api/auth/setup/finish", json={"member_id": member, "setup_code": setup_code, "code": code})
    assert r.status_code == 200, r.text


@pytest.fixture
def blank():
    appmod.reset(sample=False)
    c = TestClient(appmod.app, raise_server_exceptions=False, headers={"X-Human": "1"})
    sign_in_first_time(c, "owner_alex", appmod.STATE["owner_code"])
    yield c
    appmod.reset()


def test_blank_account_is_just_the_owner(blank):
    assert [m["id"] for m in blank.get("/api/members").json()] == ["owner_alex"]
    stranger = TestClient(appmod.app, raise_server_exceptions=False)
    assert stranger.get("/api/me", headers=H("owner_alex")).status_code == 401     # no switcher on your own account
    me = blank.get("/api/me", headers=H("owner_alex")).json()
    assert me["level"] == 4 and me["sample"] is False and me["menu"][0]["id"] == "team"


@pytest.mark.parametrize("path", ["/api/topbar", "/api/team", "/api/tasks", "/api/staff", "/api/prep", "/api/inventory/stew",
                                  "/api/inventory/warehouse", "/api/inventory/lab", "/api/cs/dishes", "/api/accounting/money-actions"])
def test_every_screen_works_empty(blank, path):
    assert blank.get(path, headers=H("owner_alex")).status_code == 200


def test_blank_has_no_sample_numbers(blank):
    bar = blank.get("/api/topbar", headers=H("owner_alex")).json()
    assert all(s["value"] == 0 for s in bar["stats"]) and bar["notifications"] == []
    assert "reservations" not in json.dumps(bar)
    blank.post("/api/demo/clock", json={"hour": 20}, headers=H("owner_alex"))
    assert appmod.db().execute("SELECT COUNT(*) FROM dish_sales").fetchone()[0] == 0


def test_first_step_on_a_blank_account_is_adding_staff(blank):
    assert add(blank, "owner_alex", first_name="Maria", title="Cook", role="cook", hourly_cents=1600).status_code == 200
    r = blank.post("/api/proposals", json={"kind": "setup_code", "params": {"member_id": "cook_maria"}})
    code = blank.post(f"/api/proposals/{r.json()['id']}/confirm", json={}).json()["result"].split(": ")[1][:9]
    phone = TestClient(appmod.app, raise_server_exceptions=False, headers={"X-Human": "1"})
    sign_in_first_time(phone, "cook_maria", code)
    assert phone.get("/api/me").json()["name"] == "Maria"
    assert "dishwasher" in blank.get("/api/team", headers=H("owner_alex")).json()["roles"]


def test_owner_switches_between_blank_and_sample(blank):
    assert blank.post("/api/demo/reset?sample=true", headers=H("owner_alex")).json()["sample"] is True
    assert len(blank.get("/api/members").json()) == 6
    assert blank.post("/api/demo/reset?sample=false", headers=H("chef_marco")).status_code == 403
    assert blank.post("/api/demo/reset?sample=false", headers=H("owner_alex")).json()["sample"] is False


def test_your_team_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "DB_PATH", str(tmp_path / "human.db"))
    appmod.reset(sample=False)
    c = TestClient(appmod.app, raise_server_exceptions=False, headers={"X-Human": "1"})
    sign_in_first_time(c, "owner_alex", appmod.STATE["owner_code"])
    assert add(c, "owner_alex", first_name="Maria", title="Cook").status_code == 200
    appmod.reset(sample=True)            # switch to the test restaurant and back: nothing of yours is lost
    appmod.reset(sample=False)
    assert [m["id"] for m in c.get("/api/members").json()] == ["owner_alex", "cook_maria"]
    appmod.reset()


# ---------- job role: picked from a list, by a manager or the account holder ----------

def test_job_role_comes_from_the_pick_list(c):
    roles = c.get("/api/team", headers=H("owner_alex")).json()["job_roles"]
    assert "Cook" in roles and "Dishwasher" in roles and "Owner" not in roles
    assert add(c, "owner_alex", first_name="Maria", title="Head honcho").status_code == 400
    assert add(c, "chef_marco", first_name="Maria", title="Dishwasher").status_code == 200


def test_only_manager_or_owner_sets_a_job_role(c):
    r = add(c, "fry_rosa", first_name="Maria", title="Cook")
    assert r.status_code == 403 and "job role" in r.json()["detail"]


# ---------- menu: a section shows only if you can open it and it has something in it ----------

def _menu(c, m):
    return {x["id"]: x for x in c.get("/api/me", headers=H(m)).json()["menu"]}


def test_blank_account_menu_is_only_where_you_start(blank):
    assert list(_menu(blank, "owner_alex")) == ["team", "assistant"]


def test_sample_menu_shows_sections_with_work(c):
    owner = _menu(c, "owner_alex")
    assert {"team", "staff", "inventory", "prep", "cs", "assistant", "accounting"} <= set(owner)
    assert "lab" not in owner["inventory"]["children"]
    assert "accounting" not in _menu(c, "chef_marco") and "team" not in _menu(c, "fry_rosa")


def test_new_staff_with_nothing_to_do_sees_little(c):
    assert add(c, "owner_alex", first_name="Maria", title="Dishwasher").status_code == 200
    m = _menu(c, "dishwasher_maria")
    assert "prep" not in m and "tasks" not in m and "team" not in m and "accounting" not in m


# ---------- business profile (the Human logo): account holder only ----------

def test_business_profile_is_owner_only(c):
    assert c.get("/api/settings", headers=H("manager_jo")).status_code == 403
    s = c.get("/api/settings", headers=H("owner_alex")).json()
    assert [l["level"] for l in s["sensitivity"]["levels"]] == [0, 1, 2, 3, 4]
    assert s["authenticator"]["status"] == "not_set_up" and s["authenticator"]["events"] == []


def test_every_confirm_card_outcome_is_logged(c):
    assert add(c, "owner_alex", first_name="Maria", title="Cook").status_code == 200
    r = c.post("/api/proposals", json={"kind": "add_member", "params": {"first_name": "Ana", "title": "Host"}}, headers=H("owner_alex"))
    c.post(f"/api/proposals/{r.json()['id']}/cancel", headers=H("owner_alex"))
    log = c.get("/api/settings", headers=H("owner_alex")).json()["confirmations"]
    assert [x["outcome"] for x in log[:2]] == ["cancelled", "confirmed"]


def test_confirm_log_is_append_only(c):
    import sqlite3 as _sq
    add(c, "owner_alex", first_name="Maria", title="Cook")
    with pytest.raises(_sq.DatabaseError):
        appmod.db().execute("UPDATE confirm_log SET outcome='cancelled'")
    with pytest.raises(_sq.DatabaseError):
        appmod.db().execute("DELETE FROM confirm_log")


# ---------- trial demos by business type (stock house, stew, lab), offered before a plan is bought ----------

@pytest.mark.parametrize("demo,has,lacks", [
    ("stock", {"inventory"}, {"prep", "cs", "staff"}),
    ("stew", {"staff", "prep", "cs", "inventory", "accounting"}, set()),
    ("lab", {"inventory", "accounting"}, {"prep", "cs"}),
])
def test_each_demo_shows_its_own_business(blank, demo, has, lacks):
    r = blank.post(f"/api/demo/reset?demo={demo}", headers=H("owner_alex")).json()
    assert r["demo"] == demo and r["sample"] is True
    menu = _menu(blank, "owner_alex")
    assert has <= set(menu) and not (lacks & set(menu))
    inv = menu.get("inventory", {}).get("children", [])
    assert {"stock": ["warehouse"], "stew": ["stew"], "lab": ["lab"]}[demo] == inv


def test_lab_demo_has_a_job_with_work_to_do(blank):
    blank.post("/api/demo/reset?demo=lab", headers=H("owner_alex"))
    lab = blank.get("/api/inventory/lab", headers=H("owner_alex")).json()
    job = lab["jobs"][0]
    assert job["name"].startswith("Henderson") and "contract" in job
    assert [p["status"] for p in job["phases"]][:4] == ["passed", "passed", "passed", "failed"]
    assert any(t["type"] for t in lab["tickets"])
    crew = blank.get("/api/inventory/lab", headers=H("carp_luis")).json()["jobs"][0]
    assert "contract" not in crew and "labor" not in crew["phases"][0]


def test_only_owner_switches_and_unknown_demo_is_refused(blank):
    assert blank.post("/api/demo/reset?demo=nope", headers=H("owner_alex")).status_code == 400
    blank.post("/api/demo/reset?demo=stew", headers=H("owner_alex"))
    assert blank.post("/api/demo/reset?demo=lab", headers=H("chef_marco")).status_code == 403
    assert blank.post("/api/demo/reset?sample=false", headers=H("owner_alex")).json()["demo"] is None


def test_business_profile_suggests_next_steps(blank):
    tips = {x["id"] for x in blank.get("/api/settings", headers=H("owner_alex")).json()["suggestions"]}
    assert {"team", "approvers", "assistant", "hiwr"} <= tips and "demo" not in tips
    blank.post("/api/demo/reset?demo=stew", headers=H("owner_alex"))
    tips = {x["id"] for x in blank.get("/api/settings", headers=H("owner_alex")).json()["suggestions"]}
    assert "demo" in tips and "team" not in tips and "approvers" not in tips


# ---------- Scan: the browser decodes; the server looks the code up ----------

def test_scan_finds_products_by_upc_and_tickets_by_id(c):
    r = c.post("/api/scan/lookup", json={"code": "036000291452", "kind": "UPC label"}, headers=H("stocker_tim")).json()
    assert [m["id"] for m in r["matches"]] == ["SALS-02"]
    r = c.post("/api/scan/lookup", json={"code": "SHP-0001", "kind": "QR code"}, headers=H("fry_rosa")).json()
    assert r["matches"][0]["type"] == "Ticket"
    assert c.post("/api/scan/lookup", json={"code": "nothing-like-this"}, headers=H("fry_rosa")).json()["matches"] == []
    assert c.post("/api/scan/lookup", json={"code": "SALS-02"}).status_code == 401


def test_scan_works_on_a_blank_account(blank):
    assert blank.post("/api/scan/lookup", json={"code": "012345678905"}, headers=H("owner_alex")).json()["matches"] == []


# ---------- Mesh: staff organizer from employee <-> sale history ----------

def test_mesh_ranks_who_fits_each_item(c):
    v = c.get("/api/mesh", headers=H("chef_marco")).json()
    assert v["own"] is False and v["lines"] > 100
    assert v["best"]["Fish tacos"]["member"] == "chef_marco" and v["best"]["House burger"]["member"] == "cook_luis"
    assert {p["name"] for p in v["by_member"]} >= {"Marco", "Rosa", "Luis"}


def test_staff_see_only_their_own_mesh(c):
    v = c.get("/api/mesh", headers=H("fry_rosa")).json()
    assert v["own"] is True and [p["member"] for p in v["by_member"]] == ["fry_rosa"]
    assert all(x["member"] == "fry_rosa" for items in v["by_item"].values() for x in items)


def test_mesh_is_empty_not_broken_on_a_blank_account(blank):
    assert blank.get("/api/mesh", headers=H("owner_alex")).json()["lines"] == 0


# ---------- Import: register sales -> inventory, through a confirm card ----------

def test_import_sales_updates_inventory_after_confirm(c):
    d = appmod.db()
    fish = d.execute("SELECT on_hand FROM materials WHERE material='white fish'").fetchone()[0]
    rows = [{"item": "fish tacos", "qty": 4, "hour": 19}, {"item": "036000291452", "qty": 2}, {"item": "Mystery soda", "qty": 1}]
    r = c.post("/api/proposals", json={"kind": "import_sales", "params": {"rows": rows}}, headers=H("chef_marco"))
    card = r.json()
    assert r.status_code == 200 and any("white fish" in x for x in card["lines"]) and any("Mystery soda" in x for x in card["lines"])
    assert d.execute("SELECT on_hand FROM materials WHERE material='white fish'").fetchone()[0] == fish   # nothing yet
    assert c.post(f"/api/proposals/{card['id']}/confirm", json={}, headers=H("chef_marco")).status_code == 200
    assert d.execute("SELECT on_hand FROM materials WHERE material='white fish'").fetchone()[0] == fish - 20
    assert d.execute("SELECT units FROM daily_sales WHERE sku='SALS-02'").fetchone()[0] >= 2


def test_import_needs_manager_and_matching_lines(c):
    rows = [{"item": "Fish tacos", "qty": 1}]
    assert c.post("/api/proposals", json={"kind": "import_sales", "params": {"rows": rows}}, headers=H("fry_rosa")).status_code == 403
    r = c.post("/api/proposals", json={"kind": "import_sales", "params": {"rows": [{"item": "nope", "qty": 1}]}}, headers=H("chef_marco"))
    assert r.status_code == 400

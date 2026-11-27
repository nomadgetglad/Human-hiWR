"""Role vocabulary: job titles in many words map to one schedule role, and words never unlock anything."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from conftest import OWNER
import hiwr_roles as R
from hiwr_access import level
from hiwr_roster import add_member, roster
from server import app as appmod


# ---------- the catalog itself ----------

def test_every_word_means_one_role():
    seen = {}
    for r in R.ROLES:
        for a in (r.key, r.en, r.es) + r.aliases_en + r.aliases_es:
            n = R.norm(a)
            assert seen.setdefault(n, r.key) == r.key, f"'{a}' means both {seen[n]} and {r.key}"


def test_keys_unique_and_transfers_point_at_real_roles():
    assert len(R.BY_KEY) == len(R.ROLES)
    for r in R.ROLES:
        assert set(r.trades) <= set(R.TRADES), r.key
        for other, share in r.transfers.items():
            assert other in R.BY_KEY and other != r.key and 0 < share <= 1, (r.key, other)
        assert r.duties and all(len(d) == 2 for d in r.duties), f"{r.key} needs duties in English and Spanish"


def test_schedule_roles_already_in_use_are_kept():
    for key in ("cook", "prep", "server", "host", "bartender", "dishwasher", "maker", "welder", "fitter", "doctor",
                "nurse"):
        assert R.role_for(key) == key


def test_covers_each_trade():
    for trade, n in (("restaurant", 15), ("retail", 10), ("lab", 15), ("factory", 5), ("health", 5)):
        assert len(R.roles_for_trade(trade)) >= n, trade


# ---------- matching titles ----------

@pytest.mark.parametrize("title,role", [
    ("Line Cook", "cook"), ("Cook II", "cook"), ("grill cook", "cook"), ("Chef de partie", "cook"),
    ("short-order cook", "cook"), ("Sushi Chef", "cook"), ("cocinera de línea", "cook"), ("Parrillero", "cook"),
    ("prep cook", "prep"), ("ayudante de cocina", "prep"), ("Pastry chef", "baker"), ("repostera", "baker"),
    ("dishie", "dishwasher"), ("kitchen porter", "dishwasher"), ("Lavaplatos", "dishwasher"),
    ("expeditor", "expo"), ("Waitress", "server"), ("mesera", "server"), ("Camarero", "server"),
    ("busboy", "busser"), ("garrotero", "busser"), ("Hostess", "host"), ("anfitriona", "host"),
    ("mixologist", "bartender"), ("cantinero", "bartender"), ("Key holder", "shift lead"),
    ("encargada de turno", "shift lead"), ("GM", "general manager"), ("gerente de tienda", "general manager"),
    ("Asst. Store Mgr", "assistant manager"), ("subgerente", "assistant manager"),
    ("Overnight Stocker", "stocker"), ("merchandiser", "stocker"), ("reponedora", "stocker"),
    ("receiving clerk", "receiver"), ("cycle counter", "inventory specialist"), ("order picker", "warehouse associate"),
    ("montacarguista", "forklift operator"), ("sales clerk", "sales associate"), ("dependienta", "sales associate"),
    ("cajera", "cashier"), ("foreperson", "foreman"), ("crew lead", "foreman"), ("capataz", "foreman"),
    ("Super", "superintendent"), ("jefe de obra", "superintendent"), ("framer", "carpenter"),
    ("Journeyman Electrician", "electrician"), ("pipefitter", "plumber"), ("fontanero", "plumber"),
    ("bricklayer", "mason"), ("albañil", "mason"), ("albanil", "mason"), ("drywaller", "drywall installer"),
    ("tablaroquero", "drywall installer"), ("peón", "laborer"), ("general laborer", "laborer"),
    ("excavator operator", "equipment operator"), ("assembler", "maker"), ("costurera", "maker"),
    ("RN", "nurse"), ("enfermera", "nurse"), ("CNA", "nursing assistant"), ("front desk", "receptionist"),
    ("barber", "stylist"), ("custodian", "cleaner"), ("conserje", "cleaner"), ("bookkeeper", "bookkeeper"),
])
def test_titles_map_to_their_role(title, role):
    assert R.role_for(title) == role


def test_seniority_and_schedule_words_are_read_not_lost():
    m = R.match("Sr. Lead Line Cook - nights, part time")
    assert m["role"] == "cook" and m["how"] == "contains"
    assert m["seniority"] == ["senior", "lead"] and m["schedule"] == ["nights", "part time"]
    assert R.match("Cook II")["role"] == "cook"
    assert R.match("cocinero de turno")["role"] == "cook"


def test_vague_word_does_not_steal_a_longer_title():
    """'cook helper' is a cook who is new, not a construction laborer."""
    m = R.match("cook helper")
    assert m["role"] == "cook" and m["seniority"] == ["helper"]
    assert R.role_for("helper") == "laborer"          # on its own it still means something


def test_two_roles_in_one_title():
    m = R.match("Server/Bartender")
    assert m["role"] in ("server", "bartender") and len(m["also"]) == 1


def test_unknown_titles_are_suggestions_only():
    m = R.match("servr")
    assert m["role"] is None and "server" in m["suggestions"]
    assert R.role_for("servr") is None
    assert R.match("chief vibes officer")["role"] is None
    assert R.match("")["role"] is None


def test_describe_in_both_languages():
    en, es = R.describe("cook", "en"), R.describe("cook", "es")
    assert en["label"] == "Cook" and es["label"] == "Cocinero"
    assert en["duties"][0] != es["duties"][0] and "food_handler" in en["usual_certs"]
    assert "line cook" in en["also_called"] and "cocinero de linea" in es["also_called"]
    assert R.describe("not a role") is None


# ---------- titles never unlock anything ----------

@pytest.mark.parametrize("title", ["General Manager", "Owner's assistant", "Controller", "Executive Chef", "Payroll clerk"])
def test_a_title_never_sets_a_level(db, title):
    mid = add_member(db, "Maria", title, OWNER)
    assert level(db, mid) == 0


def test_catalog_has_no_pay_or_level_fields():
    for r in R.ROLES:
        assert not any(k in vars(r) for k in ("level", "pay", "hourly_cents", "rate"))


# ---------- roster and hiring use it ----------

def test_schedule_role_typed_as_a_title_is_saved_as_the_role(db):
    add_member(db, "Maria", "Cook", OWNER, role="Line Cook", hourly_cents=1800)
    add_member(db, "Pedro", "Lavaplatos", OWNER, role="lavaplatos", hourly_cents=1600)
    add_member(db, "Ana", "Pit", OWNER, role="pit master", hourly_cents=1600)      # unknown: kept as typed
    roles = {m["name"]: m["role"] for m in roster(db, show_pay=True)}
    assert roles["Maria"] == "cook" and roles["Pedro"] == "dishwasher" and roles["Ana"] == "pit master"


def _candidate(db, name, history):
    cid = db.execute("INSERT INTO candidates(name) VALUES (?)", (name,)).lastrowid
    for role, months in history:
        db.execute("INSERT INTO candidate_experience VALUES (?,?,?, 'sample')", (cid, role, months))
    return cid


def test_experience_counts_every_title_for_the_role_and_part_of_related_ones(db):
    cid = _candidate(db, "Lu", [("Line Cook II", 12), ("cocinero", 6), ("Sous Chef", 4), ("prep cook", 8),
                                ("cashier", 30)])
    months, how = R.experience(db, cid, "cook")
    # 12 + 6 full; sous chef 4 * 1.0 + prep 8 * 0.75 = 10 related; cashier not related to cook
    assert months == 28
    assert any("Line Cook II" in h and "counts as cook" in h for h in how)
    assert any("prep cook" in h and "75%" in h for h in how)
    assert not any("cashier" in h for h in how)


def test_plain_role_history_reads_exactly_as_before(db):
    cid = _candidate(db, "Bo", [("dishwasher", 9)])
    assert R.experience(db, cid, "dishwasher") == (9, [])


def test_hiring_ranks_a_line_cook_for_a_cook_job(db):
    from hiwr_hiring import rank_candidates
    pos = db.execute("INSERT INTO positions(role, title, min_experience_months, hourly_cents, opened_by) "
                     "VALUES ('cook', 'Cook', 6, 1800, ?)", (OWNER,)).lastrowid
    _candidate(db, "Lu", [("Line Cook", 10)])
    _candidate(db, "Mo", [("mesero", 24)])
    ranked, not_eligible = rank_candidates(db, pos, [], "2026-12-01")
    assert [r["candidate"] for r in ranked] == ["Lu"]
    assert "10 months as cook" in ranked[0]["reasons"]
    assert any("Line Cook" in r for r in ranked[0]["reasons"])
    assert not_eligible[0]["candidate"] == "Mo"


# ---------- house titles ----------

def test_house_titles_need_manager_and_are_append_only(db):
    with pytest.raises(PermissionError):
        R.add_house_title(db, "Pit boss", "cook", "cook_luis")
    R.add_house_title(db, "Pit boss", "cook", "chef_marco")
    m = R.match("pit boss nights", db)
    assert m["role"] == "cook" and m["how"] == "house" and m["schedule"] == ["nights"]
    assert R.role_for("pit boss") is None            # only this business knows the word
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE role_title_events SET role='server'")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM role_title_events")
    R.retire_house_title(db, "Pit boss", "chef_marco")
    assert R.role_for("pit boss", db) is None
    assert db.execute("SELECT COUNT(*) FROM role_title_events").fetchone()[0] == 2


def test_house_title_cannot_redefine_a_known_word(db):
    with pytest.raises(ValueError):
        R.add_house_title(db, "Dishwasher", "general manager", "chef_marco")


def test_house_title_counts_in_hiring(db):
    R.add_house_title(db, "Pit boss", "cook", "chef_marco")
    cid = _candidate(db, "Lu", [("pit boss", 7)])
    assert R.experience(db, cid, "cook")[0] == 7


# ---------- through the app ----------

@pytest.fixture
def c():
    appmod.reset()
    return TestClient(appmod.app, raise_server_exceptions=False)


def H(m):
    return {"X-Member": m}


def test_match_endpoint_speaks_the_readers_language(c):
    en = c.get("/api/roles/match", params={"title": "cocinera de línea"}, headers=H("owner_alex")).json()
    es = c.get("/api/roles/match", params={"title": "Line cook"}, headers=H("fry_rosa")).json()
    assert en["role"] == es["role"] == "cook"
    assert en["label"] == "Cook" and es["label"] == "Cocinero"
    assert es["about"]["duties"][0].startswith("Cocina")
    assert not any(k in str(es) for k in ("hourly", "cents", "level"))


def test_match_endpoint_needs_sign_in(c):
    assert c.get("/api/roles/match", params={"title": "cook"}).status_code == 401


def test_add_member_card_says_how_the_title_reads(c):
    r = c.post("/api/proposals", headers=H("owner_alex"),
               json={"kind": "add_member", "params": {"first_name": "Maria", "title": "Sr. Line Cook"}})
    lines = " ".join(r.json()["lines"])
    assert "reads as Cook (senior)" in lines and "doesn't set the level" in lines


def test_house_title_through_the_app(c):
    staff = c.post("/api/proposals", headers=H("fry_rosa"),
                   json={"kind": "house_title", "params": {"title": "Pit boss", "role": "cook"}})
    assert staff.status_code == 403
    p = c.post("/api/proposals", headers=H("chef_marco"),
               json={"kind": "house_title", "params": {"title": "Pit boss", "role": "cook"}})
    assert p.status_code == 200 and "never edited" in " ".join(p.json()["lines"])
    assert c.post(f"/api/proposals/{p.json()['id']}/confirm", json={}, headers=H("chef_marco")).status_code == 200
    team = c.get("/api/team", headers=H("chef_marco")).json()
    assert {"title": "pit boss", "role": "cook", "label": "Cook"} in team["house_titles"]
    assert c.get("/api/roles/match", params={"title": "Pit Boss"}, headers=H("fry_rosa")).json()["how"] == "house"
    bad = c.post("/api/proposals", headers=H("chef_marco"),
                 json={"kind": "house_title", "params": {"title": "Server", "role": "cook"}})
    assert bad.status_code == 400

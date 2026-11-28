"""Income/expense vectors: totals must match the ledger to the cent, on every backend, and stay read-only."""
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

import hiwr_accounting as acct
import hiwr_vectors as vec
from hiwr_contacts import request_money_action, approve
from hiwr_processes import open_ticket, ticket_priority

FIN = "accounting_sue"
BACKENDS = [b for b in ("python", "numpy", "torch") if vec.backend(b) == b]


def _days(start, end):
    d = date.fromisoformat(start)
    while d <= date.fromisoformat(end):
        yield d.isoformat()
        d += timedelta(days=1)


def _month(db, ym, food, labor):
    """A small restaurant month. Odd cent amounts on purpose so rounding mistakes would show."""
    first = f"{ym}-01"
    last = vec._month_bounds(ym)[1]
    for i, d in enumerate(_days(first, last)):
        acct.post(db, d, "revenue", food + 37 * i, "POS food", "pos", f"{d}-food", "pos", department="food")
        acct.post(db, d, "revenue", food // 5 + 11 * i, "POS bar", "pos", f"{d}-bar", "pos", department="beverage")
        acct.post(db, d, "expense", labor + 3 * i, "hours worked", "timeclock", f"TC-{d}", "timeclock", department="food")
        acct.post(db, d, "expense", food * 31 // 100 + 1, "Sysco invoice", "invoice", f"SYS-{d}", "receiving",
                  counterparty="Sysco", department="food")
    acct.post(db, first, "expense", 850001, "rent", "bank", f"rent-{ym}", "bank", counterparty="Front St Properties")
    acct.confirm_all_proposed(db, FIN, first, last)


@pytest.fixture
def books_db(db):
    for ym, food in (("2026-08", 240013), ("2026-09", 250007), ("2026-10", 245019), ("2026-11", 262003)):
        _month(db, ym, food, 70009)
    # November oddities: a refund (double approved), capital purchase, a duplicate excluded, one left waiting
    a = request_money_action(db, "refund", 4801, "Refund t12", "chef_marco")
    approve(db, a, "manager_jo"); approve(db, a, FIN)
    acct.post_money_action(db, a, "2026-11-16", FIN, department="food")
    acct.confirm_all_proposed(db, FIN, "2026-11-16", "2026-11-16")
    cr = acct.post(db, "2026-11-12", "expense", 340000, "replace walk-in compressor", "bank", "CR-1", "bank",
                   counterparty="Carolina Refrigeration")
    acct.confirm(db, cr, FIN, reason="capital")
    dup = acct.post(db, "2026-11-03", "expense",
                    db.execute("SELECT amount_cents FROM acct_txns WHERE source_ref='SYS-2026-11-02'").fetchone()[0],
                    "ACH", "bank", "ACH-1", "bank", counterparty="Sysco")
    acct.exclude(db, dup, FIN, "payment of the invoice already counted")
    acct.post(db, "2026-11-15", "expense", 21218, "misc", "bank", "LOW-1", "bank", counterparty="Lowes")
    for day, v in (("2026-10-31", 650011), ("2026-11-30", 712003)):
        acct.record_inventory_count(db, "chef_marco", day, "food", v)
    return db


# ---------- ties to the ledger ----------
@pytest.mark.parametrize("how", BACKENDS)
@pytest.mark.parametrize("period", [("2026-11-01", "2026-11-30"), ("2026-08-01", "2026-11-30"), ("2026-11-10", "2026-11-12")])
def test_vector_totals_match_ledger_to_the_cent(books_db, how, period):
    n = acct.numbers(books_db, *period)["metrics"]
    t = vec.books(books_db, *period, how=how)["vectors"].total()
    assert t["gross_income"] == n["gross_sales"]
    assert t["net_sales"] == n["net_sales"]
    assert t["expenses"] == n["cogs"] + n["labor"] + n["occupancy"] + n["operating"]
    assert t["net"] == n["operating_profit"]
    assert all(isinstance(t[k], int) for k in t)          # cents stay whole integers


@pytest.mark.parametrize("how", BACKENDS)
@pytest.mark.parametrize("axis", ["day", "week", "month", "class", "group", "department", "source"])
def test_every_grouping_adds_back_to_the_same_total(books_db, how, axis):
    v = vec.books(books_db, "2026-08-01", "2026-11-30", how=how)["vectors"]
    parts = v.by(axis)
    total = v.total()
    for k in total:
        assert sum(p[k] for p in parts.values()) == total[k]
    assert sum(v.signed) == total["net"]                  # plain sum of the signed vector IS the net


def test_backends_agree(books_db):
    got = {b: vec.books(books_db, "2026-08-01", "2026-11-30", how=b)["vectors"].by("week") for b in BACKENDS}
    first = got[BACKENDS[0]]
    assert all(g == first for g in got.values())


def test_unknown_backend_falls_back_to_python():
    assert vec.backend("not-a-library") == "python"


def test_sign_rule_income_plus_signed_expenses_is_net_not_gross(db):
    acct.post(db, "2026-11-02", "revenue", 100000, "POS", "pos", "p1", "pos", department="food")
    acct.post(db, "2026-11-02", "expense", 30000, "Sysco", "invoice", "s1", "r", counterparty="Sysco", department="food")
    acct.confirm_all_proposed(db, FIN, "2026-11-02", "2026-11-02")
    v = vec.books(db, "2026-11-02", "2026-11-02")["vectors"]
    assert v.signed == [100000, -30000]
    t = v.total()
    assert (t["gross_income"], t["expenses"], t["net"]) == (100000, 30000, 70000)


def test_side_rows_never_count(books_db):
    b = vec.books(books_db, "2026-11-01", "2026-11-30")
    refs = {e.ref for e in b["vectors"].entries}
    assert "CR-1" not in refs and [e.ref for e in b["off_pl"]] == ["CR-1"]    # capital: shown, not in net
    assert "ACH-1" not in refs                                                # excluded duplicate
    assert [w["cents"] for w in b["waiting"]] == [21218]                      # still waiting on a person


def test_stock_change_is_added_once_like_the_ledger(books_db):
    b = vec.books(books_db, "2026-11-01", "2026-11-30")
    adj = [e for e in b["vectors"].entries if e.source == "stock_count"]
    assert [(e.cls, e.cents) for e in adj] == [("food_cost", 650011 - 712003)]


# ---------- anticipated stays separate ----------
def _dispute(db, cents):
    fin = {"invoice": "POS-1188", "expected": cents, "received": 0, "variance": -cents,
           "proposed_resolution": "refund 2 plates", "lands_in": "revenue adjustment"}
    return open_ticket(db, "DISPUTE", "Table 12 cold tacos", ticket_priority(db, "DISPUTE", cents // 100),
                       "2026-11-20T10:00", "server_kim", financial=fin)


def test_open_ticket_is_anticipated_not_net(books_db):
    before = vec.books(books_db, "2026-11-01", "2026-11-30")["vectors"].total()
    tid = _dispute(books_db, 3000)
    after = vec.books(books_db, "2026-11-01", "2026-11-30")["vectors"].total()
    assert before == after
    rows = vec.anticipated(books_db)
    assert [(r["ref"], r["cents"]) for r in rows] == [(tid, -3000)]
    assert vec.anticipated_totals(rows) == {"revenue adjustment": -3000}


def test_posted_ticket_money_is_not_counted_twice(books_db):
    tid = _dispute(books_db, 3000)
    a = request_money_action(books_db, "refund", 3000, "Refund t12", "chef_marco", ticket_id=tid)
    assert vec.anticipated(books_db)[0]["cents"] == -3000          # pending: still anticipated
    approve(books_db, a, "manager_jo"); approve(books_db, a, FIN)
    acct.post_money_action(books_db, a, "2026-11-21", FIN, department="food")
    r = vec.anticipated(books_db)[0]
    assert (r["cents"], r["already_booked"]) == (0, 3000)          # moved into the books, gone from anticipated


def test_pending_payroll_shows_as_anticipated_expense(db):
    a = request_money_action(db, "payroll", 125050, "Crew payroll", "pm")
    r = [x for x in vec.anticipated(db) if x["ref"] == f"money action {a}"][0]
    assert r["cents"] == -125050 and r["status"].startswith("PENDING")


# ---------- floats enter once, rounded to the cent ----------
def test_to_cents_rounds_half_up():
    assert vec.to_cents(7.5 * 1733) == 12998                     # 12997.5 -> 12998
    assert vec.to_cents("0.5") == 1 and vec.to_cents(2.4999) == 2


def test_lab_job_vector(db):
    from hiwr_lab import log_time
    db.execute("INSERT INTO staff(member, role, hourly_cents) VALUES ('crew_ana','framer',2850)")
    pid = db.execute("INSERT INTO lab_projects(name, contract_cents, starts_on, target_end) VALUES "
                     "('Oak St house', 41000000, '2026-11-01', '2027-06-01')").lastrowid
    db.execute("INSERT INTO lab_phases(project_id, seq, name, labor_budget_cents) VALUES (?,1,'framing',500000)", (pid,))
    log_time(db, pid, 1, "crew_ana", "2026-11-03", 7.5, 2850)    # 21375
    log_time(db, pid, 1, "crew_bo", "2026-11-04", 8.25, 1733)    # 14297.25 -> 14297
    j = vec.lab(db, pid)
    by = j["vectors"].by("class")
    assert by["crew_framer"]["expenses"] == 21375 and by["crew_unassigned"]["expenses"] == 14297
    assert j["vectors"].total()["net"] == 41000000 - 21375 - 14297


def test_stew_estimate_shown_beside_books_without_a_winner(db):
    db.execute("INSERT INTO materials VALUES ('tortilla','ea',100,0)")
    db.execute("INSERT INTO material_sources VALUES ('tortilla','Sysco',2,12,1,1)")
    db.execute("INSERT INTO materials VALUES ('fish','oz',100,1)")
    db.execute("INSERT INTO material_sources VALUES ('fish','Harbor',1,55,1,1)")
    db.execute("INSERT INTO recipes VALUES ('fish tacos','dish',1,'plate',1400)")
    db.executemany("INSERT INTO recipe_lines VALUES (?,?,?,?)", [("fish tacos", "tortilla", 3, "ea"),
                                                                ("fish tacos", "fish", 4.5, "oz")])
    db.execute("INSERT INTO dish_sales VALUES ('2026-11-02', 18, 'fish tacos', 20)")
    ops = vec.stew(db, "2026-11-02", "2026-11-02")
    t = ops.total()
    assert t["gross_income"] == 28000 and t["expenses"] == 20 * vec.to_cents(3 * 12 + 4.5 * 55)   # 20 x 284
    acct.post(db, "2026-11-02", "revenue", 27500, "POS food", "pos", "d1", "pos", department="food")
    acct.confirm_all_proposed(db, FIN, "2026-11-02", "2026-11-02")
    rows = vec.side_by_side(vec.books(db, "2026-11-02", "2026-11-02")["vectors"], ops, "Stew dish sales")
    food = [r for r in rows if r["class"] == "food_sales"][0]
    assert (food["books"], food["operations"], food["gap"]) == (27500, 28000, -500)
    assert "manager decides" in food["decision"]


# ---------- pulse and who sees what ----------
def test_net_vs_3_month_average(books_db):
    p = vec.net_vs_3mo(books_db, "2026-11")
    prior = [vec.books(books_db, *vec._month_bounds(m))["vectors"].total()["net"] for m in ("2026-08", "2026-09", "2026-10")]
    assert p["months_in_avg"] == 3
    assert p["avg_3mo_net"] == vec.to_cents(Decimal(sum(prior)) / 3)
    assert p["net"] == acct.numbers(books_db, "2026-11-01", "2026-11-30")["metrics"]["operating_profit"]
    assert p["not_counted_yet"] == 1


def test_no_baseline_is_said_plainly(db):
    p = vec.net_vs_3mo(db, "2026-11")
    assert p["pulse"] == "No baseline yet" and p["avg_3mo_net"] is None


def test_pulse_by_role(books_db):
    owner_line = vec.pulse(books_db, FIN, "2026-11")
    assert "$" in owner_line and "net" in owner_line
    manager_line = vec.pulse(books_db, "chef_marco", "2026-11")
    assert "$" not in manager_line and "%" in manager_line
    cook_line = vec.pulse(books_db, "cook_luis", "2026-11")
    assert "$" not in cook_line and "%" not in cook_line


def test_report_needs_finance(books_db):
    assert "check: sum of the signed vector" in vec.report(books_db, FIN, "2026-11-01", "2026-11-30")
    with pytest.raises(PermissionError):
        vec.report(books_db, "chef_marco", "2026-11-01", "2026-11-30")


def test_read_only(books_db):
    tables = ("acct_txns", "acct_class_events", "acct_flags", "money_actions", "approvals", "tickets", "ticket_events")
    count = lambda: {t: books_db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    _dispute(books_db, 3000)
    before = count()
    vec.report(books_db, FIN, "2026-08-01", "2026-11-30", axis="week")
    vec.pulse(books_db, FIN, "2026-11")
    vec.anticipated(books_db)
    assert count() == before


def test_partial_month_is_compared_with_the_same_days(books_db):
    p = vec.net_vs_3mo(books_db, "2026-11", through="2026-11-10")
    prior = [vec.books(books_db, f"{m}-01", f"{m}-10")["vectors"].total()["net"] for m in ("2026-08", "2026-09", "2026-10")]
    assert p["partial"] and p["avg_3mo_net"] == vec.to_cents(Decimal(sum(prior)) / 3)

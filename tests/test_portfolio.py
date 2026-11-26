"""Portfolio demo: RPM per line in its own unit, lots, appointments with bookkeeping, late fees. Sample data."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ui"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hiwr"))
from server import app as appmod  # noqa: E402
import hiwr_portfolio as pf  # noqa: E402


@pytest.fixture
def c():
    appmod.reset(demo="portfolio")
    yield TestClient(appmod.app, raise_server_exceptions=False)
    appmod.reset()


def H(m):
    return {"X-Member": m}


def confirm(c, m, kind, params):
    r = c.post("/api/proposals", json={"kind": kind, "params": params}, headers=H(m))
    if r.status_code != 200:
        return r
    return c.post(f"/api/proposals/{r.json()['id']}/confirm", json={}, headers=H(m))


def test_each_line_has_rpm_in_its_own_unit_and_a_next_up(c):
    v = c.get("/api/portfolio", headers=H("owner_alex")).json()
    units = {x["id"]: x["unit"] for x in v["lines"]}
    assert units == {"assembly": "pipes", "service": "clients", "cafe": "table turns"}
    assert all(x["rpm"] > 0 and x["next"] for x in v["lines"])
    assert 0 < v["margin_pct"] < 100 and abs(sum(x["share_pct"] for x in v["mix"]) - 100) < 0.5
    staff = c.get("/api/portfolio", headers=H("tech_luis"))
    assert staff.status_code == 403                                     # staff: not the portfolio


def test_tech_fee_minimum_and_trip():
    f = pf.tech_fee(0.5)
    assert f["billed_hours"] == 1.0 and f["total_cents"] == pf.TECH_RATE_CENTS + pf.TRIP_FEE_CENTS
    assert pf.tech_fee(2.5)["labor_cents"] == round(2.5 * pf.TECH_RATE_CENTS)


def test_business_day_alert_skips_weekends_and_holidays():
    assert pf.business_day_before("2026-11-23") == "2026-11-20"         # Monday -> Friday
    assert pf.business_day_before("2026-11-27") == "2026-11-25"         # day after Thanksgiving -> Wednesday


def test_alert_one_business_day_before_then_fee_waits_for_two_approvers(c):
    appts = {a["client"]: a for a in c.get("/api/portfolio", headers=H("manager_jo")).json()["appointments"]}
    assert appts["Seaside Laundry"]["state"] == "alert" and appts["Seaside Laundry"]["reminder"]
    notes = c.get("/api/topbar", headers=H("manager_jo")).json()["notifications"]
    assert any("Seaside Laundry" in n["text"] and "avoid" in n["text"] for n in notes)
    hm = appts["Harbor Marine"]
    assert hm["state"] == "late_fee_pending"
    assert confirm(c, "manager_jo", "propose_late_fee", {"appt_id": hm["id"]}).status_code == 200
    hm = next(a for a in c.get("/api/portfolio", headers=H("manager_jo")).json()["appointments"] if a["id"] == hm["id"])
    assert hm["state"] == "late_fee_awaiting_approval" and not any(l["kind"] == "late_fee" for l in hm["ledger"])
    fee = next(a for a in c.get("/api/accounting/money-actions", headers=H("accounting_sue")).json() if a["scope"] == "fee")
    assert confirm(c, "manager_jo", "money_decide", {"money_action_id": fee["id"], "decision": "approve"}).status_code == 403  # asked for it
    assert confirm(c, "accounting_sue", "money_decide", {"money_action_id": fee["id"], "decision": "approve"}).status_code == 200
    assert confirm(c, "owner_alex", "money_decide", {"money_action_id": fee["id"], "decision": "approve"}).status_code == 200
    hm = next(a for a in c.get("/api/portfolio", headers=H("manager_jo")).json()["appointments"] if a["id"] == hm["id"])
    assert hm["state"] == "late_fee_charged" and any(l["kind"] == "late_fee" for l in hm["ledger"])


def test_booking_an_open_slot_starts_its_ledger(c):
    v = c.get("/api/portfolio", headers=H("manager_jo")).json()
    slot = v["slots"]["service"][0]
    r = confirm(c, "manager_jo", "book_appointment", {"line": "service", "client": "Cape Fear Brewing", "starts_at": slot,
                                                      "what": "Glycol chiller check", "price_cents": 22000, "deposit_cents": 5000})
    assert r.status_code == 200
    v = c.get("/api/portfolio", headers=H("manager_jo")).json()
    a = next(x for x in v["appointments"] if x["client"] == "Cape Fear Brewing")
    assert a["balance_cents"] == 17000 and slot not in v["slots"]["service"]
    with pytest.raises(Exception):
        appmod.db().execute("DELETE FROM pf_ledger")

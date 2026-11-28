"""Sensitivity to falling numbers (1-10), its refresh schedule, and the Set up role screen."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ui"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hiwr"))
from server import app as appmod  # noqa: E402
import hiwr_sensitivity as sens  # noqa: E402


@pytest.fixture
def c():
    appmod.reset(demo="stew")
    yield TestClient(appmod.app, raise_server_exceptions=False)
    appmod.reset()


def H(m):
    return {"X-Member": m}


def confirm(c, m, kind, params):
    r = c.post("/api/proposals", json={"kind": kind, "params": params}, headers=H(m))
    if r.status_code != 200:
        return r
    return c.post(f"/api/proposals/{r.json()['id']}/confirm", json={}, headers=H(m))


def test_higher_sensitivity_flags_at_least_as_much():
    assert sens.threshold_pct(1) > sens.threshold_pct(10)
    assert sens.ai_rule(1)[0] == "computed data only" and sens.ai_rule(10)[0] == "project from trends"


def test_owner_sets_the_dial_and_it_is_logged(c):
    assert confirm(c, "manager_jo", "set_sensitivity", {"level": 9}).status_code == 403
    assert confirm(c, "owner_alex", "set_sensitivity", {"level": 9}).status_code == 200
    s = c.get("/api/settings", headers=H("owner_alex")).json()
    assert s["drop_sensitivity"]["level"] == 9 and s["drop_sensitivity"]["threshold_pct"] == 9
    assert any("sensitivity" in x["title"].lower() for x in s["confirmations"])
    p = s["drop_sensitivity"]["preview"]
    assert p["10"] >= p["5"] >= p["1"]


def test_refresh_after_close_skips_closed_days():
    assert sens.last_refresh_point("close", "2026-11-23", 9) == ("2026-11-21", 22)      # Monday morning -> Saturday close
    assert sens.last_refresh_point("close", "2026-11-27", 23) == ("2026-11-27", 22)
    assert sens.last_refresh_point("close", "2026-11-27", 10) == ("2026-11-25", 22)     # Thanksgiving is closed
    assert sens.last_refresh_point("live", "2026-11-27", 10) is None


def test_set_up_role_level_capped_at_your_own(c):
    assert confirm(c, "chef_marco", "save_role", {"role": "Prep cook", "level": 3}).status_code == 403   # Marco is level 2
    assert confirm(c, "chef_marco", "save_role", {"role": "Prep cook", "level": 1, "reports_to": "Chef",
                                                  "steps": ["Knife skills", "Labels and dates"]}).status_code == 200
    roles = {r["role"]: r for r in c.get("/api/roles", headers=H("chef_marco")).json()["roles"]}
    assert roles["Prep cook"]["reports_to"] == "Chef" and roles["Prep cook"]["level"] == 1 and len(roles["Prep cook"]["steps"]) == 2
    assert c.get("/api/roles", headers=H("fry_rosa")).status_code == 403

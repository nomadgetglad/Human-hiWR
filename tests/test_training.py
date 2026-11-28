"""Job roles added by managers, with training steps; sign-offs by a manager or a trained lead, never yourself."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ui"))
from server import app as appmod  # noqa: E402


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


def test_manager_adds_a_role_with_training_and_it_joins_the_list(c):
    assert confirm(c, "fry_rosa", "add_role", {"role": "Barista"}).status_code == 403
    assert confirm(c, "chef_marco", "add_role", {"role": "Barista", "steps": ["Espresso dial-in", "Milk"]}).status_code == 200
    assert "Barista" in c.get("/api/team", headers=H("chef_marco")).json()["job_roles"]
    assert confirm(c, "chef_marco", "add_role", {"role": "barista"}).status_code == 400          # already there


def test_trainee_until_signed_off_and_never_by_yourself(c):
    team = {m["id"]: m for m in c.get("/api/team", headers=H("chef_marco")).json()["members"]}
    tim = team["stocker_tim"]["training"]
    assert tim["trainee"] and tim["total"] == 4 and tim["done"] == 0
    step = tim["steps"][0]["step"]
    assert confirm(c, "stocker_tim", "sign_training", {"member_id": "stocker_tim", "step": step}).status_code in (403, 404)
    assert confirm(c, "chef_marco", "sign_training", {"member_id": "stocker_tim", "step": step}).status_code == 200
    tim = {m["id"]: m for m in c.get("/api/team", headers=H("chef_marco")).json()["members"]}["stocker_tim"]["training"]
    assert tim["done"] == 1 and tim["steps"][0]["signed_by"] == "chef_marco"
    me = c.get("/api/me", headers=H("stocker_tim")).json()
    assert me["level"] == 0                                                                        # training changes no access

"""Staffing questions are answered from the schedule right away, without waiting on the local model."""
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ui"))
from server import app as appmod  # noqa: E402


class NoModel:
    model = "none"

    def turn(self, *a, **k):
        raise AssertionError("the model should not be called for a staffing question")


@pytest.fixture
def c(monkeypatch):
    appmod.reset()
    from agent.runner import Agent
    from agent import config as config_mod

    def new_agent(m):
        cfg = config_mod.load("/nonexistent", {"mode": "local"})
        return Agent(appmod.db(), cfg, m["id"], backends={"local": NoModel()}, clock=appmod.now_iso, lock=appmod.LOCK)

    monkeypatch.setattr(appmod, "_new_agent", new_agent)
    return TestClient(appmod.app, raise_server_exceptions=False)


def ask(c, m, q):
    return c.post("/api/assistant", json={"text": q}, headers={"X-Member": m})


def test_whos_short_is_instant_and_from_the_schedule(c):
    t0 = time.time()
    r = ask(c, "chef_marco", "Are we short anyone tonight?")
    assert r.status_code == 200 and time.time() - t0 < 2
    body = r.json()
    assert body["backend"] == "schedule" and "Cook" in body["text"] and "Dishwasher" in body["text"]
    hist = c.get("/api/assistant", headers={"X-Member": "chef_marco"}).json()["messages"]
    assert [m["role"] for m in hist] == ["user", "assistant"]


def test_staff_get_only_their_own_shift(c):
    r = ask(c, "fry_rosa", "who's working tonight?").json()
    assert r["backend"] == "schedule" and r["text"].startswith("You're on today") and "Dishwasher" not in r["text"]

"""Fryer run-out risk (hiwr_torch). Runs with or without PyTorch installed."""
import pytest
from fastapi.testclient import TestClient

import hiwr_torch as ht
from server import app as appmod

needs_torch = pytest.mark.skipif(ht.torch is None, reason="PyTorch not installed")


@needs_torch
def test_more_batches_never_raise_the_risk():
    ch = ht.runout_chances([300, 340, 310, 360], 330, 40, 48)
    assert ch[0] == 1.0 and ch[-1] == 0.0
    assert all(a >= b for a, b in zip(ch, ch[1:]))


@needs_torch
def test_same_seed_same_answer():
    assert ht.runout_chances([10, 12], 11, 0, 4) == ht.runout_chances([10, 12], 11, 0, 4)


def test_no_history_means_no_risk_line():
    assert ht.runout_chances([], 50, 0, 48) is None
    assert ht.runout_chances([0, 0], 50, 0, 48) is None


def test_prep_screen_works_with_or_without_torch(monkeypatch):
    appmod.reset()
    c = TestClient(appmod.app)
    r = c.get("/api/prep", headers={"X-Member": "owner_alex"}).json()
    if ht.torch is None:
        assert r["risk"] is None
    else:
        assert 0 <= r["risk"]["risk_pct"] <= 100 and r["risk"]["device"] in ("cpu", "cuda")
    monkeypatch.setattr(ht, "torch", None)
    assert c.get("/api/prep", headers={"X-Member": "owner_alex"}).json()["risk"] is None

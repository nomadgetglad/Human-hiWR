"""Recall: recurring numbers and averages from the data, searchable, scoped by level."""
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


def test_recall_finds_sales_cooks_and_vendors(c):
    r = c.get("/api/recall?q=fish tacos", headers=H("manager_jo")).json()
    kinds = {f["kind"] for f in r["facts"]}
    assert {"sales", "people"} <= kinds
    sysco = c.get("/api/recall?q=sysco", headers=H("manager_jo")).json()["facts"]
    assert any("short" in f["title"] for f in sysco)


def test_staff_recall_only_their_own(c):
    facts = c.get("/api/recall", headers=H("fry_rosa")).json()["facts"]
    assert facts and all(f["who"] == "fry_rosa" for f in facts)
    assert c.get("/api/recall?q=sysco", headers=H("fry_rosa")).json()["facts"] == []


def test_recall_on_portfolio_and_blank(c):
    appmod.reset(demo="portfolio")
    f = c.get("/api/recall?q=late", headers=H("manager_jo")).json()["facts"]
    assert any("Harbor Marine" in x["title"] for x in f)
    import hiwr_recall
    from hiwr_contacts import connect
    assert hiwr_recall.facts(connect(), "owner_alex", 4, "2026-11-20") == []      # a brand-new account: no facts, no errors

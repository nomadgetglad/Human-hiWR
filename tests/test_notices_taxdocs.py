"""Deep-clean notice -> scheduled tasks (Jo), and W-2s posted by Finance (Sue). Sample data."""
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


def test_deep_clean_notice_becomes_tasks_for_closers(c):
    assert confirm(c, "fry_rosa", "post_notice", {}).status_code == 403          # staff can't post
    assert confirm(c, "manager_jo", "post_notice", {}).status_code == 200
    n = c.get("/api/notices", headers=H("manager_jo")).json()["notices"][0]
    assert len(n["tasks"]) == 5 and len({t["due_on"] for t in n["tasks"]}) == 5
    week = {d["day"]: d["shifts"] for d in c.get("/api/schedule", headers=H("manager_jo")).json()["week"]}
    for t in n["tasks"]:                                                       # every task goes to someone closing that night
        assert any(s["member"] == t["member"] and s["closes"] for s in week[t["due_on"]])
    rosa = [t for t in n["tasks"] if t["member"] == "fry_rosa"]
    if rosa:
        mine = [x for x in c.get("/api/tasks", headers=H("fry_rosa")).json() if x["kind"] == "notice"]
        assert mine and confirm(c, "fry_rosa", "notice_task_done", {"task_id": mine[0]["id"]}).status_code == 200
        assert any(x["status"] == "done" for x in c.get("/api/tasks", headers=H("fry_rosa")).json() if x["kind"] == "notice")


def test_w2s_finance_posts_each_sees_only_their_own_and_opens_are_logged(c):
    assert confirm(c, "chef_marco", "post_w2s", {"year": 2025}).status_code == 403
    assert confirm(c, "accounting_sue", "post_w2s", {"year": 2025}).status_code == 200
    assert confirm(c, "accounting_sue", "post_w2s", {"year": 2025}).status_code == 409   # once a year
    mine = c.get("/api/docs", headers=H("fry_rosa")).json()["mine"]
    assert [d["kind"] for d in mine] == ["W-2"]
    doc = c.get(f"/api/docs/{mine[0]['id']}", headers=H("fry_rosa")).json()
    assert doc["box1_wages"] == 3120000 and "ssn" not in str(doc).lower()
    tims = c.get("/api/docs", headers=H("stocker_tim")).json()["mine"][0]["id"]
    assert c.get(f"/api/docs/{tims}", headers=H("fry_rosa")).status_code == 403
    posted = c.get("/api/docs", headers=H("accounting_sue")).json()["posted"]
    assert next(p for p in posted if p["member"] == "fry_rosa")["views"] == 1
    with pytest.raises(Exception):
        appmod.db().execute("DELETE FROM tax_document_views")

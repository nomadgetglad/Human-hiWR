"""Chat history: browse by month, then day and time; find from the chat box. Access is checked before reading.

The test restaurant comes with a few months of sample history (hiwr_chatlog.SAMPLE_TEAM / SAMPLE_ASSISTANT).
"""
import sys

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT

sys.path.insert(0, str(ROOT))

from server import app as appmod  # noqa: E402
from agent import config as config_mod  # noqa: E402
from agent.runner import Agent  # noqa: E402
import hiwr_chatlog as chatlog  # noqa: E402
from hiwr_access import set_level  # noqa: E402


class FakeModel:
    model = "fake-local"

    def turn(self, system, history, user_text, tools, execute):
        return "The walk-in was fixed on Oct 3."


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setenv("HUMAN_CHAT_EMBED", "off")
    appmod.reset()

    def new_agent(m):
        cfg = config_mod.load("/nonexistent", {"mode": "local"})
        return Agent(appmod.db(), cfg, m["id"], backends={"local": FakeModel()}, clock=appmod.now_iso, lock=appmod.LOCK)

    monkeypatch.setattr(appmod, "_new_agent", new_agent)
    return TestClient(appmod.app, raise_server_exceptions=False)


def H(m):
    return {"X-Member": m}


def find(c, m, q):
    r = c.get("/api/history/search", params={"q": q}, headers=H(m))
    assert r.status_code == 200, r.text
    return r.json()


def texts(result):
    return [h["text"] for h in result["hits"]]


# ---------- browsing ----------

def test_months_newest_first_with_counts(c):
    v = c.get("/api/history/months", headers=H("chef_marco")).json()
    assert [m["month"] for m in v["months"]] == ["2026-11", "2026-10", "2026-09"]
    oct_ = v["months"][1]
    assert oct_["label"] == "October 2026"
    assert oct_["by_source"]["assistant"] == 4          # his two questions and their answers
    assert oct_["count"] == sum(oct_["by_source"].values())


def test_month_groups_by_day_and_time(c):
    v = c.get("/api/history?month=2026-10", headers=H("chef_marco")).json()
    days = [d["day"] for d in v["days"]]
    assert days == sorted(days) and days[0] == "2026-10-03"
    first = v["days"][0]
    assert first["label"] == "Saturday, Oct 3"
    assert [m["time"] for m in first["messages"]] == ["09:30", "09:41", "09:50", "09:50"]
    assert [m["who"] for m in first["messages"][2:]] == ["You", "Assistant"]     # same minute: question first
    only_team = c.get("/api/history?month=2026-10&source=team", headers=H("chef_marco")).json()
    assert all(m["source"] == "team" for d in only_team["days"] for m in d["messages"])


def test_bad_month_and_empty_search_are_refused(c):
    assert c.get("/api/history?month=2026-13", headers=H("chef_marco")).status_code == 400
    assert c.get("/api/history?month=nov", headers=H("chef_marco")).status_code == 400
    assert c.get("/api/history?month=2026-11&source=payroll", headers=H("chef_marco")).status_code == 400
    assert c.get("/api/history/search?q=", headers=H("chef_marco")).status_code == 400


def test_new_messages_land_in_history(c):
    c.post("/api/chat", json={"to": "team", "text": "Mop sink drain is slow"}, headers=H("stocker_tim"))
    hit = find(c, "chef_marco", "mop sink")["hits"][0]
    assert hit["where"] == "Whole team" and hit["who"] == "Tim" and len(hit["time"]) == 5
    months = [m["month"] for m in c.get("/api/history/months", headers=H("chef_marco")).json()["months"]]
    assert hit["month"] in months


def test_assistant_questions_are_kept_after_new_conversation(c):
    assert c.post("/api/assistant", json={"text": "When was the walk-in fixed?"}, headers=H("chef_marco")).status_code == 200
    c.post("/api/assistant/clear", json={}, headers=H("chef_marco"))
    assert c.get("/api/assistant", headers=H("chef_marco")).json()["messages"] == []   # fresh conversation
    r = find(c, "chef_marco", "walk-in fixed")
    assert "When was the walk-in fixed?" in texts(r) and "The walk-in was fixed on Oct 3." in texts(r)
    # the fast schedule answers are kept too
    c.post("/api/assistant", json={"text": "Are we short anyone tonight?"}, headers=H("chef_marco"))
    assert any(h["source"] == "assistant" for h in find(c, "chef_marco", "short anyone tonight")["hits"])


# ---------- finding ----------

def test_every_word_must_match_newest_first(c):
    r = find(c, "chef_marco", "fryer oil")
    assert r["how"] == "words"
    assert [h["day"] for h in r["hits"]] == ["2026-11-18", "2026-09-04"]
    assert find(c, "chef_marco", "fryer oil smoke")["hits"][0]["day"] == "2026-09-04"   # 'smokes' -> 'smoke'
    assert find(c, "chef_marco", "fryer unicorn")["hits"] == []


def test_month_from_and_in_filters(c):
    r = find(c, "fry_rosa", "october from:marco")
    assert r["filters"]["month"] == "2026-10" and r["filters"]["from"] == "marco"
    assert r["hits"] and all(h["who"] == "Marco" and h["month"] == "2026-10" for h in r["hits"])
    assert all(h["source"] == "dm" for h in find(c, "fry_rosa", "in:dm")["hits"])
    assert find(c, "fry_rosa", "2026-09 saturday")["hits"][0]["text"].startswith("Can you cover Saturday")
    assert all(h["mine"] for h in find(c, "fry_rosa", "from:me")["hits"])
    mine = find(c, "chef_marco", "october from:marco")["hits"]           # your own name finds your messages
    assert mine and all(h["mine"] for h in mine)


def test_hits_carry_where_to_jump(c):
    h = find(c, "chef_marco", "inspection")["hits"][0]
    assert (h["month"], h["day"], h["time"]) == ("2026-10", "2026-10-15", "14:00")
    keys = [m["key"] for d in c.get(f"/api/history?month={h['month']}", headers=H("chef_marco")).json()["days"]
            for m in d["messages"]]
    assert h["key"] in keys


# ---------- access ----------

def test_direct_messages_only_for_the_two_people_in_them(c):
    # Marco and Rosa's thread about Saturday close
    assert find(c, "fry_rosa", "saturday close")["hits"]
    assert find(c, "chef_marco", "saturday close")["hits"]
    for outsider in ("owner_alex", "manager_jo", "stocker_tim"):      # owner and manager included
        assert find(c, outsider, "saturday close")["hits"] == []
        for month in ("2026-09", "2026-10"):
            v = c.get(f"/api/history?month={month}", headers=H(outsider)).json()
            assert not any("Saturday close" in m["text"] or "tomato" in m["text"].lower()
                           for d in v["days"] for m in d["messages"])


def test_filter_only_searches_cant_reach_other_peoples_threads(c):
    # no words, just a filter: still only what you may see
    hits = find(c, "owner_alex", "in:dm")["hits"]
    assert hits and all(h["with"] == "manager_jo" for h in hits)


def test_assistant_history_is_yours_only(c):
    assert find(c, "chef_marco", "inspectors")["hits"]
    for other in ("owner_alex", "manager_jo", "fry_rosa"):
        assert find(c, other, "inspectors")["hits"] == []
    v = c.get("/api/history/months", headers=H("owner_alex")).json()
    assert v["months"][1]["by_source"]["assistant"] == 0             # October: Marco's questions, not Alex's


def test_answers_given_at_a_higher_level_are_held_back_after_a_lower_level(c):
    d = appmod.db()
    chatlog.record_assistant(d, "manager_jo", 3, "W-2 totals for this year?", "W-2 totals: sample", "local", "2026-11-19T10:00")
    assert find(c, "manager_jo", "W-2 totals")["hits"]
    set_level(d, "manager_jo", 2, "owner_alex")
    r = find(c, "manager_jo", "W-2 totals")
    assert r["hits"] == [] and r["held"] == 2
    assert c.get("/api/history/months", headers=H("manager_jo")).json()["held"] == 2


def test_history_is_append_only(c):
    d = appmod.db()
    for sql in ("DELETE FROM assistant_messages", "UPDATE assistant_messages SET text='x'",
                "DELETE FROM team_messages", "UPDATE team_messages SET text='x'"):
        with pytest.raises(Exception):
            d.execute(sql)


def test_strangers_get_nothing(c):
    assert c.get("/api/history/months", headers=H("nobody_here")).status_code in (401, 403, 404)
    assert c.get("/api/history/search?q=fish", headers=H("nobody_here")).status_code in (401, 403, 404)


# ---------- meaning search (local embedder, no language model) ----------

class ConceptEmbedder:
    """Stands in for nomic-embed-text: words that mean the same thing share a slot."""
    name, model = "fake", "concepts"
    GROUPS = [("short", "light", "under", "missing"), ("delivery", "shipment", "came", "truck"), ("fish",),
              ("cooler", "walk-in", "warm", "fridge", "43f"), ("oil", "fryer")]

    def __init__(self):
        self.calls = 0

    def embed(self, texts, purpose="document"):
        self.calls += len(texts)
        out = []
        for t in texts:
            ws = t.lower().replace(",", " ").replace(".", " ").split()
            v = [float(sum(any(w.startswith(g) for g in grp) for w in ws)) for grp in self.GROUPS] + [0.01]
            out.append(v)
        return out


def test_meaning_search_finds_other_words_for_the_same_thing(c, monkeypatch):
    emb = ConceptEmbedder()
    monkeypatch.setattr(appmod, "_chat_embedder", lambda: emb)
    r = find(c, "owner_alex", "truck missing")
    assert r["how"] == "words+meaning"
    assert any("Fish delivery came short" in h["text"] and h["match"] == "meaning" for h in r["hits"])
    # meaning never widens access: Marco's private assistant chat about the warm walk-in stays his
    r = find(c, "owner_alex", "fridge too warm")
    assert not any(h["source"] == "assistant" for h in r["hits"])
    assert any("Walk-in cooler" in h["text"] for h in r["hits"])
    # each message is embedded once, then cached
    before = emb.calls
    find(c, "owner_alex", "truck missing")
    assert emb.calls - before == 1                                     # only the query


def test_word_matches_still_work_when_the_embedder_is_down(c, monkeypatch):
    class Down:
        name, model = "ollama", "nomic-embed-text"

        def embed(self, texts, purpose="document"):
            raise ConnectionError("Ollama isn't answering")
    monkeypatch.setattr(appmod, "_chat_embedder", lambda: Down())
    r = find(c, "chef_marco", "fryer oil")
    assert r["how"] == "words" and len(r["hits"]) == 2 and "word matches only" in r["note"]


def test_search_never_calls_the_assistant_model(c, monkeypatch):
    def boom(m):
        raise AssertionError("history search must not start a language model")
    monkeypatch.setattr(appmod, "_new_agent", boom)
    assert find(c, "chef_marco", "fryer")["hits"]


def test_parse_query_months():
    from datetime import date
    today = date(2026, 11, 28)
    assert chatlog.parse_query("fish in december", today)[1]["month"] == "2025-12"    # last December
    assert chatlog.parse_query("oct 2026 cooler", today) == (["cooler"], {"month": "2026-10", "from": None, "in": None})
    assert chatlog.parse_query("in:direct tongs", today)[1]["in"] == "dm"

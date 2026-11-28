"""The web app's Assistant: the agent on the local model, reached through the UI server.

A scripted fake model stands in for Ollama, so these run offline.
"""
import json
import sys

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT

sys.path.insert(0, str(ROOT))

from server import app as appmod  # noqa: E402
from agent import config as config_mod  # noqa: E402
from agent.backends import BackendUnavailable  # noqa: E402
from agent.runner import Agent  # noqa: E402


class Fake:
    """Runs a script of tool calls, then replies. Notes whether the server lock was held while it 'thought'."""
    model = "fake-local"

    def __init__(self, steps=(), reply="Here you go.", fail=None):
        self.steps, self.reply, self.fail = list(steps), reply, fail
        self.results, self.lock_held_while_thinking, self.tool_names = [], None, []

    def turn(self, system, history, user_text, tools, execute):
        self.lock_held_while_thinking = appmod.LOCK.locked()
        self.tool_names = [t["name"] for t in tools]
        self.user_text, self.history = user_text, history
        if self.fail:
            raise BackendUnavailable(self.fail)
        for name, args in self.steps:
            self.results.append(execute(name, args))
        return self.reply


@pytest.fixture
def c(monkeypatch):
    appmod.reset()
    fakes = {}

    def new_agent(m):
        cfg = config_mod.load("/nonexistent", {"mode": "local"})
        fake = fakes.setdefault(m["id"], Fake())
        return Agent(appmod.db(), cfg, m["id"], backends={"local": fake}, clock=appmod.now_iso, lock=appmod.LOCK)

    monkeypatch.setattr(appmod, "_new_agent", new_agent)
    client = TestClient(appmod.app, raise_server_exceptions=False)
    client.fakes = fakes
    return client


def H(m):
    return {"X-Member": m}


def ask(c, m, text):
    return c.post("/api/assistant", json={"text": text}, headers=H(m))


def test_everyone_gets_the_assistant_in_the_menu(c):
    for m in ("fry_rosa", "chef_marco", "owner_alex"):
        assert "assistant" in [x["id"] for x in c.get("/api/me", headers=H(m)).json()["menu"]]


def test_ask_and_history(c):
    r = ask(c, "chef_marco", "What's open tonight?")
    assert r.status_code == 200
    assert r.json() == {"role": "assistant", "text": "Here you go.", "backend": "local"}
    h = c.get("/api/assistant", headers=H("chef_marco")).json()
    assert [m["role"] for m in h["messages"]] == ["user", "assistant"]
    assert h["messages"][0]["text"] == "What's open tonight?"
    # the agent knows who is asking and at which level
    assert "chef_marco" in c.fakes["chef_marco"].user_text and "level 2" in c.fakes["chef_marco"].user_text


def test_model_time_does_not_hold_the_app_lock_but_tool_calls_do(c):
    c.fakes["chef_marco"] = Fake(steps=[("list_tickets", {})])
    assert ask(c, "chef_marco", "tickets?").status_code == 200
    f = c.fakes["chef_marco"]
    assert f.lock_held_while_thinking is False
    assert "error" not in f.results[0] and "refused" not in f.results[0]
    assert appmod.LOCK.locked() is False


def test_tools_keep_the_level_rules_on_the_shared_database(c):
    c.fakes["fry_rosa"] = Fake(steps=[("query_db", {"sql": "SELECT * FROM staff"}),
                                      ("query_db", {"sql": "SELECT COUNT(*) FROM tickets"})])
    assert ask(c, "fry_rosa", "how much does everyone make?").status_code == 200
    staff, tickets = c.fakes["fry_rosa"].results
    assert "refused" in staff
    assert "rows" in tickets
    # the web app still works after the agent used the connection (authorizer was taken off)
    assert c.get("/api/team", headers=H("chef_marco")).status_code == 200


def test_no_approve_pay_or_send_tools_are_offered(c):
    ask(c, "owner_alex", "hi")
    names = c.fakes["owner_alex"].tool_names
    assert names
    assert not [n for n in names if any(w in n for w in ("approve", "pay_", "send", "post_", "qb_write"))]


def test_each_person_has_their_own_conversation(c):
    ask(c, "chef_marco", "secret question")
    assert c.get("/api/assistant", headers=H("fry_rosa")).json()["messages"] == []
    ask(c, "fry_rosa", "my shift?")
    assert c.fakes["fry_rosa"].history == []           # rosa's model never sees marco's turns


def test_cloud_prefix_is_refused(c):
    r = ask(c, "owner_alex", "/cloud plan next week")
    assert r.status_code == 400 and "local model" in r.json()["detail"]


def test_bad_input_and_unknown_member(c):
    assert ask(c, "owner_alex", "   ").status_code == 400
    assert ask(c, "owner_alex", "x" * 4001).status_code == 400
    assert ask(c, "nobody_here", "hi").status_code == 401


def test_model_down_gives_503_and_keeps_nothing(c):
    c.fakes["chef_marco"] = Fake(fail="Ollama isn't answering")
    r = ask(c, "chef_marco", "hello?")
    assert r.status_code == 503 and "local model" in r.json()["detail"]
    assert c.get("/api/assistant", headers=H("chef_marco")).json()["messages"] == []
    # and it isn't stuck as busy
    c.fakes["chef_marco"].fail = None
    assert ask(c, "chef_marco", "hello?").status_code == 200


def test_blank_reply_is_an_error_not_a_turn(c):
    c.fakes["chef_marco"] = Fake(reply="")
    r = ask(c, "chef_marco", "hello?")
    assert r.status_code == 502
    assert c.get("/api/assistant", headers=H("chef_marco")).json()["messages"] == []


def test_clear_and_reset_drop_conversations(c):
    ask(c, "chef_marco", "one")
    assert c.post("/api/assistant/clear", headers=H("chef_marco")).status_code == 200
    assert c.get("/api/assistant", headers=H("chef_marco")).json()["messages"] == []
    ask(c, "chef_marco", "two")
    appmod.reset()
    assert appmod.CHATS == {}


def test_every_turn_is_logged_without_content(c):
    ask(c, "chef_marco", "a very particular question")
    rows = appmod.db().execute("SELECT * FROM agent_log").fetchall()
    assert len(rows) == 1 and rows[0]["backend"] == "local" and rows[0]["requester"] == "chef_marco"
    assert "particular" not in json.dumps([dict(r) for r in rows])


def test_real_agent_is_local_only(monkeypatch, tmp_path):
    """The real constructor: only a local backend exists, whatever the config file says."""
    cfgfile = tmp_path / "c.toml"
    cfgfile.write_text('mode = "cloud"\n', encoding="utf-8")
    monkeypatch.setenv("HUMAN_AGENT_CONFIG", str(cfgfile))
    appmod.reset()
    with appmod.LOCK:
        m = appmod.member("owner_alex")
        ag = appmod._new_agent(m)
    assert set(ag.backends) == {"local"}
    assert ag.cfg["mode"] == "local"


# ---------- employees who need a person are referred to customer service ----------

def test_prompt_and_tool_refer_employees_to_customer_service():
    from pathlib import Path
    core = (Path(__file__).resolve().parents[1] / "agent" / "prompts" / "core.md").read_text(encoding="utf-8")
    assert "refer them to customer service" in core and 'route_to "customer_service"' in core


def test_employee_support_request_lands_in_customer_service(c):
    c.fakes["fry_rosa"] = Fake(steps=[("request_live_person", {"channel": "callback", "route_to": "customer_service",
                                                               "reason": "question about overtime on my check"})],
                               reply="Customer service has your request and will call you back.")
    assert ask(c, "fry_rosa", "I need to talk to someone about my pay").status_code == 200
    mine = c.get("/api/cs/support", headers=H("fry_rosa")).json()
    assert mine["queue"] is False and [r["route_to"] for r in mine["requests"]] == ["customer_service"]
    queue = c.get("/api/cs/support", headers=H("chef_marco")).json()
    assert queue["queue"] is True and queue["requests"][0]["requested_for"] == "fry_rosa"
    assert c.get("/api/cs/support", headers=H("stocker_tim")).json()["requests"] == []   # not someone else's
    assert "cs" in [x["id"] for x in c.get("/api/me", headers=H("fry_rosa")).json()["menu"]]

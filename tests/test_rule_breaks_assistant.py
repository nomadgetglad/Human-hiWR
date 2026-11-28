"""Trying to break the AI rules through the Assistant (agent/), and the open items from the Nov 27 security watch.

The agent/ folder and the Assistant screen live on the claude/web-agent-chat branch, not on main yet, so this
whole file is skipped on main and runs once that branch is merged. Same convention as test_rule_breaks.py:
a passing test is an attack the code stops; an xfail (strict) test is an attack that works today.
"""
import json

import pytest

from conftest import ROOT

pytest.importorskip("agent.tools", reason="agent/ is on the claude/web-agent-chat branch, not merged yet")

from fastapi.testclient import TestClient  # noqa: E402

from agent import config as config_mod  # noqa: E402
from agent.runner import Agent  # noqa: E402
from agent.tools import TABLE_LEVEL  # noqa: E402
from hiwr_contacts import approve  # noqa: E402
from server import app as appmod  # noqa: E402


def bug(reason):
    return pytest.mark.xfail(strict=True, reason="BUG: " + reason)


class Silent:
    model = "fake-local"

    def turn(self, system, history, user_text, tools, execute):
        self.system, self.user_text = system, user_text
        return "ok"


def tools_for(member):
    """The Assistant's tools exactly as the web app builds them for this person, on the test restaurant."""
    appmod.reset()
    with appmod.LOCK:
        d = appmod.db()
        d.execute("INSERT INTO team_messages(sender, channel, text) VALUES ('fry_rosa', 'dm:fry_rosa|stocker_tim', "
                  "'my rent is late, can you cover me Friday')")
        d.execute("INSERT INTO tax_documents(member, kind, year, posted_by, data) VALUES "
                  "('fry_rosa', 'W-2', 2025, 'accounting_sue', ?)", (json.dumps({"box1_wages": 3145000}),))
        d.commit()
        cfg = config_mod.load("/nonexistent", {"mode": "local"})
        return Agent(d, cfg, member, backends={"local": Silent()}, clock=appmod.now_iso, lock=appmod.LOCK).tools


def q(tools, sql):
    return tools.call("query_db", {"sql": sql})


# ---------- the Assistant's database tool (query_db) ----------

@bug("HIGH (security watch #1). query_db gives every table that isn't listed in TABLE_LEVEL to Managers (level 2). "
     "tax_documents (W-2 wages) isn't listed, so a chef asks the Assistant and reads everyone's W-2. The W-2 "
     "screen itself shows each form only to its employee and Finance. Fix: list tax_documents at Finance (3) or "
     "owner-only, and make unlisted tables default to the account holder (see the next test).")
def test_manager_cannot_read_w2s_through_the_assistant():
    r = q(tools_for("chef_marco"), "SELECT member, data FROM tax_documents")
    assert "refused" in r


@bug("HIGH (security watch #1). team_messages isn't listed either, so a Manager reads every private DM between "
     "two employees ('dm:fry_rosa|stocker_tim') through the Assistant. The chat screen only shows a DM to its two "
     "people. Fix: owner-only in TABLE_LEVEL, or better, never readable through query_db.")
def test_manager_cannot_read_private_dms_through_the_assistant():
    r = q(tools_for("chef_marco"), "SELECT channel, text FROM team_messages WHERE channel LIKE 'dm:%'")
    assert "refused" in r


@bug("The default for a table nobody listed is Manager (TABLE_LEVEL.get(table, 2)). Every new feature that adds a "
     "table is readable by Managers until someone remembers to list it: today that includes W-2s, DMs, ID records, "
     "authenticator records, HR requests, candidates and more. Fix: fail closed. Unlisted tables need level 4, "
     "and this test lists every table so a new one must be given a level on purpose.")
def test_every_table_has_a_level_on_purpose():
    tools = tools_for("owner_alex")
    tables = {r[0] for r in tools.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    unlisted = sorted(tables - set(TABLE_LEVEL))
    assert unlisted == [], f"{len(unlisted)} tables have no level: {unlisted}"


@pytest.mark.parametrize("sql", [
    "SELECT * FROM staff",                                                  # pay rates
    "WITH x AS (SELECT * FROM money_actions) SELECT * FROM x",              # hidden in a CTE
    "SELECT name FROM recipes UNION SELECT CAST(hourly_cents AS TEXT) FROM staff",   # smuggled in a UNION
    "SELECT (SELECT hourly_cents FROM staff LIMIT 1) AS x FROM recipes",    # smuggled in a subquery
    "SELECT * FROM tax_documents",
    "SELECT * FROM team_messages",
])
def test_staff_level_cannot_reach_sensitive_tables_by_sql_tricks(sql):
    assert "refused" in q(tools_for("fry_rosa"), sql)


@pytest.mark.parametrize("sql", [
    "UPDATE money_actions SET amount_cents=1",
    "DELETE FROM ticket_events",
    "INSERT INTO approvals(money_action_id, approver, decision) VALUES (1, 'x', 'approve')",
    "ATTACH DATABASE 'x.db' AS x",
    "PRAGMA writable_schema=1",
    "SELECT 1; DELETE FROM ticket_events",
    "SELECT load_extension('evil')",
])
def test_assistant_database_tool_never_writes(sql):
    r = q(tools_for("owner_alex"), sql)
    assert "refused" in r or "error" in r


# ---------- money through the Assistant ----------

def test_assistant_money_proposal_is_pending_and_requester_cannot_approve():
    t = tools_for("manager_jo")
    r = t.call("propose_money_action", {"kind": "refund", "description": "Refund table 4", "amount": 30})
    assert r["status"].startswith("PENDING")
    mid = int(r["ref"].split()[-1])
    with pytest.raises(PermissionError):
        approve(t.db, mid, "manager_jo")


CRASH = bug("An amount of 'inf' or '1e300' from the model isn't refused: it reaches the database and raises "
            "OverflowError, which the toolbox doesn't catch, so the Assistant's turn crashes (a 500 in the app) "
            "instead of saying no. There's also no ceiling: $1e15 is filed as a normal PENDING refund. Fix: in "
            "propose_money_action, refuse amounts that aren't finite or are above a ceiling the owner sets.")


@pytest.mark.parametrize("amount", [-5, "-0.5", "nan", pytest.param("inf", marks=CRASH),
                                    pytest.param("1e300", marks=CRASH)])
def test_assistant_money_amount_tricks_are_refused_not_crashes(amount):
    t = tools_for("manager_jo")
    r = t.call("propose_money_action", {"kind": "refund", "description": "x", "amount": amount})
    assert "refused" in r or "error" in r


def test_staff_cannot_propose_money_through_the_assistant():
    r = tools_for("fry_rosa").call("propose_money_action", {"kind": "refund", "description": "x", "amount": 10})
    assert "refused" in r


# ---------- acceptable use: business only ----------

@bug("The acceptable-use policy (business use only, 11 banned uses) isn't in the Assistant's rules at all: "
     "agent/prompts/core.md never says to refuse off-purpose requests, and nothing in code checks for them. "
     "Fix: add the AUP's business-only rule and the banned list to core.md, and log refusals.")
def test_core_prompt_carries_the_business_only_rule():
    core = (ROOT / "agent" / "prompts" / "core.md").read_text().lower()
    assert "acceptable use" in core or "business use only" in core or "business-only" in core


# ---------- the server: binding, auth off, DNS rebinding ----------

@bug("HIGH (security watch #2). .claude/launch.json starts your own account ('human-ui') on --host 0.0.0.0, which "
     "listens on every network the PC is on (home Wi-Fi, a coffee shop, Tailscale). With HUMAN_AUTH=off that means "
     "anyone on the same network is the account holder. Fix: 127.0.0.1 here, and reach it from the phone through "
     "Tailscale serve, which forwards to localhost.")
def test_launch_config_does_not_listen_on_every_network():
    f = ROOT / ".claude" / "launch.json"
    if not f.exists():
        pytest.skip(".claude/launch.json is only on Alexandre's PC")
    for c in json.loads(f.read_text())["configurations"]:
        args = c.get("runtimeArgs", [])
        if "--host" in args:
            assert args[args.index("--host") + 1] in ("127.0.0.1", "localhost"), c["name"]


@bug("HIGH (security watch #3). With HUMAN_AUTH=off the server answers any Host name, so a web page the owner "
     "opens can point its own domain at 127.0.0.1 (DNS rebinding) and read the account through the browser. "
     "Fix: when sign-in is off, refuse any Host that isn't localhost or 127.0.0.1 (Starlette's "
     "TrustedHostMiddleware does this).")
def test_auth_off_refuses_a_foreign_host_name():
    import os
    os.environ["HUMAN_AUTH"] = "off"
    try:
        appmod.reset(sample=False)
        c = TestClient(appmod.app, raise_server_exceptions=False)
        r = c.get("/api/me", headers={"X-Member": "owner_alex", "Host": "attacker.example"})
        assert r.status_code in (400, 403, 421)
    finally:
        del os.environ["HUMAN_AUTH"]
        appmod.reset()


@bug("With HUMAN_AUTH=off, anyone who can reach the port picks 'owner_alex' in the X-Member header and is the "
     "account holder, whatever address they come from. The comment says 'this PC only', but nothing checks it. "
     "Fix: when sign-in is off, answer only requests from 127.0.0.1 / ::1.")
def test_auth_off_answers_only_this_computer():
    import os
    os.environ["HUMAN_AUTH"] = "off"
    try:
        appmod.reset(sample=False)
        c = TestClient(appmod.app, raise_server_exceptions=False)     # TestClient's address is not loopback
        r = c.get("/api/me", headers={"X-Member": "owner_alex"})
        assert r.status_code in (401, 403)
    finally:
        del os.environ["HUMAN_AUTH"]
        appmod.reset()
